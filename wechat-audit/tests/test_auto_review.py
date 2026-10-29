from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from shipment_bot.auto_review import Recognized, classify, self_check, summarize
from shipment_bot.extract import ExtractionError, Extractor
from shipment_bot.group_audit import GroupAudit, automatic_request
from shipment_bot.models import Document, Line


def document(**changes):
    return Document(
        customer="测试店",
        document_number="A001",
        total="100",
        lines=[
            Line(
                name="蛋液", quantity="2", unit="箱", unit_price="50", price_unit="箱", amount="100"
            )
        ],
        **changes,
    )


@pytest.mark.parametrize(
    "text,kind",
    [
        ("杭州公司\n出货单\n订单号 A001", "delivery"),
        ("销售订单\n订单号 A001", "order"),
        ("订单号 A001", "unknown"),
        ("订单\n出货单", "unknown"),
        ("单价\n100", "unknown"),
    ],
)
def test_title_detection_does_not_classify_order_number_as_order(text, kind):
    assert classify(text)[0] == kind


def test_single_delivery_check_with_relaxed_optional_fields():
    doc = document()
    doc.customer = None
    assert self_check(doc) == []
    assert "客户" in "".join(self_check(doc, require_customer=True))
    assert "规格" in "".join(self_check(doc, require_specification=True))
    doc.lines[0].amount = Decimal("101")
    issues = self_check(doc)
    assert any("数量×单价=100.00" in s for s in issues)
    assert any("合计不符" in s for s in issues)


def test_unit_ambiguity_never_claims_calculation_error():
    doc = document()
    doc.lines[0].price_unit = None
    doc.lines[0].amount = Decimal("200")
    issues = self_check(doc)
    assert any("计价单位不明确" in x for x in issues)
    assert not any("数量×单价" in x for x in issues)


def test_pairing_requires_explicit_reference_and_customer():
    order = Recognized(document=document(), kind="order")
    delivery = Recognized(document=document(), kind="delivery", image_index=2)
    assert "未自动配对" in summarize([order, delivery])
    delivery.document.reference_order_id = "A001"
    assert "【两单比对】" in summarize([order, delivery])
    delivery.document.customer = "另一个店"
    assert "未自动配对" in summarize([order, delivery])


@pytest.mark.parametrize(
    "text",
    [
        "@审单bot 审核",
        "@审单bot\u2005帮我审核一下",
        "@审单bot 帮我看下这张单",
        "@审单bot 检查出货单",
    ],
)
def test_relaxed_at_phrases(text):
    assert automatic_request(text, "审单bot")


def test_non_requests_stay_silent():
    for text in ["审核", "@其他bot 审核", "@审单bot 今天天气如何", "引用 @审单bot 审核"]:
        assert not automatic_request(text, "审单bot")


def message(n, text="", sender="member", kind="text"):
    return dict(
        local_id=n,
        sort_seq=n,
        shard="message_0.db",
        sender_username=sender,
        type=kind,
        content=text,
    )


@pytest.fixture
def bot(tmp_path):
    extract = SimpleNamespace(
        auto_image=Mock(side_effect=lambda path: Recognized(document=document(), kind="delivery"))
    )
    return GroupAudit(
        tmp_path / "test.sqlite3",
        "g@chatroom",
        "self",
        "审单bot",
        extract,
        Mock(return_value=tmp_path / "image.png"),
        clock=lambda: 1000,
    )


def test_unlabelled_photos_only_call_model_on_at_and_are_cached(bot):
    assert bot.handle(bot.group, message(1, kind="图片")) is None
    assert bot.handle(bot.group, message(2, kind="图片")) is None
    bot.extract.auto_image.assert_not_called()
    result = bot.handle(bot.group, message(3, "@审单bot 审核"))
    assert "第1张" in result and "第2张" in result
    assert bot.extract.auto_image.call_count == 2
    bot.handle(bot.group, message(4, "@审单bot 审核"))
    assert bot.extract.auto_image.call_count == 2
    bot.handle(bot.group, message(5, kind="image"))
    result = bot.handle(bot.group, message(6, "@审单bot 审核"))
    assert "第2张" not in result
    assert bot.extract.auto_image.call_count == 3


