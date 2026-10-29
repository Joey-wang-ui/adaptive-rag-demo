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


def self_check(doc, require_customer=False, require_specification=False):
    issues = list(doc.uncertainties)
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
        if row.quantity is None or row.unit_price is None or row.amount is None:
            issues.append(label + "数量、单价或行金额不完整，无法核算。")
            continue
        if row.quantity <= 0 or row.unit_price == 0:
            issues.append(label + "数量非正数或单价为零，请核实赠品、退货等情况。")
        if not row.unit or not row.price_unit:
            issues.append(label + "计价单位不明确；不默认按箱、件或公斤相乘。")
        elif unit(row.unit) != unit(row.price_unit):
            issues.append(label + f"数量单位{row.unit}与计价单位{row.price_unit}不同，需确认换算。")
        elif money(row.quantity * row.unit_price) != row.amount:
            issues.append(
                label
                + f"金额不符：数量×单价={money(row.quantity * row.unit_price)}，单据为{row.amount}。"
            )
    if doc.total is None:
        issues.append("合计金额未识别到，无法核对总计。")
    elif any(row.amount is None for row in doc.lines):
        issues.append("部分行金额缺失，无法核对总计。")
    else:
        total = sum((row.amount or Decimal(0) for row in doc.lines), Decimal(0))
        if total != doc.total:
            issues.append(
                f"合计不符：逐行相加{money(total)}，单据合计{doc.total}；折扣、运费、税费需单独说明。"
            )
    return list(dict.fromkeys(issues))


def summarize(items, errors=(), require_customer=False, require_specification=False):
    output = ["【单据自动自查】"]
    for item in items:
        index = item.image_index
        label = {"order": "订单", "delivery": "出货单", "unknown": "类型待确认的单据"}[item.kind]
        doc = item.document
        output.append(
            f"第{index}张：{label}｜客户：{doc.customer or '未识别'}｜单号：{doc.document_number or doc.reference_order_id or '未识别'}"
        )
        issues = self_check(doc, require_customer, require_specification)
        output.extend("- " + issue for issue in issues[:20])
        if len(issues) > 20:
            output.append(f"另有{len(issues)-20}项待核实，请先处理以上问题。")
        if not issues:
            output.append("已识别字段的行金额及合计未发现计算差异。")
    output.extend("- " + error for error in errors)
    orders = [x.document for x in items if x.kind == "order"]
    deliveries = [x.document for x in items if x.kind == "delivery"]
    if len(items) == 2 and not errors and len(orders) == len(deliveries) == 1:
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
