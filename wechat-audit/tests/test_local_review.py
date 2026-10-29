import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PIL import Image
from shipment_bot.auto_review import Recognized
from shipment_bot.desktop_state import Controller, save_settings
from shipment_bot.local_review import review_request
from shipment_bot.models import Document, Line


def recognized(source="image", quantity="2"):
    return Recognized(
        source_type=source,
        document=Document(
            customer="朴恩",
            total="100",
            lines=[
                Line(name="蛋液", quantity=quantity, unit="箱", unit_price="50", price_unit="箱")
            ],
        ),
    )


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "invoice.png"
    Image.new("RGB", (200, 200), "white").save(path)
    return path


def test_standalone_local_image_and_text_comparison_without_wechat_or_group(image):
    extractor = SimpleNamespace(
        auto_image=Mock(return_value=recognized(quantity="3")),
        auto_text=Mock(return_value=recognized("text")),
    )
    factory, progress = Mock(return_value=extractor), Mock()
    report = review_request(
        {"images": [str(image)], "text": "朴恩蛋液2箱，每箱50元，合计100元", "compare": True},
        {"audit": {"skill": "我的审单技能"}},
        "test-only",
        factory=factory,
        progress=progress,
    )
    assert "数量不一致" in report
    assert "文字为2，图片为3" in report
    assert factory.call_args.kwargs["skill"] == "我的审单技能"
    extractor.auto_image.assert_called_once_with(image)
    assert progress.call_count == 2


def test_image_failure_does_not_claim_text_image_match(tmp_path):
    extractor = SimpleNamespace(auto_image=Mock(), auto_text=Mock(return_value=recognized("text")))
    report = review_request(
        {"images": [str(tmp_path / "missing.png")], "text": "蛋液2箱，每箱50元", "compare": True},
        {},
        "test-only",
        factory=Mock(return_value=extractor),
    )
    assert "图文比对未完成" in report
    assert "图片文件不存在或不可读取" in report
    assert "字段与本张图片一致" not in report
    extractor.auto_image.assert_not_called()


@pytest.mark.parametrize(
    "spec",
    [
        {},
        {"images": ["a"] * 5},
        {"text": "x" * 20001},
        {"images": ["a", "b"], "text": "compare", "compare": True},
        {"images": "bad"},
    ],
)
def test_bad_requests_are_rejected_before_api_work(spec):
    factory = Mock()
    with pytest.raises(ValueError):
        review_request(spec, {}, "test-only", factory=factory)
    factory.assert_not_called()


def test_text_only_local_audit_is_supported():
    extractor = SimpleNamespace(auto_image=Mock(), auto_text=Mock(return_value=recognized("text")))
    report = review_request(
        {"text": "蛋液2箱，每箱50元，合计100元"},
        {},
        "test-only",
        factory=Mock(return_value=extractor),
    )
    assert "计算为100.00元" in report
    extractor.auto_image.assert_not_called()


def test_local_controller_does_not_require_group_binding_and_keeps_key_out_of_request(
    tmp_path, image
):
    save_settings(tmp_path, "", "", "test-only-key", True, require_group=False)
    process = Mock()
    process.poll.return_value = None
    process.tree_alive.return_value = False
    factory, hotkey = Mock(return_value=process), Mock()
    controller = Controller(
        tmp_path, tmp_path, worker_factory=factory, hotkey_factory=lambda: hotkey
    )
    controller.start("local", "test-only-key", review={"images": [str(image)], "text": ""})
    request_path = factory.call_args.args[2]["JIAHUI_LOCAL_REQUEST"]
    assert "test-only-key" not in open(request_path, encoding="utf-8").read()
    assert "test-only-key" not in str(factory.call_args.args[0])
    assert controller.busy
    controller.statusfile.write_text(
        json.dumps({"state": "reviewed", "report": "审核结果"}), encoding="utf-8"
    )
    process.poll.return_value = 0
    hotkey.pressed.is_set.return_value = False
    controller.poll()
    assert controller.state == "reviewed" and not controller.busy
    hotkey.close.assert_called_once()


