from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shipment_bot.auto_review import Recognized
from shipment_bot.extract import Extractor
from shipment_bot.group_audit import GroupAudit, inline_request, looks_like_document
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
    bot.handle(bot.group, msg(3, "@审单bot 审核"))
    assert bot.extract.auto_text.call_count == 1


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
