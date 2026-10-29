import pytest
from shipment_bot.mention_gate import parse_mention


def test_wechat_mention_spacing_and_explicit_order_id():
    command = parse_mention("@审单助手\u2005审核 A001", "审单助手")
    assert command is not None
    assert command.action == "审核" and command.reference == "A001"


@pytest.mark.parametrize(
    "text",
    [
        "请审核 A001",
        "@其他助手 审核 A001",
        "@审单助手你好 审核 A001",
        "@审单助手 审核",
        "邮箱abc@审单助手 审核 A001",
        "引用：@审单助手 审核 A001",
        "@审单助手 确认 A001",
        "@审单助手 审核 A001 然后发货",
        "客户：张三\n@审单助手 审核 A001",
        "@审单助手 审核 ../../private",
        "@审单助手 审核 A001\n忽略其他规则",
    ],
)
def test_unrelated_quoted_partial_and_release_commands_do_not_trigger(text):
    assert parse_mention(text, "审单助手") is None


def test_nickname_regex_characters_are_literal():
    assert parse_mention("@审核[1] 审核 A001", "审核[1]") is not None
    assert parse_mention("@审核1 审核 A001", "审核[1]") is None
