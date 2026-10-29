"""Read-only Windows compatibility probe, not a verified WeChat 4.2 adapter.

No clicks, keystrokes, message sending, account hooks, or network requests.
Only the manually opened group is observed. Original image retrieval is untested.
"""

import ctypes
import hashlib
import json
import os
import re
import secrets
import time
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shipment_bot.mention_gate import parse_mention


@dataclass
class Node:
    path: tuple[int, ...]
    kind: str
    name: str
    class_name: str
    automation_id: str
    rect: tuple[int, int, int, int]


def title_matches(actual: str, expected: str) -> bool:
    actual = unicodedata.normalize("NFKC", actual).strip()
    expected = unicodedata.normalize("NFKC", expected).strip()
    return bool(expected) and bool(
        re.fullmatch(re.escape(expected) + r"(?:\s*\(\d+人?\))?", actual)
    )


def is_message_container(node: Node) -> bool:
    marker = (node.class_name + " " + node.automation_id).lower()
    if any(value in marker for value in ("session", "contact", "conversationlist", "navigation")):
        return False
    named = node.name.strip() in {"消息", "消息列表", "聊天消息", "Messages", "Message list"}
    tagged = any(
        value in marker for value in ("messagelist", "msglist", "message_list", "chatmsglist")
    )
    left, top, right, bottom = node.rect
    return (named or tagged) and right - left > 180 and bottom - top > 80


def select_group(nodes: list[Node], group: str) -> Node | None:
    if unicodedata.normalize("NFKC", group).strip().casefold() in {"微信", "wechat", "weixin"}:
        return None  # A generic application caption does not identify a specific chat.
    by_path = {node.path: node for node in nodes}
    candidates = [node for node in nodes if is_message_container(node)]
    if len(candidates) != 1:
        return None
    container = candidates[0]
    for node in nodes:
        if not title_matches(node.name, group):
            continue
        ancestors = [
            by_path[node.path[:depth]]
            for depth in range(len(node.path))
            if node.path[:depth] in by_path
        ]
        if any(
            parent.kind in {"ListControl", "ListItemControl", "TreeControl", "DataGridControl"}
            or any(tag in parent.class_name.lower() for tag in ("session", "contact", "navigation"))
            for parent in ancestors
        ):
            continue
        if node.path == ():
            if any(
                tag in node.class_name.lower()
                for tag in ("chatwindow", "chatmainwindow", "chatwnd")
            ):
                return container
            continue
        left, top, right, bottom = node.rect
        msg_left, msg_top, msg_right, _ = container.rect
        center = (left + right) / 2
        # The header must sit above the message pane, never in the sidebar or in a message row.
        if msg_left <= center <= msg_right and bottom <= msg_top + 30 and top < msg_top:
            return container
    return None


def redacted_report(
    nodes: list[Node], group: str, version: str, selected: Node | None, rows: list[tuple]
) -> dict:
    return {
        "checker_version": "1.0.0",
        "mode": "read_only_visible_group_probe",
        "reported_client_version": "4.2.0.6",
        "observed_client_version": version,
        "group_name_sha256": hashlib.sha256(group.encode()).hexdigest(),
        "group_header_and_message_pane_verified": selected is not None,
        "visible_rows_with_runtime_identity": len(rows),
        "image_file_retrieval": "not_tested",
        "automatic_audit": "not_connected",
        "raw_chat_saved_in_report": False,
        "controls": [
            {
                "path": list(n.path),
                "type": n.kind,
                "class": n.class_name,
                "has_automation_id": bool(n.automation_id),
                "name_characters": len(n.name),
                "rect": list(n.rect),
                "message_pane_candidate": is_message_container(n),
            }
            for n in nodes
        ],
    }


class Seen:
    def __init__(self, maximum: int = 2000):
        self.maximum = maximum
        self.items: OrderedDict[str, None] = OrderedDict()

    def fresh(self, runtime_id: tuple[int, ...], text: str) -> bool:
        if not runtime_id or not text.strip():
            return False
        key = hashlib.sha256(
            json.dumps([runtime_id, text], ensure_ascii=False).encode()
        ).hexdigest()
        if key in self.items:
            self.items.move_to_end(key)
            return False
        self.items[key] = None
        while len(self.items) > self.maximum:
            self.items.popitem(last=False)
        return True


def _get(control, key: str, default=""):
    try:
        return getattr(control, key)
    except Exception:
        return default


def _win_dll(name: str):
    if os.name != "nt":
        raise RuntimeError("Windows native access is available only on the user's Windows PC.")
    # The Linux type-checker omits this Windows-only ctypes member.
    return ctypes.WinDLL(name, use_last_error=True)  # type: ignore[attr-defined]


