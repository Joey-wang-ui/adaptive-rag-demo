"""Single-image arithmetic review and conservative document pairing."""

import re
from collections import Counter
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel

from shipment_bot.models import Document
from shipment_bot.review import compare, money, norm, unit


class Recognized(BaseModel):
    document: Document
    kind: Literal["order", "delivery", "unknown"] = "unknown"
    title: str = ""
    image_index: int = 1
    source_type: Literal["image", "text"] = "image"


def classify(text):
    delivery, order = [], []
    for line in text.splitlines():
        if len(line.strip()) > 60:
            continue
        if re.search(r"(?:出货单|送货单|发货单|销售出库单|销货单)(?!号|编号)", line):
            delivery.append(line.strip())
        if re.search(r"(?:订货单|订购单|采购订单|销售订单|订单)(?!号|编号)", line):
            order.append(line.strip())
    if bool(delivery) == bool(order):
        return "unknown", ""
    return ("delivery", delivery[0]) if delivery else ("order", order[0])


def _check_document(doc, require_customer=False, require_specification=False):
    issues, calculations, amounts = list(doc.uncertainties), [], []
    if require_customer and not doc.customer:
        issues.append("未识别到客户名称。")
    keys = [(norm(x.name), norm(x.specification)) for x in doc.lines]
    if any(count > 1 for count in Counter(keys).values()):
        issues.append("存在同名同规格重复行，请核实是否重复开单；未自动合并。")
    for i, row in enumerate(doc.lines, 1):
        label = f"第{i}行「{row.name or '名称不清'}」"
        if not row.name:
            issues.append(label + "商品名称无法辨认。")
        if require_specification and not row.specification:
            issues.append(label + "规格缺失。")
        expected = None
        if row.quantity is None or row.unit_price is None:
            issues.append(label + "数量或单价缺失，无法核算该行乘积。")
        else:
            if row.quantity <= 0 or row.unit_price == 0:
                issues.append(label + "数量非正数或单价为零，请核实赠品、退货等情况。")
            if not row.unit or not row.price_unit:
                issues.append(label + "计价单位不明确；不默认按箱、件或公斤相乘。")
            elif unit(row.unit) != unit(row.price_unit):
                issues.append(
                    label + f"数量单位{row.unit}与计价单位{row.price_unit}不同，需确认换算。"
                )
            else:
                expected = money(row.quantity * row.unit_price)
                if row.amount is not None and expected != row.amount:
                    issues.append(label + f"金额不符：数量×单价={expected}，单据为{row.amount}。")
        if row.amount is None:
            if expected is not None:
                calculations.append(
                    label + f"原文未写行金额；按{row.quantity}{row.unit}×"
                    f"{row.unit_price}元/{row.price_unit}计算为{expected}元（计算值）。"
                )
            else:
                issues.append(label + "行金额未填写，且现有数量、单价或单位不足以计算。")
        amounts.append(row.amount if row.amount is not None else expected)
    if any(value is None for value in amounts):
        issues.append("部分行金额缺失且无法计算，无法完整核对总计。")
    else:
        total = sum(amounts, Decimal(0))
        if doc.total is None:
            calculations.append(
                f"已列商品金额合计{money(total)}元（计算值）；原文未写合计，无法比对。"
            )
        elif total != doc.total:
            basis = "按已列商品计算" if calculations else "逐行相加"
            issues.append(
                f"合计不符：{basis}{money(total)}，单据合计{doc.total}；折扣、运费、税费需单独说明。"
            )
        elif calculations:
            calculations.append(f"计算金额合计{money(total)}元，与填写的合计一致。")
    if doc.total is None:
        issues.append("合计金额未填写或未识别到，无法与原文总额核对。")
    return list(dict.fromkeys(issues)), calculations


def self_check(doc, require_customer=False, require_specification=False):
    return _check_document(doc, require_customer, require_specification)[0]


