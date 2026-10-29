"""Windows-only global stop key and kernel-owned process tree.

Imports are lazy so lifecycle tests can run without Windows/WeChat.
"""

import ctypes
import subprocess
import threading
from ctypes import wintypes


class GlobalEscape:
    """Register on a dedicated message thread, independent of Tk focus/state."""

    def __init__(self):
        self.pressed = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.thread = None
        self.thread_id = None

    def start(self):
        self.thread = threading.Thread(target=self._listen, daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            self.close()
            raise OSError("全局 Esc 急停注册超时，已阻止启动。")
        if self.error:
            self.close()
            raise OSError(f"无法注册全局 Esc 急停，已阻止启动：{self.error}")

    def _listen(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.thread_id = kernel.GetCurrentThreadId()
        message = wintypes.MSG()
        # Create the queue before exposing readiness/PostThreadMessage.
        user.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        registered = False
        try:
            if not user.RegisterHotKey(None, 1, 0x4000, 0x1B):  # MOD_NOREPEAT, VK_ESCAPE
                raise ctypes.WinError(ctypes.get_last_error())
            registered = True
            self.ready.set()
            while True:
                result = user.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    self.error = "Windows 热键消息循环失效"
                    self.pressed.set()  # fail closed if monitoring is lost
                    break
                if message.message == 0x0312 and message.wParam == 1:
                    self.pressed.set()
        except Exception as error:
            self.error = str(error)
            if registered:
                self.pressed.set()
        finally:
            if registered:
                user.UnregisterHotKey(None, 1)
            self.ready.set()

    def close(self):
        if self.thread and self.thread.is_alive():
            # start() normally returns only after the message queue exists.
            if self.thread_id:
                ctypes.WinDLL("user32", use_last_error=True).PostThreadMessageW(
                    self.thread_id,
                    0x0012,
                    0,
                    0,  # WM_QUIT
                )
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise OSError("全局 Esc 监听未能释放；请勿重新启动任务。")


class WindowsWorker:
    """Suspend before assigning to a kill-on-close Job; descendants cannot escape."""

    def __init__(self, command, cwd, env, output):
        from ctypes import wintypes as w

        class Startup(ctypes.Structure):
            _fields_ = [
                ("cb", w.DWORD),
                ("reserved", w.LPWSTR),
                ("desktop", w.LPWSTR),
                ("title", w.LPWSTR),
                ("x", w.DWORD),
                ("y", w.DWORD),
                ("width", w.DWORD),
                ("height", w.DWORD),
                ("chars_x", w.DWORD),
                ("chars_y", w.DWORD),
                ("fill", w.DWORD),
                ("flags", w.DWORD),
                ("show", w.WORD),
                ("reserved_size", w.WORD),
                ("reserved_bytes", ctypes.POINTER(ctypes.c_byte)),
                ("stdin", w.HANDLE),
                ("stdout", w.HANDLE),
                ("stderr", w.HANDLE),
            ]

        class Process(ctypes.Structure):
            _fields_ = [
                ("process", w.HANDLE),
                ("thread", w.HANDLE),
                ("pid", w.DWORD),
                ("tid", w.DWORD),
            ]

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", w.DWORD),
                ("min_working", ctypes.c_size_t),
                ("max_working", ctypes.c_size_t),
                ("active_limit", w.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", w.DWORD),
                ("scheduling", w.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        k = self.kernel
        # Explicit prototypes prevent HANDLE truncation on 64-bit Windows.
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, w.LPCWSTR], w.HANDLE),
            "SetInformationJobObject": ([w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD], w.BOOL),
            "QueryInformationJobObject": (
                [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p],
                w.BOOL,
            ),
            "AssignProcessToJobObject": ([w.HANDLE, w.HANDLE], w.BOOL),
            "TerminateJobObject": ([w.HANDLE, w.UINT], w.BOOL),
            "CloseHandle": ([w.HANDLE], w.BOOL),
            "TerminateProcess": ([w.HANDLE, w.UINT], w.BOOL),
            "ResumeThread": ([w.HANDLE], w.DWORD),
            "WaitForSingleObject": ([w.HANDLE, w.DWORD], w.DWORD),
            "GetExitCodeProcess": ([w.HANDLE, ctypes.POINTER(w.DWORD)], w.BOOL),
            "CreateProcessW": (
                [
                    w.LPCWSTR,
                    w.LPWSTR,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    w.BOOL,
                    w.DWORD,
                    ctypes.c_void_p,
                    w.LPCWSTR,
                    ctypes.POINTER(Startup),
                    ctypes.POINTER(Process),
                ],
                w.BOOL,
            ),
        }
        for name, (args, result) in signatures.items():
            function = getattr(k, name)
            function.argtypes, function.restype = args, result
        self.handle = None
        self.job = k.CreateJobObjectW(None, None)
        if not self.job:
            raise ctypes.WinError(ctypes.get_last_error())
        process = Process()
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            self._check(
                k.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits))
            )
            startup = Startup()
            startup.cb = ctypes.sizeof(startup)
            # Only log handles are inherited, via the worker's own explicit file open.
            block = ctypes.create_unicode_buffer(
                "\0".join(f"{key}={value}" for key, value in sorted(env.items())) + "\0\0"
            )
            self._check(
                k.CreateProcessW(
                    None,
                    ctypes.create_unicode_buffer(subprocess.list2cmdline(command)),
                    None,
                    None,
                    False,
                    0x4 | 0x08000000 | 0x400,  # SUSPENDED | NO_WINDOW | UNICODE_ENVIRONMENT
                    block,
                    str(cwd),
                    ctypes.byref(startup),
                    ctypes.byref(process),
                )
            )
            self._check(k.AssignProcessToJobObject(self.job, process.process))
            if k.ResumeThread(process.thread) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle, self.pid = process.process, process.pid
        except Exception:
            if process.process:
                k.TerminateProcess(process.process, 1)
                k.CloseHandle(process.process)
            k.CloseHandle(self.job)
            self.job = None
            raise
        finally:
            if process.thread:
                k.CloseHandle(process.thread)

    @staticmethod
    def _check(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def poll(self):
        result = self.kernel.WaitForSingleObject(self.handle, 0)
        if result == 258:  # WAIT_TIMEOUT
            return None
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        self._check(self.kernel.GetExitCodeProcess(self.handle, ctypes.byref(code)))
        return code.value

    def stop_tree(self):
        if self.job:
            self._check(self.kernel.TerminateJobObject(self.job, 1))

    def tree_alive(self):
        class Accounting(ctypes.Structure):
            _fields_ = [
                ("times", ctypes.c_int64 * 4),
                ("faults", wintypes.DWORD),
                ("total", wintypes.DWORD),
                ("active", wintypes.DWORD),
                ("terminated", wintypes.DWORD),
            ]

        info = Accounting()
        self._check(
            self.kernel.QueryInformationJobObject(
                self.job, 1, ctypes.byref(info), ctypes.sizeof(info), None
            )
        )
        return bool(info.active)

    def close(self):
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
