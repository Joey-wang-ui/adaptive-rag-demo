"""Exercise the adapter boundary with fake transport: never connects to WeChat."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shipment_bot import group_runtime
from shipment_bot.group_audit import REPLY_PREFIX


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "group_name": "测试群",
                "bot_nickname": "审单bot",
                "binding": {"account": "self", "group": "g@chatroom"},
            }
        )
    )
    monkeypatch.setattr(group_runtime, "DATA", tmp_path)
    monkeypatch.setattr(group_runtime, "CONFIG", config)
    monkeypatch.setattr(
        group_runtime, "os", SimpleNamespace(name="nt", environ={"DEEPSEEK_API_KEY": "test-only"})
    )
    db = Mock()
    db.get_self_info.return_value = {"username": "self"}
    db.search_contact.return_value = [{"username": "g@chatroom", "nick_name": "测试群"}]
    db.get_messages.return_value = [{"sort_seq": 50}]
    send, extract = Mock(return_value={"status": "成功"}), Mock()
    extract.return_value.interpret_request = None
    listener = Mock()
    listener_factory = Mock(return_value=listener)
    monkeypatch.setitem(
        sys.modules,
        "wechatauto",
        SimpleNamespace(WeChatDB=Mock(return_value=db), MediaDownloader=Mock()),
    )
    monkeypatch.setattr(group_runtime, "build_media", lambda factory, db: factory(db))
    monkeypatch.setitem(sys.modules, "wechatauto.db", SimpleNamespace(Listener=listener_factory))
    monkeypatch.setitem(sys.modules, "wechatauto.guia", SimpleNamespace(quick_send=send))
    monkeypatch.setattr(group_runtime, "Extractor", extract)
    monkeypatch.setattr(group_runtime.time, "sleep", Mock(side_effect=KeyboardInterrupt))
    return SimpleNamespace(
        config=config,
        db=db,
        send=send,
        extract=extract,
        listener=listener,
        factory=listener_factory,
    )


def test_check_never_loads_model_or_sends(runtime, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["runtime", "check"])
    group_runtime.main()
    runtime.send.assert_not_called()
    runtime.extract.assert_not_called()
    runtime.factory.assert_not_called()


def test_run_only_registers_bound_group_and_at_can_reply(runtime, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["runtime", "run"])

    def start():
        group, callback = runtime.listener.add_listener.call_args.args
        assert group == "g@chatroom"
        callback(
            {
                "local_id": 1,
                "sort_seq": 51,
                "type": "文本",
                "sender_username": "member",
                "content": "你好",
            },
            None,
        )
        runtime.send.assert_not_called()
        callback(
            {
                "local_id": 2,
                "sort_seq": 52,
                "type": "文本",
                "sender_username": "member",
                "content": "@审单bot 审核 A001",
            },
            None,
        )

    runtime.listener.start.side_effect = start
    group_runtime.main()
    assert runtime.factory.call_args.kwargs["watermark_file"] == ""
    assert runtime.factory.call_args.kwargs["max_retries"] == 0
    runtime.send.assert_called_once()
    assert runtime.send.call_args.args[1] == "测试群"
    assert runtime.send.call_args.kwargs["verify"] is True
    assert "材料不足" in runtime.send.call_args.args[0]


def test_wrong_binding_stops_before_api_or_listener(runtime, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["runtime", "run"])
    runtime.db.get_self_info.return_value = {"username": "different-account"}
    with pytest.raises(RuntimeError, match="账号或群"):
        group_runtime.main()
    runtime.extract.assert_not_called()
    runtime.factory.assert_not_called()
    runtime.send.assert_not_called()


@pytest.mark.parametrize("enabled", [False, True])
def test_self_test_setting_reaches_adapter_and_own_reply_does_not_loop(
    runtime, monkeypatch, enabled
):
    config = json.loads(runtime.config.read_text())
    config["allow_self_test"] = enabled
    runtime.config.write_text(json.dumps(config))
    monkeypatch.setattr(sys, "argv", ["runtime", "run"])

    def start():
        group, callback = runtime.listener.add_listener.call_args.args
        request = dict(
            local_id=1,
            sort_seq=51,
            type="文本",
            sender_username="self",
            content="@审单bot 审核 A001",
        )
        callback(request, None)
        if enabled:
            reply = runtime.send.call_args.args[0]
            assert reply.startswith(REPLY_PREFIX)
            callback({**request, "local_id": 2, "sort_seq": 52, "content": reply}, None)

    runtime.listener.start.side_effect = start
    group_runtime.main()
    assert runtime.send.call_count == int(enabled)


@pytest.mark.parametrize("failure", [None, PermissionError("private-path test-only")])
def test_image_then_mention_replies_on_missing_original_or_download_failure(
    runtime, monkeypatch, failure
):
    config = json.loads(runtime.config.read_text())
    config["allow_self_test"] = True
    runtime.config.write_text(json.dumps(config))
    monkeypatch.setattr(sys, "argv", ["runtime", "run"])
    media = sys.modules["wechatauto"].MediaDownloader.return_value
    media.download_image.return_value = None
    media.download_image.side_effect = failure

    def start():
        _, callback = runtime.listener.add_listener.call_args.args
        callback(
            dict(
                local_id=1,
                sort_seq=51,
                type="图片",
                sender_username="self",
                content="[图片]",
                shard="message_0.db",
            ),
            None,
        )
        runtime.send.assert_not_called()
        callback(
            dict(
                local_id=2,
                sort_seq=52,
                type="文本",
                sender_username="self",
                content="@审单bot 审核",
            ),
            None,
        )

    runtime.listener.start.side_effect = start
    group_runtime.main()
    runtime.send.assert_called_once()
    reply = runtime.send.call_args.args[0]
    assert "未完成识别" in reply
    assert ("尚未下载到电脑" if failure is None else "读取微信图片失败") in reply
    assert "private-path" not in reply and "test-only" not in reply
    runtime.extract.return_value.auto_image.assert_not_called()
    media.detect_image_key.assert_not_called()
