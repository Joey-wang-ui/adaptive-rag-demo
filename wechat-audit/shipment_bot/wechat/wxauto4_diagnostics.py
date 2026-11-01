"""Passive wxauto4 experiment. Observations never enter the production audit inbox."""

import argparse
import importlib
import json
import logging
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from shipment_bot.store import Store
from shipment_bot.wechat.wxauto4_prepare import UPSTREAM_COMMIT, verify


def timestamp():
    return datetime.now(UTC).isoformat()


class Diagnostics:
    def __init__(self, path: Path, groups: set[str]):
        if not groups or any(not g.strip() or "*" in g for g in groups):
            raise ValueError("exact nonempty group whitelist required")
        self.groups = frozenset(groups)
        self.store = Store(path)
        self.session = uuid.uuid4().hex
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS wxauto4_sessions (
                    session_id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
                    upstream_commit TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wxauto4_observations (
                    session_id TEXT NOT NULL, group_label TEXT NOT NULL,
                    runtime_id TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY(session_id, group_label, runtime_id)
                );
                CREATE TABLE IF NOT EXISTS wxauto4_ui_samples (
                    session_id TEXT NOT NULL, observed_at TEXT NOT NULL, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wxauto4_probe_events (
                    session_id TEXT NOT NULL, observed_at TEXT NOT NULL, code TEXT NOT NULL
                );
            """)
            db.execute(
                "INSERT INTO wxauto4_sessions VALUES (?,?,?)",
                (self.session, timestamp(), UPSTREAM_COMMIT),
            )

    def event(self, code):
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO wxauto4_probe_events VALUES (?,?,?)", (self.session, timestamp(), code)
            )

    def capture(self, group_label, message):
        if group_label not in self.groups:
            return False
        raw_id = getattr(message, "id", None)
        if not isinstance(raw_id, str | tuple | list | int) or not raw_id:
            self.event("missing_runtime_id")
            return False
        runtime_id = json.dumps(raw_id, ensure_ascii=False)
        payload = {}
        for field in ("sender", "sender_remark", "content", "type", "attr"):
            value = getattr(message, field, None)
            if value is not None and not isinstance(value, str):
                raise ValueError("unexpected diagnostic field type")
            payload[field] = value
        payload.update(
            {
                "group_label": group_label,
                "runtime_id": raw_id,
                "sender_is_display_name": True,
                "sent_at": None,
                "mention_ids": None,
                "image_path": None,
                "identity_scope": "ui-session-only",
            }
        )
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded) > 25000:
            self.event("oversized_message")
            return False
        with self.store.connection() as db:
            old = db.execute(
                "SELECT payload FROM wxauto4_observations WHERE "
                "session_id=? AND group_label=? AND runtime_id=?",
                (self.session, group_label, runtime_id),
            ).fetchone()
            if old:
                if old["payload"] != encoded:
                    raise ValueError("UI runtime ID reused with conflicting contents")
                return False
            db.execute(
                "INSERT INTO wxauto4_observations VALUES (?,?,?,?,?)",
                (self.session, group_label, runtime_id, timestamp(), encoded),
            )
        return True

    def poll(self, bindings):
        if set(bindings) != self.groups:
            raise ValueError("bindings differ from whitelist")
        for label, chat in bindings.items():
            info = chat.ChatInfo()
            if info.get("chat_type") != "group" or info.get("chat_name") != label:
                raise ValueError("group window identity changed; stop experiment")
            for message in chat.GetNewMessage():
                self.capture(label, message)

    def sample_ui(self, sampler):
        state = sampler()
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO wxauto4_ui_samples VALUES (?,?,?)",
                (self.session, timestamp(), json.dumps(state)),
            )

    def counts(self):
        with self.store.connection() as db:
            return db.execute(
                "SELECT COUNT(*) FROM wxauto4_observations WHERE session_id=?", (self.session,)
            ).fetchone()[0]


def load_reviewed_sdk(bundle: Path):
    verify(bundle)
    if os.name != "nt":
        raise RuntimeError("live experiment requires Windows; no SDK was imported")
    if any(n == "wxauto4" or n.startswith("wxauto4.") for n in sys.modules):
        raise RuntimeError("use a fresh process; refusing an already imported SDK")
    sys.path.insert(0, str(bundle.resolve()))
    sdk = importlib.import_module("wxauto4")
    if not Path(sdk.__file__).resolve().is_relative_to((bundle / "wxauto4").resolve()):
        raise RuntimeError("unexpected SDK import origin")
    sdk.WxParam.ENABLE_FILE_LOGGER = False
    sdk.WxParam.ENABLE_SENDER_OCR = False
    # Avoid hashing identical visible messages into a false stable identity.
    sdk.WxParam.MESSAGE_HASH = False
    logging.getLogger("wxauto4").disabled = True
    return sdk


def attach_existing(wx, groups):
    # Do not call AddListenChat/ChatWith/Show/KeepRunning: never open or switch chats.
    bindings = {}
    for chat in wx.GetAllSubWindow():
        if chat.who not in groups:
            continue
        if chat.who in bindings:
            raise ValueError("duplicate group window names; cannot identify a unique target")
        if chat.ChatInfo().get("chat_type") != "group":
            raise ValueError("whitelist label resolves to a non-group window")
        bindings[chat.who] = chat
    if set(bindings) != set(groups):
        raise ValueError("manually open each authorized group in its own window first")
    return bindings


def main():
    parser = argparse.ArgumentParser(description="wxauto4 只读诊断；不进入审核、不下载图片、不发送")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--group", action="append", required=True)
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument(
        "--live", action="store_true", help="仅在 Windows 授权测试机实际读取已打开的群窗口"
    )
    args = parser.parse_args()
    verify(args.bundle)
    if args.seconds <= 0 or not 0.5 <= args.interval <= 10:
        parser.error("seconds must be positive; interval must be 0.5..10")
    if not args.live:
        print("副本校验通过。尚未导入 SDK；只有 --live 才连接 Windows 微信。")
        return
    diagnostics = Diagnostics(args.database, set(args.group))
    sdk = load_reviewed_sdk(args.bundle)
    wx = sdk.WeChat(start_listener=False)
    bindings = attach_existing(wx, diagnostics.groups)
    diagnostics.event("attached_existing_windows")
    print("诊断已启动；仅记录本地接收时间/显示名/UI ID。请手动进行最小化和 VS Code 输入测试。")

    def ui_state():
        import ctypes

        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = ctypes.c_void_p
        return {
            "foreground_hwnd": user32.GetForegroundWindow(),
            "clipboard_sequence": user32.GetClipboardSequenceNumber(),
            "group_minimized": {
                label: bool(user32.IsIconic(ctypes.c_void_p(chat._api.control.NativeWindowHandle)))
                for label, chat in bindings.items()
            },
        }

    deadline = time.monotonic() + args.seconds
    try:
        while time.monotonic() < deadline:
            diagnostics.sample_ui(ui_state)
            try:
                diagnostics.poll(bindings)
            finally:
                diagnostics.sample_ui(ui_state)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        diagnostics.event("interrupted")
    except Exception:
        diagnostics.event("failed")
        raise
    finally:
        # No SDK listener was started; no StopListening/Close calls that could alter windows.
        diagnostics.event("stopped")
        print(f"本次记录 {diagnostics.counts()} 条观测；发送和审核始终关闭。")


if __name__ == "__main__":
    main()
