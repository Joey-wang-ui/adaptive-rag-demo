from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shipment_bot.auto_review import Recognized
from shipment_bot.extract import Extractor
from shipment_bot.group_audit import REPLY_PREFIX, GroupAudit, inline_request, looks_like_document
from shipment_bot.models import Document, Line


def doc(reference=None):
    return Document(
        customer="王记店",
        document_number="A001",
        reference_order_id=reference,
        total="100",
        lines=[
            Line(
                name="蛋液",
                quantity="2",
                unit="箱",
                unit_price="50",
                price_unit="箱",
                amount="100",
                specification="10kg",
            )
        ],
    )


def msg(n, text="", kind="text", sender="member"):
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
    extractor = SimpleNamespace(
        auto_text=Mock(
            side_effect=lambda text: Recognized(document=doc(), kind="order", source_type="text")
        ),
        auto_image=Mock(side_effect=lambda path: Recognized(document=doc("A001"), kind="delivery")),
    )
    return GroupAudit(
        tmp_path / "test.sqlite3",
        "g@chatroom",
        "self",
        "审单bot",
        extractor,
        Mock(return_value=Path("test.png")),
        clock=lambda: 1000,
    )


@pytest.mark.parametrize(
    "text",
    ["王记店蛋液2箱，每箱50元，合计100元", "订单 客户王记店 蛋液两箱", "蛋液10kg", "合计100"],
)
def test_plain_business_text_is_candidate(text):
    assert looks_like_document(text)


@pytest.mark.parametrize("text", ["好的", "明天10点开会", "这个链接发我一下", "x" * 20001])
def test_ordinary_chat_not_candidate(text):
    assert not looks_like_document(text)


def test_plain_text_review_waits_for_at_and_reuses_cache(bot):
    assert bot.handle(bot.group, msg(1, "王记店蛋液2箱，每箱50元，合计100元")) is None
    bot.extract.auto_text.assert_not_called()
    result = bot.handle(bot.group, msg(2, "@审单bot 审核"))
    assert "第1份文字" in result
    bot.image_loader.assert_not_called()
    assert bot.handle(bot.group, msg(3, "@审单bot 审核")) is None
    assert bot.extract.auto_text.call_count == 1


def test_self_test_is_opt_in_and_only_in_selected_group(bot):
    body = "@审单bot 审核\n王记店 蛋液2箱 每箱50元 金额100元"
    assert bot.handle(bot.group, msg(1, body, sender="self")) is None
    bot.extract.auto_text.assert_not_called()
    bot.allow_self_test = True
    assert bot.handle("private", msg(2, body, sender="self")) is None
    assert bot.handle("other@chatroom", msg(3, body, sender="self")) is None
    result = bot.handle(bot.group, msg(4, body, sender="self"))
    assert "第1份文字" in result
    assert bot.handle(bot.group, msg(5, "@审单bot 审核", sender="self")) is None
    assert bot.extract.auto_text.call_count == 1


def test_self_test_image_then_command_and_reply_cannot_become_material(bot):
    bot.allow_self_test = True
    bot.handle(bot.group, msg(1, kind="image", sender="self"))
    bot.extract.auto_image.assert_not_called()
    result = bot.handle(bot.group, msg(2, "@审单bot 审核", sender="self"))
    assert "第1张" in result
    echo = "self:\n" + REPLY_PREFIX + "\n" + result + "\n@审单bot 审核"
    assert bot.handle(bot.group, msg(3, echo, sender="self")) is None
    assert bot.handle(bot.group, msg(4, "@审单bot 审核", sender="self")) is None
    assert bot.extract.auto_image.call_count == 1
    bot.handle(bot.group, msg(5, "王记店 蛋液2箱 每箱50元", sender="self"))
    assert "第1份文字" in bot.handle(bot.group, msg(6, "@审单bot 审核", sender="self"))
    assert bot.extract.auto_text.call_count == 1


def test_completed_own_batch_does_not_fall_back_to_other_member(bot):
    bot.handle(bot.group, msg(1, "王记店 蛋液2箱", sender="member"))
    bot.handle(bot.group, msg(2, "@审单bot 审核", sender="member"))
    bot.handle(bot.group, msg(3, "王记店 蛋液2箱", sender="other"))
    assert bot.handle(bot.group, msg(4, "@审单bot 审核", sender="member")) is None
    assert bot.extract.auto_text.call_count == 1
    assert "第1份文字" in bot.handle(bot.group, msg(5, "@审单bot 审核", sender="other"))


