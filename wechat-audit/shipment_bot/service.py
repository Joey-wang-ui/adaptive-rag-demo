"""A bounded chat workflow, not a free-form agent with computer permissions."""

import re
from pathlib import Path

from shipment_bot.extract import ExtractionError, Extractor, validate_image
from shipment_bot.models import Document, Incoming
from shipment_bot.review import compare, render
from shipment_bot.store import Store, scope

HELP = """嘉惠发货前审单助手（试运行）
按顺序发送：
1. 新建 A001
2. 订单
3. 一张客户最终确认订单的完整图片
4. 出货单
5. 一张开好的出货单完整图片
收齐后自动返回差异；也可发送“审核”重看。
可用文字代替图片，例如：
订单 客户：测试烘焙店；蛋液，规格10kg，数量10箱，成交单价85元/箱。
查看订单 / 查看出货单：显示已识别字段。
确认 A001 v2：仅对无差异的指定版本记录人工确认。
重发“订单”或“出货单”再发新图，会撤销旧确认并重新审核。
第一版每份单据限一张完整图片；不支持直接读客户群或核对实际装货。
图片 OCR 在本机运行，识别出的文字会发送到你配置的 DeepSeek API。
"""


def describe(label: str, document: Document) -> str:
    rows = [f"【{label}识别结果】客户：{document.customer or '未识别'}"]
    for i, line in enumerate(document.lines[:30], 1):
        rows.append(
            f"{i}. {line.name or '名称不清'} | {line.specification or '规格缺失'} | "
            f"{line.quantity if line.quantity is not None else '?'}{line.unit or '?'} | "
            f"单价 {line.unit_price if line.unit_price is not None else '?'}/{line.price_unit or '计价单位待确认'} | "
            f"金额 {line.amount if line.amount is not None else '?'}"
        )
    if len(document.lines) > 30:
        rows.append(f"共 {len(document.lines)} 行，此处显示前 30 行；超出部分请拆单处理。")
    rows.append(f"合计：{document.total if document.total is not None else '未识别'}")
    if document.uncertainties:
        rows.append("待确认：" + "；".join(document.uncertainties[:5]))
    return "\n".join(rows)


class Bot:
    def __init__(self, store: Store, extractor: Extractor, media_roots: list[Path]):
        self.store = store
        self.extractor = extractor
        self.media_roots = media_roots

    def handle(self, incoming: Incoming) -> str:
        peer = scope(incoming.account_id, incoming.sender_id)
        cached = self.store.cached(peer, incoming.message_id)
        if cached is not None:
            return cached
        try:
            reply = self._handle(peer, incoming)
        except ExtractionError as error:
            reply = f"本次识别未完成：{error}\n没有给出发货许可，请修正后重新发送。"
        return self.store.cache(peer, incoming.message_id, reply)

    def _review(self, peer: str, case_id: str) -> str:
        session = self.store.session(peer)
        if session.get("pending_role"):
            return "正在等待新的订单/出货单材料，旧审核已失效。请先发送对应图片或文字。"
        case = self.store.case(peer, case_id)
        if not case.get("order_json") or not case.get("delivery_json"):
            return "还没收齐确认订单和出货单。请依次发送“订单”、图片、“出货单”、图片。"
        review = compare(
            Document.model_validate_json(case["order_json"]),
            Document.model_validate_json(case["delivery_json"]),
        )
        self.store.save_report(peer, case_id, case["revision"], review.model_dump_json())
        return render(case_id, case["revision"], review)

    def _handle(self, peer: str, incoming: Incoming) -> str:
        text = incoming.text.strip()
        if text in {"帮助", "开始", "help", "/help", "你好"} and not incoming.attachments:
            return HELP
        match = re.fullmatch(r"新建\s+([A-Za-z0-9_-]{1,40})", text)
        if match and not incoming.attachments:
            case_id = match.group(1)
            existing = self.store.case(peer, case_id)
            self.store.new_case(peer, case_id)
            return f"{'已打开' if existing else '已新建'} {case_id}。先发送“订单”，再发送一张最终确认订单图片。请勿把不同订单混在一起。"
        session = self.store.session(peer)
        case_id = session.get("case_id")
        if not case_id:
            return "请先发送“新建 A001”（A001 换成你本次的订单编号）。发送“帮助”查看用法。"
        match = re.fullmatch(r"确认\s+([A-Za-z0-9_-]{1,40})\s+v(\d+)", text)
        if match and not incoming.attachments:
            if match.group(1) != case_id or session.get("pending_role"):
                return "确认的订单不是当前订单，或正在等待替换材料，未记录确认。"
            if not self.store.approve(peer, case_id, int(match.group(2))):
                return "该版本没有有效的无差异报告，或已被新材料替换，未记录确认。请发送“审核”。"
            return f"已记录本微信操作者对 {case_id} v{match.group(2)} 的人工确认。\n请按公司要求核对实物并安排发货。本版尚未区分销售和负责人岗位权限。"
        if text == "审核" and not incoming.attachments:
            return self._review(peer, case_id)
        if text in {"查看订单", "查看出货单"} and not incoming.attachments:
            column = "order_json" if text == "查看订单" else "delivery_json"
            raw = self.store.case(peer, case_id).get(column)
            return (
                describe(text[2:], Document.model_validate_json(raw))
                if raw
                else "还未保存这份单据。"
            )

        role = session.get("pending_role")
        for prefix, value in (("出货单", "delivery"), ("订单", "order")):
            if text == prefix or text.startswith(prefix + " ") or text.startswith(prefix + "\n"):
                role = value
                self.store.pending(peer, role)
                text = text[len(prefix) :].strip()
                if not text and not incoming.attachments:
                    return f"请发送一张完整、清晰的{prefix}原图，也可以直接发送完整文字。旧审核和确认已失效。"
                break
        if not role:
            return (
                "请先发送“订单”或“出货单”，明确下一份材料的类型；不要让我猜。发送“帮助”查看步骤。"
            )
        if incoming.attachments:
            if len(incoming.attachments) != 1:
                raise ExtractionError(
                    "本版每份单据限一张完整图片，请合并为清晰单页或拆成独立订单。"
                )
            attachment = incoming.attachments[0]
            if attachment.content_type and not attachment.content_type.startswith("image/"):
                raise ExtractionError("请发送单据图片，本版不直接处理 PDF、Excel、语音或视频。")
            try:
                path = validate_image(Path(attachment.path), self.media_roots)
            except (OSError, ValueError):
                raise ExtractionError("附件已不可用，请重新从微信发送图片。") from None
            document = self.extractor.image(path)
        else:
            if not text or text in {"[图片]", "[文件]", "[语音]", "[视频]"}:
                raise ExtractionError("未收到可用的图片或文字，请发送单据原图。")
            document = self.extractor.parse(text)
        # Keep the first version bounded and make every displayed line reviewable.
        if len(document.lines) > 30:
            raise ExtractionError("本版每单最多 30 行商品，请拆单处理。")
        case = self.store.replace_document(peer, case_id, role, document.model_dump_json())
        label = "确认订单" if role == "order" else "出货单"
        result = describe(label, document)
        if case.get("order_json") and case.get("delivery_json"):
            result += "\n\n" + self._review(peer, case_id)
        else:
            next_role = "出货单" if role == "order" else "订单"
            result += f"\n已保存 {case_id} v{case['revision']}。请发送“{next_role}”，再发对应图片。"
        return result
