"""Offline material collection and explicit, cancellable audit. No WeChat imports."""

import multiprocessing
from dataclasses import dataclass, field
from pathlib import Path

from shipment_bot.auto_review import Recognized, self_check
from shipment_bot.extract import ExtractionError, Extractor, validate_image
from shipment_bot.review import compare, norm

DISCLAIMER = (
    "本结果不代表批准发货，仍须人工核对原图、最终订单和实物。"
    "单单自查不验证成交价；本软件不验证实际库存或实物。"
)


@dataclass
class Material:
    text: str = ""
    images: list[str] = field(default_factory=list)
    role: str = "unknown"


def audit(materials, extractor, cancelled=lambda: False):
    def checkpoint():
        if cancelled():
            raise InterruptedError("审核已取消")

    if not materials or len(materials) > 8:
        raise ExtractionError("请添加 1～8 份材料。每份可混合文字和图片。")
    items, sources = [], []
    for index, material in enumerate(materials, 1):
        checkpoint()
        if material.role not in {"unknown", "order", "delivery"}:
            raise ExtractionError("材料类型无效。")
        if len(material.images) > 4:
            raise ExtractionError("每份材料最多 4 张图片。")
        parts, warnings = [], []
        if material.text.strip():
            parts.append(material.text.strip())
        for raw in material.images:
            checkpoint()
            path = Path(raw)
            try:
                path = validate_image(path, [path.parent])
            except (OSError, ValueError):
                raise ExtractionError("图片无法读取，请重新添加。") from None
            text, notes = extractor.read_image(path)
            parts.append(text)
            warnings.extend(notes)
        checkpoint()
        text = "\n\n".join(parts)
        if not text.strip():
            raise ExtractionError(f"材料 {index} 没有可用文字或图片。")
        if len(text) > 20000:
            raise ExtractionError(f"材料 {index} 超过 20000 字，请拆分。")
        item = extractor._recognize(text, warnings, "text")
        checkpoint()
        # Explicit user role is optional; never derive an association from a role alone.
        if material.role != "unknown":
            item.kind = material.role
        item.image_index = index
        items.append(item)
        sources.append(text)
    checkpoint()
    return render_report(items, sources)


def render_report(items: list[Recognized], sources: list[str]):
    rows = ["杭州嘉惠食品有限公司 · 发货前辅助审核", DISCLAIMER]
    for index, (item, source) in enumerate(zip(items, sources, strict=True), 1):
        doc = item.document
        label = {"unknown": "类型待确认", "order": "订单", "delivery": "出货单"}[item.kind]
        rows.extend([f"\n【材料 {index}：{label} · 识别字段】", doc.model_dump_json(indent=2)])
        rows.append("【自查问题及依据】")
        issues = self_check(doc, require_customer=True, require_specification=True)
        rows.extend(f"- {issue}" for issue in issues)
        if not issues:
            rows.append("已识别字段行金额与合计未发现计算差异，仍待人工确认。")
        rows.extend(["【依据：本份输入文字 / 本机 OCR 原文，行 evidence 对应上文字段】", source])
    orders = [x.document for x in items if x.kind == "order"]
    deliveries = [x.document for x in items if x.kind == "delivery"]
    if len(items) == 2 and len(orders) == len(deliveries) == 1:
        order, delivery = orders[0], deliveries[0]
        expected = order.reference_order_id or order.document_number
        if (
            expected
            and delivery.reference_order_id
            and norm(expected) == norm(delivery.reference_order_id)
            and order.customer
            and delivery.customer
            and norm(order.customer) == norm(delivery.customer)
        ):
            rows.append("\n【两单比对】依据：客户及明确关联订单号一致；最终订单版本仍需人工确认。")
            findings = compare(order, delivery).findings
            rows.extend(f"- [{x.code}] {x.message}" for x in findings)
            if not findings:
                rows.append("已识别字段未发现两单差异，仍待人工确认。")
        else:
            rows.append("未自动配对：缺少一致客户或明确关联订单号，仅分别自查，请人工确认。")
    elif len(items) > 1:
        rows.append("未自动配对：材料类型不明或多份材料，未跨单拼接，请人工确认。")
    else:
        rows.append("仅一份材料：未核对成交订单与实际出货之间的差异。")
    rows.append(DISCLAIMER)
    return "\n".join(rows)


def worker(connection, materials, settings):
    try:
        extractor = Extractor(**settings)
        result = audit(materials, extractor)
        connection.send(("ok", result))
    except ExtractionError as error:
        # Our extraction errors are bounded and contain neither headers nor provider bodies.
        message = str(error).replace(settings.get("api_key", "") or "\0", "[Key 已隐藏]")
        connection.send(("error", message))
    except Exception:
        connection.send(("error", "审核未完成：图片、OCR 或运行环境异常，请检查材料后重试。"))
    finally:
        connection.close()


class AuditTask:
    """Owned spawned worker; key travels through IPC, never command arguments or files."""

    def __init__(self):
        self.process = None
        self.connection = None

    @property
    def busy(self):
        return self.process is not None

    def start(self, materials, settings):
        if self.busy:
            raise ValueError("请先停止当前审核。")
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=worker, args=(child, materials, settings), daemon=True)
        try:
            process.start()
        except Exception:
            parent.close()
            raise
        finally:
            child.close()
        self.connection, self.process = parent, process

    def poll(self):
        if not self.busy:
            return None
        if self.connection.poll():
            try:
                result = self.connection.recv()
            except EOFError:
                result = ("error", "审核进程意外结束，未产生有效结果。")
            self.stop()
            return result
        if not self.process.is_alive():
            self.stop()
            return "error", "审核进程意外结束，未产生有效结果。"
        return None

    def stop(self):
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=0.5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=0.5)
            self.process.close()
            self.connection.close()
        self.process = self.connection = None