def summarize(
    items, errors=(), require_customer=False, require_specification=False, compare_requested=False
):
    output = ["【单据自动自查】"]
    if errors:
        output.append(
            "【本批审核未完成】有材料未识别成功；以下仅是已识别部分的自查，不能据此判断图文一致。"
        )
        output.extend("- " + error for error in errors)
    for item in items:
        index = item.image_index
        label = {"order": "订单", "delivery": "出货单", "unknown": "单据（未注明订单/出货单）"}[
            item.kind
        ]
        doc = item.document
        position = f"第{index}张" if item.source_type == "image" else f"第{index}份文字"
        output.append(
            f"{position}：{label}｜客户：{doc.customer or '未识别'}｜单号：{doc.document_number or doc.reference_order_id or '未识别'}"
        )
        issues, calculations = _check_document(doc, require_customer, require_specification)
        output.extend("- " + note for note in calculations)
        output.extend("- " + issue for issue in issues[:20])
        if len(issues) > 20:
            output.append(f"另有{len(issues)-20}项待核实，请先处理以上问题。")
        if not issues:
            output.append("已识别字段的行金额及合计未发现计算差异。")
    orders = [x.document for x in items if x.kind == "order"]
    deliveries = [x.document for x in items if x.kind == "delivery"]
    captions = [x.document for x in items if x.source_type == "text"]
    images = [x.document for x in items if x.source_type == "image"]
    if compare_requested:
        if errors or len(items) != 2 or len(captions) != 1 or len(images) != 1:
            output.append(
                "【图文比对未完成】请提供一张可读取的图片和本次要核对的文字；不能据部分材料判断一致。"
            )
        else:
            findings = compare_caption(captions[0], images[0])
            headline = (
                "发现图文差异"
                if any("不一致" in finding for finding in findings)
                else "图文尚有待确认项"
                if findings
                else "已提供字段与图片一致"
            )
            output.insert(1, "【" + headline + "】")
            output.append("【按本次请求核对图文】仅核对文字明确提供的字段，不代替业务归属确认。")
            output.extend("- " + finding for finding in findings[:20])
            if not findings:
                output.append(
                    "文字中已提供且可识别的字段与本张图片一致；未填写的字段不在本次比对范围。"
                )
    elif len(items) == 2 and not errors and len(orders) == len(deliveries) == 1:
        order, delivery = orders[0], deliveries[0]
        expected_id = order.reference_order_id or order.document_number
        if (
            expected_id
            and delivery.reference_order_id
            and norm(expected_id) == norm(delivery.reference_order_id)
            and order.customer
            and delivery.customer
            and norm(order.customer) == norm(delivery.customer)
        ):
            report = compare(order, delivery)
            output.append(
                "【两单比对】客户及明确关联订单号一致；按本次提供材料比对，订单最终版本仍需人工确认。"
            )
            output.extend("- " + x.message for x in report.findings[:20])
            if not report.findings:
                output.append("已识别字段未发现两单差异。")
        else:
            output.append(
                "未自动配对：两份材料缺少一致的客户及明确关联订单号，已分别自查，避免串单。"
            )
    elif len(items) > 1:
        output.append("多张材料已分别自查；未自动拼接跨页单据或合并不同订单。")
    output.append(
        "自查不验证成交价是否正确、是否错发漏发；本结果不代表批准发货，请核对原图和实物。"
    )
    return "\n".join(output)


def compare_caption(caption, image):
    """Compare only caption-provided fields; no order ID or role invention."""
    findings = []
    for field, label in [("customer", "客户"), ("total", "合计")]:
        expected, actual = getattr(caption, field), getattr(image, field)
        if expected is None:
            continue
        if actual is None:
            findings.append(f"图片{label}未识别，无法确认文字中的{label}。")
        elif norm(expected) != norm(actual) if field == "customer" else expected != actual:
            findings.append(f"{label}不一致：文字为{expected}，图片为{actual}。")
    matched = set()
    for row in caption.lines:
        candidates = [
            (i, other)
            for i, other in enumerate(image.lines)
            if row.name
            and norm(row.name) == norm(other.name)
            and (not row.specification or norm(row.specification) == norm(other.specification))
        ]
        if len(candidates) != 1 or candidates[0][0] in matched:
            findings.append(
                f"商品「{row.name or '名称未识别'}」在图片中未找到唯一对应行，请核对商品名称和规格。"
            )
            continue
        index, other = candidates[0]
        matched.add(index)
        for field, label in [
            ("quantity", "数量"),
            ("unit", "数量单位"),
            ("unit_price", "单价"),
            ("price_unit", "计价单位"),
            ("amount", "行金额"),
        ]:
            expected, actual = getattr(row, field), getattr(other, field)
            if expected is None:
                continue
            if actual is None:
                findings.append(f"「{row.name}」图片{label}未识别，无法核对。")
            elif (
                unit(expected) != unit(actual)
                if field in {"unit", "price_unit"}
                else expected != actual
            ):
                findings.append(f"「{row.name}」{label}不一致：文字为{expected}，图片为{actual}。")
    if len(matched) < len(image.lines):
        findings.append("图片还有未能与文字唯一对应的商品行，本次不能确认整张单据一致。")
    findings.extend("图片待确认：" + note for note in image.uncertainties)
    findings.extend("文字待确认：" + note for note in caption.uncertainties)
    return list(dict.fromkeys(findings))
