"""Run real Win32 lifecycle tests in the existing Windows packaging job."""

import ctypes
import os
import subprocess
import sys
import time

import pytest
from shipment_bot.windows_safety import GlobalEscape, WindowsWorker

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires real Windows kernel/user32")


def wait_until(predicate):
    deadline = time.monotonic() + 10
    while not predicate():
        assert time.monotonic() < deadline, "Windows lifecycle deadline exceeded"
        time.sleep(0.02)


def test_hotkey_exclusive_registration_release_and_repeat():
    for _ in range(2):
        key = GlobalEscape()
        key.start()
        try:
            other = GlobalEscape()
            with pytest.raises(OSError, match="Esc"):
                other.start()
            # Message routing happens on its own thread, without any Tk mainloop/focus.
            ctypes.windll.user32.PostThreadMessageW(key.thread_id, 0x0312, 1, 0x1B0000)
            assert key.pressed.wait(2)
        finally:
            key.close()
        assert not key.thread.is_alive()


def test_job_kills_child_after_parent_exits_and_preserves_unrelated_process(tmp_path):
    heartbeat = tmp_path / "heartbeat"
    child_code = (
        "import time; from pathlib import Path; "
        f"p=Path({str(heartbeat)!r}); "
        "exec('while True:\\n p.write_text(str(time.time_ns()))\\n time.sleep(0.02)')"
    )
    worker_code = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child_code!r}])"
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    process = None
    try:
        with (tmp_path / "log").open("ab") as output:
            process = WindowsWorker(
                [sys.executable, "-c", worker_code], tmp_path, os.environ.copy(), output
            )
        wait_until(lambda: heartbeat.exists() and process.poll() is not None)
        assert process.tree_alive()
        process.stop_tree()
        wait_until(lambda: not process.tree_alive())
        previous = heartbeat.read_text()
        time.sleep(0.1)
        assert heartbeat.read_text() == previous  # all owned input/automation loops are dead
        assert unrelated.poll() is None
    finally:
        if process:
            process.close()
        unrelated.kill()
        unrelated.wait()


def test_job_close_kills_live_tree(tmp_path):
    with (tmp_path / "log").open("ab") as output:
        process = WindowsWorker(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            tmp_path,
            os.environ.copy(),
            output,
        )
    assert process.poll() is None
    # Retain a separate wait handle so closing our wrapper cannot fake this assertion.
    kernel = process.kernel
    kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
    kernel.OpenProcess.restype = ctypes.c_void_p
    wait_handle = kernel.OpenProcess(0x100000, False, process.pid)  # SYNCHRONIZE
    assert wait_handle
    try:
        process.close()
        assert kernel.WaitForSingleObject(wait_handle, 5000) == 0
        assert process.job is None and process.handle is None
    finally:
        kernel.CloseHandle(wait_handle)


def test_global_escape_with_minimized_ui_and_foreign_foreground(tmp_path):
    """Use real key input; no WeChat, API or message sending."""
    import tkinter as tk

    from shipment_bot.desktop_state import Controller, save_settings

    root = tk.Tk()
    foreign = None
    controller = None
    key = GlobalEscape()
    try:
        root.update()
        root.iconify()
        root.update()
        window_file = tmp_path / "foreground.txt"
        foreign_code = (
            "import tkinter as tk; from pathlib import Path; "
            "r=tk.Tk(); r.title('Unrelated foreground test window'); r.update(); "
            f"Path({str(window_file)!r}).write_text(str(r.winfo_id())); r.mainloop()"
        )
        foreign = subprocess.Popen([sys.executable, "-c", foreign_code])
        wait_until(window_file.exists)
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        user.GetAncestor.restype = ctypes.c_void_p
        user.GetForegroundWindow.restype = ctypes.c_void_p
        user.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        hwnd = user.GetAncestor(int(window_file.read_text()), 2)  # GA_ROOT
        user.SetForegroundWindow(hwnd)
        wait_until(lambda: user.GetForegroundWindow() == hwnd)
        save_settings(tmp_path, "test", "bot", "", False)
        (tmp_path / "group_launch.py").write_text("import time; time.sleep(60)")
        controller = Controller(tmp_path, tmp_path, hotkey_factory=lambda: key)
        controller.start("check")
        user.keybd_event(0x1B, 0, 0, 0)
        user.keybd_event(0x1B, 0, 2, 0)  # KEYEVENTF_KEYUP
        assert key.pressed.wait(3), "global Esc did not arrive with foreign foreground"
        controller.poll()

        def stopped():
            controller.poll()
            return not controller.busy

        wait_until(stopped)
        assert controller.state == "stopped"
        assert root.winfo_exists()  # emergency stop never closes the UI
        assert foreign.poll() is None  # nor the foreground application
        assert not key.thread.is_alive()
        assert controller.process is None
    finally:
        if controller:
            controller.close()
        if foreign:
            foreign.kill()
            foreign.wait()
        root.destroy()
