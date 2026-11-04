"""Self-contained Windows application host. No Python/Node setup at end-user launch."""

from __future__ import annotations

import argparse
import html
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class AlreadyRunningError(RuntimeError):
    pass


class InstanceLock:
    def __init__(self, home: Path):
        self.path = home / "desktop.lock"
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0, 2)
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise AlreadyRunningError("嘉惠助手已经在运行，请在任务栏打开现有窗口。") from None
        return self

    def __exit__(self, *args):
        if self.handle:
            self.handle.close()


def default_home() -> Path:
    if os.environ.get("JIAHUI_HOME"):
        return Path(os.environ["JIAHUI_HOME"]).expanduser().resolve()
    return (
        Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".local/share"))) / "JiahuiAssistant"
    ).resolve()


def data_directory(home: Path) -> Path:
    prefs = home / "desktop.json"
    if prefs.exists():
        value = json.loads(prefs.read_text(encoding="utf-8")).get("data_dir")
        if value:
            return Path(value).expanduser().resolve()
    return home / "data"


def app_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "scripts.jiahui_desktop"]


class DesktopRuntime:
    def __init__(self, home: Path, data_dir: Path | None = None):
        self.home = home.resolve()
        self.data_dir = (data_dir or data_directory(home)).resolve()
        self.process = None
        self.output = None
        self.stopped = threading.Event()
        self.bootstrap = secrets.token_urlsafe(32)
        self.url = ""

    def start(self, timeout: float = 150):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "logs").mkdir(exist_ok=True)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update(
            {
                "BUSINESS_PROFILE": "jiahui",
                "JIAHUI_DATA_DIR": str(self.data_dir),
                "JIAHUI_DESKTOP_SETUP": self.bootstrap,
                "PYTHONIOENCODING": "utf-8",
            }
        )
        env.setdefault("HF_HUB_OFFLINE", "1")
        env.setdefault("TRANSFORMERS_OFFLINE", "1")
        # Source runs need the checkout import path; frozen runs carry the modules.
        if not getattr(sys, "frozen", False):
            env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        self.output = (self.home / "logs" / "desktop-server.log").open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            [
                *app_command(),
                "--serve",
                "--home",
                str(self.home),
                "--data-dir",
                str(self.data_dir),
                "--port",
                str(port),
            ],
            cwd=self.home,
            env=env,
            stdout=self.output,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.stopped.is_set():
            if self.process.poll() is not None:
                raise RuntimeError("本地服务启动失败，请查看日志。")
            try:
                with opener.open(self.url + "/api/v1/desktop/status", timeout=2) as response:
                    status = json.load(response)
                    if status.get("desktop"):
                        return self.url + (
                            "/#setup=" + self.bootstrap if status["setup_required"] else "/"
                        )
            except (OSError, ValueError, urllib.error.URLError):
                pass
            self.stopped.wait(0.25)
        raise RuntimeError("服务启动已取消或超时，请查看日志。")

    def stop(self):
        self.stopped.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.output:
            self.output.close()


def webview_runtime_available() -> bool:
    if os.name != "nt":
        return True
    import winreg

    for hive in [winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER]:
        for location in [
            r"SOFTWARE\Microsoft\EdgeUpdate\Clients",
            r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients",
        ]:
            try:
                with winreg.OpenKey(hive, location) as parent:
                    for index in range(winreg.QueryInfoKey(parent)[0]):
                        try:
                            with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as child:
                                name = winreg.QueryValueEx(child, "name")[0]
                                version = winreg.QueryValueEx(child, "pv")[0]
                                if "webview2" in name.lower() and version != "0.0.0.0":
                                    return True
                        except OSError:
                            continue
            except OSError:
                continue
    return False


