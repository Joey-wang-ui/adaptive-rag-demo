"""Conservative, deterministic comparisons. LLMs never decide release."""

import re
import unicodedata
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from shipment_bot.models import Document, Finding, Review

CENT = Decimal("0.01")


def norm(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"\s+", "", value)


def unit(value: str | None) -> str:
    value = norm(value)
    return {"公斤": "kg", "千克": "kg", "克": "g"}.get(value, value)


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def compare(order: Document, delivery: Document) -> Review:
    findings: list[Finding] = []

    def add(code: str, text: str, difference: bool = False) -> None:
        findings.append(
            Finding(level="difference" if difference else "uncertain", code=code, message=text)
        )

    for label, doc in (("确认订单", order), ("出货单", delivery)):
        for note in doc.uncertainties:
            add("recognition_uncertain", f"{label}：{note}")
        if not doc.customer:
            add("missing_customer", f"{label}未识别到客户名称，请核对是否为同一客户。")
        keys = [(norm(x.name), norm(x.specification)) for x in doc.lines]
        duplicates = {k for k, count in Counter(keys).items() if count > 1}
        if duplicates:
            add("duplicate_lines", f"{label}存在同名同规格的重复行，需人工核对，暂不自动合并。")
        for index, item in enumerate(doc.lines, 1):
            title = f"{label}第 {index} 行「{item.name or '未识别商品'}」"
            if not item.name or not item.specification:
                add("missing_identity", f"{title}商品名称或规格缺失。")
            if item.quantity is None or item.quantity <= 0:
                add("missing_quantity", f"{title}数量缺失或不是正数。")
            if not item.unit:
                add("missing_unit", f"{title}单位缺失，不能默认按箱或按件。")
            if item.unit_price is None or item.unit_price == 0:
                add("missing_price", f"{title}成交单价缺失或为零，请确认。")
            if not item.price_unit:
                add("missing_price_unit", f"{title}未明确单价按箱、件还是公斤计价，请确认。")
            elif item.unit and unit(item.price_unit) != unit(item.unit):
                add(
                    "pricing_basis",
                    f"{title}数量单位为 {item.unit}、计价单位为 {item.price_unit}，需人工换算核对。",
                )
            if (
                item.quantity is not None
                and item.unit_price is not None
                and item.unit
                and item.price_unit
                and unit(item.unit) == unit(item.price_unit)
            ):
                calculated = money(item.quantity * item.unit_price)
                if item.amount is not None and item.amount != calculated:
                    add(
                        "line_amount",
                        f"{title}金额不符：数量×单价应为 {calculated}，单据为 {item.amount}。",
                        True,
                    )
            if label == "出货单" and item.amount is None:
                add("missing_amount", f"{title}行金额未识别到。")
        if doc.total is not None:
            if all(x.amount is not None for x in doc.lines):
                subtotal = sum((x.amount or Decimal(0) for x in doc.lines), Decimal(0))
                if doc.total != subtotal:
                    add(
                        "document_total",
                        f"{label}合计不符：逐行相加为 {money(subtotal)}，单据合计为 {doc.total}。"
                        "折扣、运费、税费需人工说明。",
                        True,
                    )
            else:
                add("incomplete_total", f"{label}部分行金额缺失，无法验证总计。")
        elif label == "出货单":
            add("missing_total", "出货单合计金额未识别到。")

    if order.customer and delivery.customer and norm(order.customer) != norm(delivery.customer):
        add(
            "customer", f"客户不同：订单「{order.customer}」，出货单「{delivery.customer}」。", True
        )
    if (
        order.reference_order_id
        and delivery.reference_order_id
        and norm(order.reference_order_id) != norm(delivery.reference_order_id)
    ):
        add("reference_order", "两份单据明确标注的关联订单号不同，请核对。", True)

    used: set[int] = set()
    for expected in order.lines:
        if not expected.name:
            continue
        exact = [
            i
            for i, actual in enumerate(delivery.lines)
            if i not in used
            and norm(expected.name) == norm(actual.name)
            and norm(expected.specification) == norm(actual.specification)
        ]
        if len(exact) == 1:
            index = exact[0]
        elif len(exact) > 1:
            add("ambiguous_match", f"「{expected.name}」匹配到多条出货记录，请人工确认。")
            continue
        else:
            # A matching SKU can identify a renamed item, but is not permission to ignore differences.
            by_sku = [
                i
                for i, actual in enumerate(delivery.lines)
                if i not in used and expected.sku and norm(expected.sku) == norm(actual.sku)
            ]
            if len(by_sku) == 1:
                index = by_sku[0]
                add("identity", f"编码 {expected.sku} 对应的商品名称或规格不一致。", True)
            else:
                add(
                    "missing_item",
                    f"订单商品「{expected.name} {expected.specification or ''}」未找到唯一对应出货行。",
                    True,
                )
                continue
        used.add(index)
        actual = delivery.lines[index]
        label = f"「{expected.name}」"
        if expected.sku and actual.sku and norm(expected.sku) != norm(actual.sku):
            add("sku", f"{label}商品编码不符：{expected.sku} / {actual.sku}。", True)
        if expected.unit and actual.unit and unit(expected.unit) != unit(actual.unit):
            add(
                "unit",
                f"{label}单位不同：订单 {expected.unit}，出货单 {actual.unit}；未做箱/件/公斤换算。",
                True,
            )
        if expected.quantity is not None and actual.quantity is not None:
            if expected.quantity != actual.quantity:
                add(
                    "quantity",
                    f"{label}数量不同：订单 {expected.quantity} {expected.unit or ''}，出货单 {actual.quantity} {actual.unit or ''}。",
                    True,
                )
        if expected.unit_price is not None and actual.unit_price is not None:
            if expected.unit_price != actual.unit_price:
                add(
                    "price",
                    f"{label}单价不同：本单成交价 {expected.unit_price}，出货单 {actual.unit_price}。",
                    True,
                )
        if (
            expected.price_unit
            and actual.price_unit
            and unit(expected.price_unit) != unit(actual.price_unit)
        ):
            add(
                "price_unit",
                f"{label}单价计价单位不同：订单每{expected.price_unit}，出货单每{actual.price_unit}。",
                True,
            )
    for index, actual in enumerate(delivery.lines):
        if index not in used:
            add(
                "extra_item",
                f"出货单「{actual.name} {actual.specification or ''}」未找到唯一对应订单行。",
                True,
            )

    status: Literal["differences", "needs_confirmation", "no_differences"] = "no_differences"
    if findings:
        status = (
            "differences"
            if any(x.level == "difference" for x in findings)
            else "needs_confirmation"
        )
    return Review(status=status, findings=findings)


def render(case_id: str, revision: int, review: Review) -> str:
    title = {
        "differences": "发现差异，待修改或人工核实",
        "needs_confirmation": "资料或识别存在疑点，待人工确认",
        "no_differences": "已识别字段比对无差异，仍待人工确认",
    }[review.status]
    rows = [f"【发货前复核】{case_id} · v{revision}", title]
    for i, item in enumerate(review.findings[:20], 1):
        rows.append(f"{i}. {item.message}")
    if len(review.findings) > 20:
        rows.append(f"另有 {len(review.findings) - 20} 项，请先修正以上问题再审核。")
    rows.append("本结果不代表已批准发货；还需核对原图和实物。")
    if review.status == "no_differences":
        rows.append(f"确认原图和实物后，可发送：确认 {case_id} v{revision}")
    else:
        rows.append("请重发修正后的订单或出货单；本版不允许带差异自动放行。")
    return "\n".join(rows)
