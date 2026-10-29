from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shipment_bot.group_audit import GroupAudit, command, resolve_group
from shipment_bot.models import Document, Line


@pytest.fixture
def bot(tmp_path):
    document = Document(
        customer="测试店",
        reference_order_id="A001",
        total="100",
        lines=[
            Line(
                name="蛋液",
                specification="10kg",
                quantity="2",
                unit="箱",
                unit_price="50",
                price_unit="箱",
                amount="100",
            )
        ],
    )
    extract = SimpleNamespace(parse=Mock(return_value=document), image=Mock(return_value=document))
    return GroupAudit(
        tmp_path / "audit.sqlite3",
        "g@chatroom",
        "self",
        "审单bot",
        extract,
        Mock(return_value=tmp_path / "image.jpg"),
        clock=lambda: 1000,
    )


def msg(n, text="", sender="member", kind="text"):
    return dict(
        local_id=n,
        sort_seq=n,
        sender_username=sender,
        type=kind,
        content=text,
        shard="message_0.db",
    )


@pytest.mark.parametrize(
    "text",
    [
        "你好",
        "审核 A001",
        "@审单bot 确认 A001",
        "引用 @审单bot 审核 A001",
        "@审单bot 审核 A001\n顺便闲聊",
        "@别的bot 审核 A001",
    ],
)
def test_strict_gate(text):
    assert command(text, "审单bot") is None


def test_wechat_mention_space():
    assert command("@审单bot\u2005审核 A001", "审单bot") == "A001"


def test_private_other_group_self_unknown_sender_are_ignored(bot):
    for group, sender in [
        ("private", "member"),
        ("else@chatroom", "member"),
        (bot.group, "self"),
        (bot.group, ""),
    ]:
        assert bot.handle(group, msg(1, "@审单bot 审核 A001", sender)) is None
    bot.extract.parse.assert_not_called()


def test_materials_wait_for_at_and_reuse_parsed_result(bot):
    for n, text in [(1, "订单 A001\n订单正文"), (2, "出货单 A001\n出货单正文")]:
        assert bot.handle(bot.group, msg(n, text)) is None
    bot.extract.parse.assert_not_called()
    text = bot.handle(bot.group, msg(3, "@审单bot 审核 A001"))
    assert "无差异" in text and "不代表已批准" in text
    assert "可发送：确认" not in text
    assert bot.extract.parse.call_count == 2
    assert bot.handle(bot.group, msg(3, "@审单bot 审核 A001")) is None
    assert bot.handle(bot.group, msg(4, "@审单bot 审核 A001")) is None
    assert bot.extract.parse.call_count == 2


def test_completed_labelled_case_is_skipped_after_restart_but_new_material_can_review(bot):
    bot.handle(bot.group, msg(1, "订单 A001\n订单正文"))
    bot.handle(bot.group, msg(2, "出货单 A001\n出货单正文"))
    assert "无差异" in bot.handle(bot.group, msg(3, "@审单bot 审核 A001"))
    restarted = GroupAudit(
        bot.path,
        bot.group,
        bot.account,
        bot.nickname,
        bot.extract,
        bot.image_loader,
        clock=lambda: 1001,
    )
    assert restarted.handle(bot.group, msg(4, "@审单bot 审核 A001")) is None
    assert bot.extract.parse.call_count == 2
    restarted.handle(bot.group, msg(5, "出货单 A001\n新出货单正文"))
    assert "无差异" in restarted.handle(bot.group, msg(6, "@审单bot 审核 A001"))
    assert bot.extract.parse.call_count == 3


def test_image_association_is_per_sender_and_invalidates_old_role(bot):
    bot.handle(bot.group, msg(1, "订单 A001\n旧订单"))
    bot.handle(bot.group, msg(2, "出货单 A001\n出货单"))
    bot.handle(bot.group, msg(3, "订单 A001"))
    bot.handle(bot.group, msg(4, kind="image", sender="someone_else"))
    assert "材料不足" in bot.handle(bot.group, msg(5, "@审单bot 审核 A001"))
    bot.handle(bot.group, msg(6, kind="image"))
    assert "无差异" in bot.handle(bot.group, msg(7, "@审单bot 审核 A001"))
    bot.image_loader.assert_called_once()


def test_unrelated_text_cancels_pending_and_expiry_blocks_old_images(bot):
    bot.handle(bot.group, msg(1, "订单 A001"))
    bot.handle(bot.group, msg(2, "其他闲聊"))
    bot.handle(bot.group, msg(3, kind="image"))
    assert "材料不足" in bot.handle(bot.group, msg(4, "@审单bot 审核 A001"))
    bot.handle(bot.group, msg(5, "订单 A001"))
    bot.clock = lambda: 1201
    bot.handle(bot.group, msg(6, kind="image"))
    assert "材料不足" in bot.handle(bot.group, msg(7, "@审单bot 审核 A001"))


def test_ambiguous_group_is_rejected():
    db = Mock()
    db.search_contact.return_value = [
        dict(username="a@chatroom", nick_name="测试"),
        dict(username="b@chatroom", nick_name="测试"),
    ]
    with pytest.raises(ValueError):
        resolve_group(db, "测试")
    db.search_contact.return_value = [dict(username="private", nick_name="测试")]
    with pytest.raises(ValueError):
        resolve_group(db, "测试")


def test_difference_and_material_expiry(bot):
    bot.handle(bot.group, msg(1, "订单 A001\n订单"))
    bot.handle(bot.group, msg(2, "出货单 A001\n出货单"))
    order = bot.extract.parse.return_value
    delivery = order.model_copy(deep=True)
    delivery.lines[0].quantity = Decimal("3")
    bot.extract.parse.side_effect = [order, delivery]
    assert "发现差异" in bot.handle(bot.group, msg(3, "@审单bot 审核 A001"))
    bot.clock = lambda: 100000
    assert bot.handle(bot.group, msg(4, "@审单bot 审核 A001")) is None
    bot.clock = lambda: 1001
    bot.handle(bot.group, msg(5, "出货单 A001\n新出货单"))
    bot.clock = lambda: 100000
    assert "超过24小时" in bot.handle(bot.group, msg(6, "@审单bot 审核 A001"))


def test_upstream_chinese_types_and_sender_prefix(bot):
    bot.handle(bot.group, msg(1, "member:\n订单 A001", kind="文本"))
    bot.handle(bot.group, msg(2, "[图片]", kind="图片"))
    bot.handle(bot.group, msg(3, "member:\n出货单 A001\n出货单正文", kind="文本"))
    assert "无差异" in bot.handle(
        bot.group, msg(4, "member:\n@审单bot\u2005审核 A001", kind="文本")
    )
    bot.image_loader.assert_called_once()