def test_failed_material_can_retry_but_completed_batch_stays_silent(bot):
    from shipment_bot.extract import ExtractionError

    bot.handle(bot.group, msg(1, "王记店 蛋液2箱"))
    bot.handle(bot.group, msg(2, kind="image"))
    bot.image_loader.side_effect = ExtractionError("请打开原图")
    assert "未完成识别" in bot.handle(bot.group, msg(3, "@审单bot 审核"))
    bot.image_loader.side_effect = None
    assert "第2张" in bot.handle(bot.group, msg(4, "@审单bot 审核"))
    assert bot.extract.auto_text.call_count == 1
    assert bot.extract.auto_image.call_count == 1
    assert bot.handle(bot.group, msg(5, "@审单bot 审核")) is None


def test_mixed_order_text_and_delivery_image_can_be_compared(bot):
    bot.handle(bot.group, msg(1, "订单 客户王记店，单号A001，蛋液2箱，每箱50元，金额100元"))
    bot.handle(bot.group, msg(2, kind="图片"))
    result = bot.handle(bot.group, msg(3, "@审单bot 帮我审核一下"))
    assert "第1份文字" in result and "第2张" in result and "【两单比对】" in result


@pytest.mark.parametrize("text", ["@审单bot 审核\n王记店 蛋液2箱", "王记店 蛋液2箱\n@审单bot 审核"])
def test_inline_text_request_in_one_message(bot, text):
    assert inline_request(text, "审单bot") == "王记店 蛋液2箱"
    assert "第1份文字" in bot.handle(bot.group, msg(1, text))
    assert bot.extract.auto_text.call_count == 1


def test_explicit_inline_can_review_incomplete_document(bot):
    assert "第1份文字" in bot.handle(bot.group, msg(1, "@审单bot 审核\n客户王记店"))


def test_foreign_mentions_and_private_messages_never_sent_to_model(bot):
    bot.handle(bot.group, msg(1, "@其他人 客户王记店，蛋液2箱"))
    assert "没有可审核" in bot.handle(bot.group, msg(2, "@审单bot 审核"))
    assert bot.handle("private", msg(3, "@审单bot 审核\n客户王记店，蛋液2箱")) is None
    bot.extract.auto_text.assert_not_called()


def test_text_parser_does_not_load_ocr_and_validates_source_metadata():
    extractor = Extractor("dummy-key")
    extractor.parse = Mock(return_value=doc())
    extractor.read_image = Mock(side_effect=AssertionError("OCR must not run for text"))
    result = extractor.auto_text("订单\n王记店 A001 蛋液2箱 每箱50元 金额100元")
    assert result.source_type == "text" and result.kind == "order"
    assert result.document.customer == "王记店"
    assert result.document.document_number == "A001"
    extractor.read_image.assert_not_called()


def test_received_image_warning_survives_cache_and_prevents_unqualified_pass(bot):
    from shipment_bot.image_read import ImageAsset

    bot.image_loader.return_value = ImageAsset(Path("test.png"), "微信接收版，非已确认原图")
    bot.handle(bot.group, msg(1, kind="图片"))
    report = bot.handle(bot.group, msg(2, "@审单bot 审核"))
    assert "微信接收版，非已确认原图" in report
    assert "未发现计算差异" not in report


@pytest.mark.parametrize(
    "text",
    [
        "@审单bot 审核 客户朴恩，蛋液2箱，50元/箱，合计100元",
        "@审单bot审核：朴恩蛋液2箱，每箱50元，合计100元",
        "@审单bot 帮我看看，朴恩蛋液2箱，每箱50元",
    ],
)
def test_one_line_natural_document_request(bot, text):
    report = bot.handle(bot.group, msg(1, text))
    assert "第1份文字" in report
    assert "@审单bot" not in bot.extract.auto_text.call_args.args[0]


@pytest.mark.parametrize("text", ["@其他人 审核 朴恩蛋液2箱", "引用 @审单bot 审核 朴恩蛋液2箱"])
def test_one_line_foreign_or_quoted_request_stays_silent(bot, text):
    assert bot.handle(bot.group, msg(1, text)) is None
    bot.extract.auto_text.assert_not_called()
