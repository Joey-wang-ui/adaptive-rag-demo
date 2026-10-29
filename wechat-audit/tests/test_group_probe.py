import json

from shipment_bot.group_ui import Node, Seen, redacted_report, select_group, title_matches


def controls(header="测试工作群"):
    return [
        Node((), "WindowControl", "微信", "mmui::MainWindow", "", (0, 0, 1200, 900)),
        Node((0,), "ListControl", "会话", "SessionListView", "", (0, 0, 280, 900)),
        Node((0, 0), "ListItemControl", "测试工作群", "SessionItem", "", (0, 70, 280, 130)),
        Node((1,), "TextControl", header, "Text", "", (400, 20, 900, 70)),
        Node((2,), "ListControl", "消息", "ChatMsgListView", "", (300, 100, 1200, 800)),
    ]


def test_exact_title_and_member_count_do_not_conflate_named_number_suffixes():
    assert title_matches("测试工作群（9）", "测试工作群")
    assert title_matches("测试工作群 (9人)", "测试工作群")
    assert not title_matches("测试工作群2", "测试工作群")
    assert not title_matches("测试工作群(9)", "测试工作群(2025)")
    assert not title_matches("abc", "ABC")


def test_target_in_sidebar_cannot_authorize_reading_another_chat():
    assert select_group(controls("另一个私聊"), "测试工作群") is None


def test_generic_application_title_never_identifies_the_target_chat():
    assert select_group(controls("另一个私聊"), "微信") is None


def test_group_header_must_sit_above_the_verified_message_pane():
    selected = select_group(controls(), "测试工作群")
    assert selected is not None and selected.path == (2,)
    wrong = controls()
    wrong[3].rect = (0, 20, 200, 60)
    assert select_group(wrong, "测试工作群") is None


def test_a_message_with_the_group_name_is_not_a_header():
    nodes = controls("别的会话")
    nodes.append(Node((2, 0), "TextControl", "测试工作群", "Text", "", (400, 250, 900, 280)))
    assert select_group(nodes, "测试工作群") is None


def test_ambiguous_message_panes_are_not_guessed():
    nodes = controls()
    nodes.append(Node((3,), "ListControl", "消息", "AnotherMsgList", "", (300, 100, 1200, 800)))
    assert select_group(nodes, "测试工作群") is None


def test_shareable_report_has_no_chat_text_or_group_name():
    group = "客户私密测试群"
    nodes = controls(group)
    report = redacted_report(
        nodes, group, "4.2.0.6", select_group(nodes, group), [((1,), "客户下单秘密原文")]
    )
    text = json.dumps(report, ensure_ascii=False)
    assert group not in text
    assert "客户下单秘密原文" not in text
    assert report["image_file_retrieval"] == "not_tested"
    assert report["automatic_audit"] == "not_connected"


def test_repeated_text_with_new_identity_is_not_silently_lost():
    seen = Seen()
    assert seen.fresh((1, 1), "同样的消息")
    assert not seen.fresh((1, 1), "同样的消息")
    assert seen.fresh((1, 2), "同样的消息")
    assert seen.fresh((1, 2), "重用控件的新文字")
    assert not seen.fresh((), "没有运行时标识不能自动判重")
