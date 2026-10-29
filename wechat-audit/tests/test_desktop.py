import json
import time
from unittest.mock import Mock

import pytest
from shipment_bot.desktop_state import Controller, save_settings


def test_settings_change_invalidates_detected_binding(tmp_path):
    config = {"group_name": "测试群", "bot_nickname": "审单bot", "binding": {"group": "g@chatroom"}}
    (tmp_path / "config.json").write_text(json.dumps(config))
    same = save_settings(tmp_path, "测试群", "审单bot", "dummy-key", True)
    assert same["binding"] == config["binding"]
    assert (tmp_path / "api-key.txt").read_text() == "dummy-key"
    changed = save_settings(tmp_path, "另一个群", "审单bot", "dummy-key", False)
    assert "binding" not in changed
    assert not (tmp_path / "api-key.txt").exists()


def test_new_window_controller_does_not_start_process(tmp_path):
    controller = Controller(tmp_path, tmp_path)
    assert controller.state == "idle"
    assert not controller.busy
    assert controller.poll() == ("", {})


def test_unchecked_group_cannot_run(tmp_path):
    save_settings(tmp_path, "测试群", "审单bot", "", False)
    controller = Controller(tmp_path, tmp_path)
    with pytest.raises(ValueError, match="检测连接"):
        controller.start("run", "dummy")
    assert controller.process is None


def test_no_key_in_command_arguments(tmp_path, monkeypatch):
    save_settings(tmp_path, "测试群", "审单bot", "", False)
    process = Mock()
    process.poll.return_value = None
    popen = Mock(return_value=process)
    monkeypatch.setattr("shipment_bot.desktop_state.subprocess.Popen", popen)
    controller = Controller(tmp_path, tmp_path)
    controller.start("check", "dummy-secret")
    assert "dummy-secret" not in str(popen.call_args.args[0])
    assert popen.call_args.kwargs["env"]["DEEPSEEK_API_KEY"] == "dummy-secret"
    assert not (tmp_path / "api-key.txt").exists()
    with pytest.raises(ValueError, match="正在运行"):
        controller.start("check")


def test_worker_lifecycle_with_real_harmless_subprocess(tmp_path):
    save_settings(tmp_path, "测试群", "审单bot", "", False)
    (tmp_path / "group_launch.py").write_text("""
import json, os, sys, time
from pathlib import Path
state = "checked" if sys.argv[1] == "check" else "running"
Path(os.environ["JIAHUI_AUDIT_STATUS"]).write_text(json.dumps({"state": state}))
print("worker ready", flush=True)
if state == "running": time.sleep(30)
""")
    controller = Controller(tmp_path, tmp_path)
    controller.start("check")
    output = ""
    deadline = time.monotonic() + 5
    while controller.process is not None and time.monotonic() < deadline:
        output += controller.poll()[0]
        time.sleep(0.02)
    assert controller.state == "checked"
    assert "worker ready" in output
    config = json.loads((tmp_path / "config.json").read_text())
    config["binding"] = {"group": "g@chatroom", "account": "self"}
    (tmp_path / "config.json").write_text(json.dumps(config))
    controller.start("run", "dummy")
    deadline = time.monotonic() + 5
    try:
        while controller.state != "running" and time.monotonic() < deadline:
            controller.poll()
            time.sleep(0.02)
        assert controller.state == "running"
        controller.stop()
        while controller.process is not None and time.monotonic() < deadline:
            controller.poll()
            time.sleep(0.02)
        assert controller.state == "stopped"
    finally:
        if controller.busy:
            controller.process.kill()


def test_exit_success_without_detection_status_is_not_claimed_ready(tmp_path, monkeypatch):
    save_settings(tmp_path, "测试群", "审单bot", "", False)
    process = Mock()
    process.poll.return_value = 0
    monkeypatch.setattr("shipment_bot.desktop_state.subprocess.Popen", Mock(return_value=process))
    controller = Controller(tmp_path, tmp_path)
    controller.start("check")
    controller.poll()
    assert controller.state == "error"