def test_local_escape_stops_and_never_accepts_late_report(tmp_path, image):
    save_settings(tmp_path, "", "", "test-only", True, require_group=False)
    process = Mock()
    process.poll.return_value = None
    process.tree_alive.return_value = False
    factory, hotkey = Mock(return_value=process), Mock()
    hotkey.pressed.is_set.return_value = True
    controller = Controller(
        tmp_path, tmp_path, worker_factory=factory, hotkey_factory=lambda: hotkey
    )
    controller.start("local", "test-only", review={"images": [str(image)]})
    controller.poll()
    process.stop_tree.assert_called_once()
    controller.statusfile.write_text(
        json.dumps({"state": "reviewed", "report": "late"}), encoding="utf-8"
    )
    process.poll.return_value = 0
    controller.poll()
    assert controller.state == "stopped"
    assert factory.call_count == 1
    hotkey.close.assert_called_once()


@pytest.mark.skipif(os.name != "nt", reason="real Tk window and clipboard image support")
def test_real_local_window_selection_paste_result_and_close_without_stopping_group(
    tmp_path, image, monkeypatch
):
    import tkinter as tk

    from PIL import ImageGrab
    from shipment_bot.local_window import LocalReviewWindow

    controller = SimpleNamespace(
        busy=False,
        mode=None,
        stopping=False,
        state="idle",
        statusfile=None,
        start=Mock(),
        stop=Mock(),
    )
    root = tk.Tk()
    root.withdraw()
    window = LocalReviewWindow(root, controller, tmp_path, Mock(), lambda: "test-only")
    try:
        window.add_paths([image])
        assert window.paths == [image]
        monkeypatch.setattr(
            ImageGrab, "grabclipboard", lambda: Image.new("RGB", (200, 200), "white")
        )
        window.paste_image()
        assert len(window.paths) == 2
        window.clear_images()
        window.add_paths([image])
        window.text.insert("1.0", "蛋液2箱，每箱50元")
        window.start()
        assert controller.start.call_args.args[0] == "local"
        assert controller.start.call_args.kwargs["review"]["images"] == [str(image)]
        controller.mode, controller.state, controller.statusfile = "local", "reviewed", "request-1"
        window.refresh({"state": "reviewed", "report": "图文结果"})
        assert "图文结果" in window.result.get("1.0", "end")
        controller.busy, controller.mode = True, "run"
        window.close()
        controller.stop.assert_not_called()  # Closing unused window doesn't stop the group task.
    finally:
        if window.window.winfo_exists():
            window.window.destroy()
        root.destroy()


def test_natural_instruction_only_does_not_become_fake_document(image):
    from shipment_bot.audit_skill import ReviewIntent

    extractor = SimpleNamespace(
        auto_image=Mock(return_value=recognized()),
        auto_text=Mock(),
        interpret_request=Mock(return_value=ReviewIntent(action="review", scope="recent")),
    )
    report = review_request(
        {"images": [str(image)], "text": "帮我看看这张的金额", "compare": True},
        {},
        "test-only",
        factory=Mock(return_value=extractor),
    )
    extractor.auto_text.assert_not_called()
    assert "第1张" in report and "图文比对未完成" not in report


def test_natural_caption_request_is_separated_from_document(image):
    from shipment_bot.audit_skill import ReviewIntent

    body = "朴恩蛋液2箱，每箱50元，合计100元"
    extractor = SimpleNamespace(
        auto_image=Mock(return_value=recognized()),
        auto_text=Mock(return_value=recognized("text")),
        interpret_request=Mock(
            return_value=ReviewIntent(action="compare", scope="inline", document_text=body)
        ),
    )
    report = review_request(
        {"images": [str(image)], "text": body + "，跟图片一致吗？", "compare": True},
        {},
        "test-only",
        factory=Mock(return_value=extractor),
    )
    extractor.auto_text.assert_called_once_with(body)
    assert "已提供字段与图片一致" in report
