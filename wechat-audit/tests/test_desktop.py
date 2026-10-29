import json
import time
from unittest.mock import Mock

import pytest
from shipment_bot.desktop_state import Controller, save_settings


def test_self_test_default_off_and_explicitly_saved(tmp_path):
    config = save_settings(tmp_path, "测试群", "审单bot", "", False)
    assert config["allow_self_test"] is False
    config = save_settings(tmp_path, "测试群", "审单bot", "", False, allow_self_test=True)
    assert config["allow_self_test"] is True
    assert json.loads((tmp_path / "config.json").read_text())["allow_self_test"] is True


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
    controller = Controller(tmp_path, tmp_path, worker_factory=popen)
    controller.start("check", "dummy-secret")
    assert "dummy-secret" not in str(popen.call_args.args[0])
    assert popen.call_args.args[2]["DEEPSEEK_API_KEY"] == "dummy-secret"
    assert not (tmp_path / "api-key.txt").exists()
    with pytest.raises(ValueError, match="正在运行"):
        controller.start("check")
    controller.close()


def test_worker_lifecycle_with_real_harmless_subprocess(tmp_path):
    save_settings(tmp_path, "测试群", "审单bot", "", False)
    (tmp_path / "group_launch.py").write_text("""
import json, os, sys, time
from pathlib import Path
state = "checked" if sys.argv[1] == "check" else "running"
Path(os.environ["JIAHUI_AUDIT_STATUS"]).write_text(json.dumps({"state": state}))
Path(os.environ["JIAHUI_AUDIT_LOG"]).write_text("worker ready")
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
    process.tree_alive.return_value = False
    monkeypatch.setattr("shipment_bot.desktop_state.subprocess.Popen", Mock(return_value=process))
    controller = Controller(tmp_path, tmp_path, worker_factory=lambda *args: process)
    controller.start("check")
    controller.poll()
    assert controller.state == "error"


def test_switch_provider_removes_old_key_and_persists_preferences(tmp_path):
    save_settings(tmp_path, "测试群", "审单bot", "old-provider-key", True)
    config = save_settings(
        tmp_path,
        "测试群",
        "审单bot",
        "old-provider-key",
        True,
        llm={"base_url": "https://new.example.test/v1", "model": "small-v1"},
        audit={"instructions": "注意规格简称", "require_customer": True},
    )
    assert not (tmp_path / "api-key.txt").exists()
    assert config["llm"]["model"] == "small-v1"
    assert config["audit"]["require_customer"] is True
    assert config["audit"]["instructions"] == "注意规格简称"
    save_settings(
        tmp_path,
        "测试群",
        "审单bot",
        "new-provider-key",
        True,
        llm=config["llm"],
        audit=config["audit"],
    )
    assert (tmp_path / "api-key.txt").read_text() == "new-provider-key"


class FakeEscape:
    def __init__(self):
        import threading

        self.pressed = threading.Event()
        self.closed = False

    def start(self):
        pass

    def close(self):
        self.closed = True


def prepared_controller(tmp_path, **kwargs):
    save_settings(tmp_path, "测试群", "审单bot", "", False, allow_self_test=True)
    config = json.loads((tmp_path / "config.json").read_text())
    config["binding"] = {"group": "g@chatroom", "account": "self"}
    (tmp_path / "config.json").write_text(json.dumps(config))
    return Controller(tmp_path, tmp_path, **kwargs)


def test_registration_failure_prevents_worker_start(tmp_path):
    class Unavailable(FakeEscape):
        def start(self):
            raise OSError("无法注册全局 Esc 急停")

    factory = Mock()
    controller = prepared_controller(tmp_path, hotkey_factory=Unavailable, worker_factory=factory)
    with pytest.raises(OSError, match="Esc"):
        controller.start("run", "dummy")
    factory.assert_not_called()
    assert not controller.busy
    assert controller.hotkey is None


@pytest.mark.parametrize("mode", ["check", "run"])
def test_escape_stops_owned_tree_once_and_requires_manual_restart(tmp_path, mode):
    key = FakeEscape()
    process = Mock()
    process.poll.return_value = None
    process.tree_alive.return_value = False
    factory = Mock(return_value=process)
    controller = prepared_controller(tmp_path, hotkey_factory=lambda: key, worker_factory=factory)
    assert controller.hotkey is None  # no global registration while idle
    controller.start(mode, "dummy")
    key.pressed.set()
    controller.poll()
    controller.poll()
    process.stop_tree.assert_called_once_with()
    assert controller.state == "stopping"
    with pytest.raises(ValueError):
        controller.start("run", "dummy")
    process.poll.return_value = 1
    controller.poll()
    assert controller.state == "stopped"
    assert key.closed
    assert controller.hotkey is None
    for _ in range(3):
        controller.poll()
    assert factory.call_count == 1  # never auto-restart
    controller.start("run", "dummy")  # explicit button path only
    assert factory.call_count == 2
    controller.close()


def test_start_failure_releases_global_key(tmp_path):
    key = FakeEscape()
    controller = prepared_controller(
        tmp_path, hotkey_factory=lambda: key, worker_factory=Mock(side_effect=OSError("job failed"))
    )
    with pytest.raises(OSError, match="job failed"):
        controller.start("run", "dummy")
    assert key.closed and not controller.busy


def test_natural_exit_kills_surviving_descendants_before_releasing_key(tmp_path):
    key, process = FakeEscape(), Mock()
    process.poll.return_value = 1
    process.tree_alive.side_effect = [True, False]
    controller = prepared_controller(
        tmp_path, hotkey_factory=lambda: key, worker_factory=lambda *args: process
    )
    controller.start("run", "dummy")
    controller.poll()
    process.stop_tree.assert_called_once_with()
    assert controller.busy and not key.closed
    controller.poll()
    assert not controller.busy and key.closed
    assert controller.state == "error"


def test_failed_stop_does_not_claim_stopped_or_allow_restart(tmp_path):
    key, process = FakeEscape(), Mock()
    process.stop_tree.side_effect = OSError("access denied")
    controller = prepared_controller(
        tmp_path, hotkey_factory=lambda: key, worker_factory=lambda *args: process
    )
    controller.start("run", "dummy")
    with pytest.raises(OSError):
        controller.stop()
    assert controller.busy and not key.closed
    assert controller.state != "stopped"
    with pytest.raises(ValueError):
        controller.start("run", "dummy")
    controller.close()
    process.close.assert_called_once_with()
    assert key.closed