def test_other_sender_only_when_unique_and_ambiguous_batches_refused(bot):
    bot.handle(bot.group, message(1, kind="image", sender="a"))
    assert "单据自动自查" in bot.handle(bot.group, message(2, "@审单bot 审核", sender="boss"))
    bot.handle(bot.group, message(3, kind="image", sender="b"))
    assert "单据自动自查" in bot.handle(bot.group, message(4, "@审单bot 审核", sender="boss"))
    assert bot.handle(bot.group, message(5, "@审单bot 审核", sender="b")) is None
    bot.handle(bot.group, message(6, kind="image", sender="b"))
    bot.handle(bot.group, message(7, kind="image", sender="c"))
    assert "多人发材料" in bot.handle(bot.group, message(8, "@审单bot 审核", sender="boss"))


def test_overflow_does_not_silently_ignore_extra_pages(bot):
    for n in range(1, 6):
        bot.handle(bot.group, message(n, kind="image"))
    assert "超过4份" in bot.handle(bot.group, message(6, "@审单bot 审核"))
    bot.extract.auto_image.assert_not_called()
    bot.handle(bot.group, message(7, kind="image"))
    assert "第1张" in bot.handle(bot.group, message(8, "@审单bot 审核"))


def test_stale_batch_and_failed_image_not_reported_as_pass(bot):
    bot.handle(bot.group, message(1, kind="image"))
    bot.extract.auto_image.side_effect = ExtractionError("原图不可用")
    result = bot.handle(bot.group, message(2, "@审单bot 审核"))
    assert "未完成识别" in result and "未发现计算差异" not in result
    bot.clock = lambda: 2000
    assert "没有可审核" in bot.handle(bot.group, message(3, "@审单bot 审核"))


def test_actual_configurable_api_request_and_custom_instructions():
    def handler(request):
        assert str(request.url) == "https://models.example.test/v1/chat/completions"
        import json

        body = json.loads(request.content)
        assert body["model"] == "small-model"
        assert "注意袋装商品" in body["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": document().model_dump_json()}}
                ]
            },
        )

    extractor = Extractor(
        "test-key",
        base_url="https://models.example.test/v1",
        model="small-model",
        instructions="注意袋装商品",
        transport=httpx.MockTransport(handler),
    )
    assert (
        extractor.parse("测试店 A001 蛋液 数量2箱 单价50元/箱 金额100 合计100").customer == "测试店"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://models.example.test/v1",
        "https://user:secret@host.test/v1",
        "https://host.test/v1?api_key=secret",
    ],
)
def test_unsafe_or_credential_bearing_url_rejected(url):
    with pytest.raises(ExtractionError):
        Extractor("test", base_url=url)


def test_auto_image_classification_requires_visible_header_and_metadata_source():
    extractor = Extractor("test")
    extractor.read_image = Mock(
        return_value=("送货单\n蛋液 数量2箱 单价50元/箱 金额100 合计100", [])
    )
    extractor.parse = Mock(return_value=document())
    result = extractor.auto_image("unused")
    assert result.kind == "delivery"
    assert result.document.customer is None
    assert result.document.document_number is None


def test_user_screenshot_missing_line_amount_is_calculated_without_mutating_evidence():
    doc = document()
    doc.document_number = None
    doc.lines[0].amount = None
    assert self_check(doc) == []
    report = summarize([Recognized(document=doc, source_type="text")])
    assert "计算为100.00元（计算值）" in report
    assert "与填写的合计一致" in report
    assert "无法核算" not in report
    assert doc.lines[0].amount is None


def test_missing_line_amount_still_detects_wrong_total():
    doc = document()
    doc.lines[0].amount = None
    doc.total = Decimal("120")
    report = summarize([Recognized(document=doc, source_type="text")])
    assert "合计不符" in report and "100.00" in report and "120" in report
    assert "未发现计算差异" not in report


@pytest.mark.parametrize("price_unit", [None, "公斤"])
def test_missing_line_amount_never_invents_pricing_units_or_conversion(price_unit):
    doc = document()
    doc.lines[0].amount = None
    doc.lines[0].price_unit = price_unit
    report = summarize([Recognized(document=doc)])
    assert "无法完整核对总计" in report
    assert "与填写的合计一致" not in report
    assert "计算为100.00" not in report
