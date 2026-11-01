import ast
import hashlib
import json
from types import SimpleNamespace

import pytest
from shipment_bot.wechat.wxauto4_diagnostics import Diagnostics, attach_existing
from shipment_bot.wechat.wxauto4_prepare import (
    MANIFEST,
    UPSTREAM_COMMIT,
    patch_update_deletion,
    verify,
)


def message(**changes):
    return SimpleNamespace(
        **{
            "id": "ui-123",
            "sender": "同名成员",
            "sender_remark": "",
            "content": "/审核 A001",
            "type": "text",
            "attr": "friend",
            **changes,
        }
    )


class Chat:
    def __init__(self, who="测试群", kind="group", messages=None):
        self.who = who
        self.kind = kind
        self.messages = messages or []

    def ChatInfo(self):
        return {"chat_name": self.who, "chat_type": self.kind}

    def GetNewMessage(self):
        return self.messages


def test_patch_disables_deletion_in_constructor_and_repeated_listener_calls(tmp_path):
    original = """from pathlib import Path

def delete_update_files():
    for file in Path('update').iterdir():
        file.unlink()

def other():
    return 42
"""
    patched = patch_update_deletion(original)
    namespace = {}
    exec(compile(patched, "<isolated-test>", "exec"), namespace)
    # Original would access a nonexistent directory. Patched function is a no-op on every call.
    namespace["delete_update_files"]()
    namespace["delete_update_files"]()
    assert namespace["other"]() == 42
    assert original.startswith("from pathlib import Path")
    with pytest.raises(ValueError):
        patch_update_deletion('print("no function")')
    fn = next(n for n in ast.parse(patched).body if isinstance(n, ast.FunctionDef))
    assert not any(isinstance(n, ast.Call) for n in ast.walk(fn))


def test_manifest_integrity_and_extra_source_rejected(tmp_path):
    file = tmp_path / "wxauto4/utils/tools.py"
    file.parent.mkdir(parents=True)
    file.write_text(patch_update_deletion("def delete_update_files():\n    pass\n"))
    (tmp_path / MANIFEST).write_text(
        json.dumps(
            {
                "upstream_commit": UPSTREAM_COMMIT,
                "patch": "disable-update-deletion-v1",
                "prepared_sha256": {
                    "wxauto4/utils/tools.py": hashlib.sha256(file.read_bytes()).hexdigest()
                },
            }
        )
    )
    verify(tmp_path)
    extra = tmp_path / "wxauto4/extra.py"
    extra.write_text('print("unexpected")')
    with pytest.raises(ValueError):
        verify(tmp_path)
    extra.unlink()
    file.write_text("changed")
    with pytest.raises(ValueError):
        verify(tmp_path)


def test_diagnostic_identity_is_session_scoped_not_false_stable_identity(tmp_path):
    path = tmp_path / "diagnostics.db"
    first = Diagnostics(path, {"测试群"})
    assert first.capture("测试群", message())
    assert not first.capture("测试群", message())
    assert not first.capture("其他群", message())
    second = Diagnostics(path, {"测试群"})
    assert second.capture("测试群", message())
    assert first.session != second.session
    with second.store.connection() as db:
        rows = db.execute("SELECT payload FROM wxauto4_observations").fetchall()
        assert len(rows) == 2
        data = json.loads(rows[0]["payload"])
        assert data["sent_at"] is None and data["mention_ids"] is None
        assert data["image_path"] is None
        assert data["identity_scope"] == "ui-session-only"
        assert data["sender_is_display_name"] is True
        # Diagnostic capture never creates production audit inbox/tasks.
        assert (
            db.execute("SELECT name FROM sqlite_master WHERE name='wechat_inbox'").fetchone()
            is None
        )
    with pytest.raises(ValueError):
        first.capture("测试群", message(content="different"))


def test_whitelist_window_binding_never_opens_or_sends():
    group = Chat()
    ignored = Chat("其他群")
    wx = SimpleNamespace(GetAllSubWindow=lambda: [group, ignored])
    assert attach_existing(wx, {"测试群"}) == {"测试群": group}
    with pytest.raises(ValueError):
        attach_existing(wx, {"缺少的群"})
    wx.GetAllSubWindow = lambda: [group, Chat()]
    with pytest.raises(ValueError):
        attach_existing(wx, {"测试群"})
    wx.GetAllSubWindow = lambda: [Chat(kind="friend")]
    with pytest.raises(ValueError):
        attach_existing(wx, {"测试群"})


def test_poll_records_burst_and_stops_on_changed_group(tmp_path):
    d = Diagnostics(tmp_path / "d.db", {"测试群"})
    c = Chat(messages=[message(id=str(i)) for i in range(100)])
    d.poll({"测试群": c})
    d.poll({"测试群": c})
    assert d.counts() == 100
    d.sample_ui(lambda: {"foreground_hwnd": 123, "clipboard_sequence": 5})
    with d.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM wxauto4_ui_samples").fetchone()[0] == 1
    c.who = "其他群"
    with pytest.raises(ValueError):
        d.poll({"测试群": c})
    with pytest.raises(ValueError):
        d.poll({})


def test_reject_wildcards_and_missing_ids(tmp_path):
    with pytest.raises(ValueError):
        Diagnostics(tmp_path / "d.db", {"*"})
    d = Diagnostics(tmp_path / "d.db", {"测试群"})
    assert not d.capture("测试群", message(id=None))
    assert d.counts() == 0