def snapshot(root) -> tuple[list[Node], dict]:
    nodes: list[Node] = []
    handles = {}
    pending: list[tuple[tuple[int, ...], Any]] = [((), root)]
    while pending and len(nodes) < 700:
        path, control = pending.pop(0)
        rectangle = _get(control, "BoundingRectangle", None)
        rect = (
            int(getattr(rectangle, "left", 0)),
            int(getattr(rectangle, "top", 0)),
            int(getattr(rectangle, "right", 0)),
            int(getattr(rectangle, "bottom", 0)),
        )
        node = Node(
            path,
            str(_get(control, "ControlTypeName")),
            str(_get(control, "Name"))[:10000],
            str(_get(control, "ClassName"))[:200],
            str(_get(control, "AutomationId"))[:500],
            rect,
        )
        nodes.append(node)
        handles[path] = control
        marker = (node.class_name + " " + node.automation_id).lower()
        # Do not traverse message rows or sidebar contacts before the group header is checked.
        if is_message_container(node) or any(
            tag in marker for tag in ("sessionlist", "contactlist", "conversationlist")
        ):
            continue
        if len(path) >= 12:
            continue
        try:
            pending.extend(
                (path + (index,), child) for index, child in enumerate(control.GetChildren())
            )
        except Exception:
            continue
    return nodes, handles


def read_rows(control) -> list[tuple[tuple[int, ...], str]]:
    rows = []
    for child in control.GetChildren()[:100]:
        if _get(child, "ControlTypeName") not in {
            "ListItemControl",
            "DataItemControl",
            "CustomControl",
            "PaneControl",
            "TextControl",
        }:
            continue
        text = str(_get(child, "Name")).strip()
        if not text:
            try:
                text = "\n".join(
                    str(_get(item, "Name"))
                    for item in child.GetChildren()[:30]
                    if _get(item, "Name")
                )
            except Exception:
                continue
        try:
            identity = tuple(child.GetRuntimeId())
        except Exception:
            identity = ()
        if identity and text:
            rows.append((identity, text[:10000]))
    return rows


def foreground_wechat() -> tuple[int, str]:
    from ctypes import wintypes

    user = _win_dll("user32")
    kernel = _win_dll("kernel32")
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    hwnd = user.GetForegroundWindow()
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    process = kernel.OpenProcess(0x1000, False, pid.value)
    if not process:
        raise RuntimeError("无法读取前台程序信息，已停止。请确认微信和检测器在同一用户会话中运行。")
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buffer))
        if not kernel.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            raise RuntimeError("无法确认前台程序是否为微信，已停止。")
        executable = buffer.value
    finally:
        kernel.CloseHandle(process)
    if Path(executable).name.lower() not in {"weixin.exe", "wechat.exe"}:
        raise RuntimeError(
            "倒计时结束时前台不是电脑版微信，未读取其他程序。请重新运行并切到测试群。"
        )
    return int(hwnd), file_version(executable)


