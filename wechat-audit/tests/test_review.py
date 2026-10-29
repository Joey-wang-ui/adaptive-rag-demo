from decimal import Decimal

import pytest
from shipment_bot.models import Document
from shipment_bot.review import compare


def doc(**changes):
    raw = {
        "customer": "测试店",
        "lines": [
            {
                "name": "蛋液",
                "sku": "E01",
                "specification": "10kg",
                "quantity": "10",
                "unit": "箱",
                "unit_price": "85",
                "price_unit": "箱",
                "amount": "850",
                "evidence": "蛋液 10kg 10箱 单价85 金额850",
            }
        ],
        "total": "850",
    }
    item = changes.pop("item", None)
    if item:
        raw["lines"][0].update(item)
    raw.update(changes)
    return Document.model_validate(raw)


def codes(result):
    return {x.code for x in result.findings}


def test_matching_documents_never_mean_automatic_approval():
    result = compare(doc(), doc())
    assert result.status == "no_differences"
    assert not hasattr(result, "approved")


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("quantity", "12", "quantity"),
        ("unit_price", "88", "price"),
        ("unit", "件", "unit"),
        ("sku", "WRONG", "sku"),
        ("specification", "20kg", "identity"),
        ("amount", "849.98", "line_amount"),
    ],
)
def test_real_discrepancies_block_confirmation(field, value, code):
    result = compare(doc(), doc(item={field: value}))
    assert result.status == "differences"
    assert code in codes(result)


def test_one_cent_difference_is_not_silently_accepted():
    result = compare(doc(), doc(item={"amount": "849.99"}, total="849.99"))
    assert "line_amount" in codes(result)


def test_decimal_calculation_has_no_binary_float_error():
    value = doc(item={"quantity": "3", "unit_price": "0.10", "amount": "0.30"}, total="0.30")
    assert compare(value, value).status == "no_differences"


def test_missing_price_is_not_a_free_item_or_hallucinated_price():
    result = compare(doc(item={"unit_price": None}), doc())
    assert result.status == "needs_confirmation"
    assert "missing_price" in codes(result)


def test_blurry_ocr_is_not_a_clean_review():
    result = compare(doc(uncertainties=["数字模糊"]), doc())
    assert result.status == "needs_confirmation"


def test_duplicate_rows_are_not_silently_collapsed():
    baseline = doc()
    baseline.lines.append(baseline.lines[0].model_copy())
    result = compare(baseline, doc())
    assert "duplicate_lines" in codes(result)
    assert result.status != "no_differences"


def test_wrong_customer_and_extra_item():
    delivery = doc(customer="另一家店")
    delivery.lines.append(delivery.lines[0].model_copy(update={"name": "馅料", "sku": "F01"}))
    result = compare(doc(), delivery)
    assert {"customer", "extra_item"} <= codes(result)


def test_kg_spelling_is_equivalent_but_box_conversions_are_not():
    first = doc(item={"unit": "公斤", "price_unit": "公斤"})
    assert compare(first, doc(item={"unit": "kg", "price_unit": "kg"})).status == "no_differences"
    assert "unit" in codes(compare(first, doc(item={"unit": "箱"})))


def test_half_up_money_rounding():
    first = doc(item={"quantity": "1", "unit_price": "1.005", "amount": "1.01"}, total="1.01")
    assert "line_amount" not in codes(compare(first, first))
    assert first.lines[0].unit_price == Decimal("1.005")


def test_different_pricing_basis_is_never_multiplied_as_if_equal():
    first = doc(item={"price_unit": "公斤"})
    result = compare(first, first)
    assert result.status == "needs_confirmation"
    assert "pricing_basis" in codes(result)


def test_missing_pricing_basis_requires_confirmation():
    result = compare(doc(item={"price_unit": None}), doc())
    assert result.status == "needs_confirmation"
    assert "missing_price_unit" in codes(result)