def ensure_webview_runtime():
    if webview_runtime_available():
        return
    from tkinter import Tk, messagebox

    root = Tk()
    root.withdraw()
    try:
        accepted = messagebox.askyesno(
            "首次准备嘉惠助手",
            "需要安装微软 WebView2 窗口组件（仅首次）。是否现在安装？需要联网，Windows可能请求安装授权。",
            parent=root,
        )
        if not accepted:
            raise RuntimeError("已取消WebView2安装")
        installer = Path(sys.executable).parent / "MicrosoftEdgeWebview2Setup.exe"
        if not installer.is_file():
            raise RuntimeError("缺少微软WebView2安装组件，请完整解压Windows软件包。")
        subprocess.run([str(installer), "/silent", "/install"], check=True, timeout=240)
        if not webview_runtime_available():
            raise RuntimeError("WebView2未准备就绪，请完成微软组件安装后重开软件。")
    finally:
        root.destroy()


def loading_html(
    message: str = "正在启动你的嘉惠工作台", detail: str = "连接本地资料与模型，请稍候…"
) -> str:
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><style>body{{margin:0;height:100vh;display:grid;place-items:center;background:#f6f7f2;color:#163f34;font-family:"Microsoft YaHei",sans-serif}}main{{text-align:center}}b{{display:grid;place-items:center;margin:0 auto 28px;width:76px;height:76px;border-radius:24px;background:#164f40;color:#fff;font-size:35px}}h1{{font-size:25px;font-weight:600}}p{{color:#738178;font-size:14px;max-width:600px;line-height:1.8;overflow-wrap:anywhere}}small{{letter-spacing:3px;color:#8a9b8f}}</style><main><b>嘉</b><small>JIAHUI WORKSPACE</small><h1>{html.escape(message)}</h1><p>{html.escape(detail)}</p></main></html>"""


def run_gui(home: Path):
    ensure_webview_runtime()
    import webview

    webview.settings["ALLOW_DOWNLOADS"] = True
    runtime = DesktopRuntime(home)
    with InstanceLock(home):
        window = webview.create_window(
            "嘉惠 · 内部业务助手",
            html=loading_html(),
            width=1240,
            height=840,
            min_size=(940, 660),
            background_color="#f6f7f2",
            text_select=True,
        )
        window.events.closed += runtime.stop

        def ready():
            try:
                url = runtime.start()
                if not runtime.stopped.is_set():
                    window.load_url(url)
            except Exception:
                runtime.stop()
                if not runtime.stopped.is_set():
                    return
                try:
                    window.load_html(
                        loading_html(
                            "启动未完成",
                            f'请查看 {home / "logs" / "desktop-server.log"}，关闭后可重新打开。',
                        )
                    )
                except Exception:
                    pass

        try:
            webview.start(
                ready,
                gui="edgechromium" if os.name == "nt" else None,
                debug=False,
                private_mode=True,
                storage_path=str(home / "webview"),
            )
        finally:
            runtime.stop()


def run_server(args):
    import uvicorn

    from app.jiahui.desktop_api import create_desktop_app

    home = Path(args.home).resolve()
    data_dir = Path(args.data_dir).resolve() if args.data_dir else data_directory(home)
    app = create_desktop_app(home, data_dir, os.environ.get("JIAHUI_DESKTOP_SETUP", ""))
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        loop="asyncio",
        http="h11",
        ws="none",
        access_log=False,
        log_config=None,
    )


def main():
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = sys.stdout
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--home")
    parser.add_argument("--data-dir")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.serve:
        run_server(args)
    elif args.self_test:
        from scripts.jiahui_desktop_selftest import self_test

        self_test()
    else:
        try:
            run_gui(Path(args.home).resolve() if args.home else default_home())
        except Exception as exc:
            from tkinter import Tk, messagebox

            root = Tk()
            root.withdraw()
            messagebox.showerror(
                "嘉惠助手",
                str(exc)
                if isinstance(exc, RuntimeError | OSError)
                else "软件未能启动，请查看用户数据目录中的日志。",
                parent=root,
            )
            root.destroy()
            raise SystemExit(1) from None


if __name__ == "__main__":
    main()