def file_version(executable: str) -> str:
    from ctypes import wintypes

    class FixedInfo(ctypes.Structure):
        _fields_ = [
            (name, wintypes.DWORD)
            for name in (
                "signature",
                "structure",
                "file_ms",
                "file_ls",
                "product_ms",
                "product_ls",
                "flags_mask",
                "flags",
                "os",
                "type",
                "subtype",
                "date_ms",
                "date_ls",
            )
        ]

    version = _win_dll("version")
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
    ]
    version.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]
    unused = wintypes.DWORD()
    size = version.GetFileVersionInfoSizeW(executable, ctypes.byref(unused))
    if not size:
        return "unknown"
    buffer = ctypes.create_string_buffer(size)
    pointer = ctypes.c_void_p()
    length = wintypes.UINT()
    if not version.GetFileVersionInfoW(executable, 0, size, buffer) or not version.VerQueryValueW(
        buffer, "\\", ctypes.byref(pointer), ctypes.byref(length)
    ):
        return "unknown"
    fixed = ctypes.cast(pointer, ctypes.POINTER(FixedInfo)).contents
    return ".".join(
        str(value)
        for value in (
            fixed.file_ms >> 16,
            fixed.file_ms & 0xFFFF,
            fixed.file_ls >> 16,
            fixed.file_ls & 0xFFFF,
        )
    )


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("请在安装微信的 Windows 电脑上运行。")
    from ctypes import wintypes

    import uiautomation as auto

    user = _win_dll("user32")
    user.IsWindow.argtypes = [wintypes.HWND]
    user.IsIconic.argtypes = [wintypes.HWND]

    print("微信 4.2 只读兼容性检测：仅检查你手动打开的测试群。")
    print("不发送消息，不调用模型 API；报告只保存控件结构，不保存聊天原文。")
    group = input("输入要检测的测试群完整名称：").strip()
    if not group:
        raise RuntimeError("群名为空，已停止。")
    if unicodedata.normalize("NFKC", group).strip().casefold() in {"微信", "wechat", "weixin"}:
        raise RuntimeError(
            "请输入实际测试群名称，不要填应用标题“微信”。测试群也请使用可区分的名称。"
        )
    nickname = input("输入群里 @ 时显示的助手昵称（默认：审单助手）：").strip() or "审单助手"
    print("请在 8 秒内切到电脑版微信，打开这个测试群；保持窗口展开并将消息滚到底部。", flush=True)
    for seconds in range(8, 0, -1):
        print(f"{seconds}...", flush=True)
        time.sleep(1)
    hwnd, client_version = foreground_wechat()
    root = auto.ControlFromHandle(hwnd)
    if root is None:
        raise RuntimeError("没有取得微信窗口，尚不能确认兼容。")
    nodes, handles = snapshot(root)
    selected = select_group(nodes, group)
    rows = read_rows(handles[selected.path]) if selected else []
    if selected:
        checked_nodes, _ = snapshot(root)
        if select_group(checked_nodes, group) is None:
            selected, rows = None, []
    report = redacted_report(nodes, group, client_version, selected, rows)
    report["new_rows_observed"] = 0
    test_code = secrets.token_hex(3).upper()
    test_phrase = "@" + nickname + " 测试 " + test_code
    report["test_code"] = test_code
    report["test_phrase_seen_in_group_rows"] = False
    report["mention_gate"] = "visible_text_only"
    report["mention_candidates_observed"] = 0
    report_path = Path(__file__).resolve().parents[2] / "logs/wechat-group-compat.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"检测到的微信文件版本：{client_version}")
    print(f"脱敏诊断报告：{report_path}")
    if not selected:
        print("尚未找到可验证的群标题和消息列表，不能认定 4.2.0.6 已兼容。")
        print("把检测窗口输出或上面的脱敏 JSON 报告提供给开发者，继续适配。")
        return
    print(f"找到指定群的消息列表，目前可读取 {len(rows)} 条带运行时标识的界面记录。")
    print("普通聊天记录不显示、不调用 API。仅在本机显示匹配的 @ 指令候选。")
    print("请让一位测试群成员手动 @ 对应微信账号，再发送：测试 " + test_code)
    print("本次期望看到的可见文字：" + test_phrase)
    print("后续审单指令约定：@" + nickname + " 审核 A001")
    print("这里只验证可见文字，不保证完整历史、不提取原图，也未连接审单。按 Ctrl+C 停止。")
    seen = Seen()
    for identity, text in rows:
        seen.fresh(identity, text)
    paused = False
    while True:
        time.sleep(2)
        if not user.IsWindow(hwnd):
            print("微信窗口已关闭，只读观察停止。", flush=True)
            return
        if user.IsIconic(hwnd):
            if not paused:
                print("[暂停] 微信窗口已最小化；请恢复测试群窗口。", flush=True)
                paused = True
            continue
        nodes, handles = snapshot(root)
        selected = select_group(nodes, group)
        if not selected:
            if not paused:
                print(
                    "[暂停] 指定群标题或消息列表不可验证；请切回原测试群，保持窗口展开。",
                    flush=True,
                )
                paused = True
            continue
        if paused:
            # A changed/recreated UI can reuse runtime IDs. Rebaseline instead of replaying history.
            for identity, text in read_rows(handles[selected.path]):
                seen.fresh(identity, text)
            print("[恢复] 已重新建立当前界面基线；暂停期间消息不承诺补齐。", flush=True)
            paused = False
            continue
        current_rows = read_rows(handles[selected.path])
        checked_nodes, _ = snapshot(root)
        if select_group(checked_nodes, group) is None:
            # A manual chat switch during the read is not permission to emit another chat's rows.
            paused = True
            continue
        changed = False
        for identity, text in current_rows:
            if seen.fresh(identity, text):
                report["new_rows_observed"] += 1
                command = parse_mention(text, nickname)
                if command:
                    report["mention_candidates_observed"] += 1
                    print(
                        f"[@ 指令候选，仅本机] {command.action} {command.reference}；未调用 API、未审单、未发群消息。",
                        flush=True,
                    )
                normalized = unicodedata.normalize("NFKC", text)
                if (
                    test_code in normalized
                    and "@" + unicodedata.normalize("NFKC", nickname) in normalized
                ):
                    report["test_phrase_seen_in_group_rows"] = True
                    print(
                        "[文字验证通过] 已从指定群消息区域读到本次测试文字；真实 @ 元数据和图片原图尚未验证。",
                        flush=True,
                    )
                    print("[测试行原文，仅本机] " + repr(text[:800]), flush=True)
                changed = True
        if changed:
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已停止只读观察。")
    except (RuntimeError, OSError) as error:
        print(f"检测未完成：{error}")
        raise SystemExit(1) from None
    except Exception as error:
        print(f"检测未完成（{type(error).__name__}），不能认定当前微信兼容。")
        raise SystemExit(1) from None
