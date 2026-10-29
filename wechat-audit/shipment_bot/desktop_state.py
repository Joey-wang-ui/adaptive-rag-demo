"""Desktop configuration and owned worker lifecycle; no WeChat or API calls."""

import codecs
import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit


def data_directory():
    if getattr(sys, "frozen", False):
        return Path(os.environ["LOCALAPPDATA"]) / "JiahuiGroupAudit"
    return Path(__file__).resolve().parents[1] / "data/group-audit"


def save_settings(
    directory, group, nickname, key, remember, llm=None, audit=None, allow_self_test=False
):
    group, nickname = group.strip(), nickname.strip()
    if not group or not nickname or any("\n" in x or "\r" in x for x in (group, nickname)):
        raise ValueError("请填写完整的群名和群昵称，不能包含换行。")
    if llm is not None:
        url = urlsplit(llm["base_url"].strip())
        if (
            not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.scheme not in {"https", "http"}
            or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})
        ):
            raise ValueError("请填写 HTTPS 接口地址（本机可用 HTTP），密钥只填在 Key 栏。")
        if not llm["model"].strip():
            raise ValueError("模型名称不能为空。")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "config.json"
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    old_endpoint = (
        config.get("llm", {}).get("base_url", "https://api.deepseek.com").strip().rstrip("/")
    )
    if llm is not None and llm["base_url"].strip().rstrip("/") != old_endpoint:
        # Never transfer a saved credential to a newly configured provider.
        key, remember = "", False
    if (config.get("group_name"), config.get("bot_nickname")) != (group, nickname):
        config.pop("binding", None)
    config.update(group_name=group, bot_nickname=nickname)
    config["allow_self_test"] = bool(allow_self_test)
    if llm is not None:
        config["llm"] = {"base_url": llm["base_url"].strip(), "model": llm["model"].strip()}
    if audit is not None:
        config["audit"] = {
            "instructions": str(audit.get("instructions", ""))[:3000],
            "skill": str(audit.get("skill", config.get("audit", {}).get("skill", "")))[:20000],
            "require_customer": bool(audit.get("require_customer", False)),
            "require_specification": bool(audit.get("require_specification", False)),
        }
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    key_path = directory / "api-key.txt"
    if remember and key.strip():
        key_path.write_text(key.strip(), encoding="utf-8")
    elif not remember:
        key_path.unlink(missing_ok=True)
    return config


class Controller:
    def __init__(self, directory, root, *, hotkey_factory=None, worker_factory=None):
        self.directory, self.root = directory, root
        self.process = None
        self.hotkey = None
        self.hotkey_factory = hotkey_factory
        self.worker_factory = worker_factory
        self.mode = None
        self.stopping = False
        self.state = "idle"
        self.logfile = None
        self.statusfile = None
        self.offset = 0
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    @property
    def busy(self):
        return self.process is not None

    def start(self, mode, key=""):
        if self.busy:
            raise ValueError("已有检测或审单任务正在运行，请先停止。")
        if mode not in {"check", "run"}:
            raise ValueError("未知运行模式。")
        config = json.loads((self.directory / "config.json").read_text(encoding="utf-8"))
        if mode == "run" and not config.get("binding"):
            raise ValueError("请先点击“检测连接”，检查显示的账号和群是否正确。")
        if mode == "run" and not key.strip() and not (self.directory / "api-key.txt").exists():
            raise ValueError("请填写模型 API Key。")
        logs = self.directory / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        self.logfile = logs / (token + ".log")
        self.statusfile = logs / (token + ".json")
        self.offset = 0
        self.decoder.reset()
        env = os.environ.copy()
        # Only this worker receives the transient key, never command arguments.
        if key.strip():
            env["DEEPSEEK_API_KEY"] = key.strip()
        env.update(
            JIAHUI_AUDIT_DATA_DIR=str(self.directory),
            JIAHUI_AUDIT_STATUS=str(self.statusfile),
            JIAHUI_AUDIT_LOG=str(self.logfile),
            JIAHUI_AUDIT_NO_PROMPT="1",
            PYTHONIOENCODING="utf-8",
            PYTHONUNBUFFERED="1",
            PYINSTALLER_RESET_ENVIRONMENT="1",
        )
        interpreter = sys.executable
        if Path(interpreter).name.lower() == "pythonw.exe":
            interpreter = str(Path(interpreter).with_name("python.exe"))
        command = (
            [sys.executable, "--worker", mode]
            if getattr(sys, "frozen", False)
            else [interpreter, str(self.root / "group_launch.py"), mode]
        )
        from shipment_bot.windows_safety import GlobalEscape, WindowsWorker

        factory = self.hotkey_factory or (GlobalEscape if os.name == "nt" else None)
        try:
            if factory:
                self.hotkey = factory()
                self.hotkey.start()  # must succeed BEFORE any automation can run
            with self.logfile.open("ab") as output:
                if self.worker_factory or os.name == "nt":
                    self.process = (self.worker_factory or WindowsWorker)(
                        command, self.root, env, output
                    )
                else:
                    self.process = subprocess.Popen(
                        command,
                        cwd=self.root,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
        except Exception:
            self._release_hotkey()
            self.state = "error"
            raise
        self.mode, self.stopping, self.state = mode, False, "starting"

    def _release_hotkey(self):
        if self.hotkey:
            self.hotkey.close()
            self.hotkey = None

    def stop(self):
        if self.process is None or self.stopping:
            return
        self.stopping, self.state = True, "stopping"
        try:
            if hasattr(self.process, "stop_tree"):
                self.process.stop_tree()
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except OSError:
            self.stopping = False  # allow a manual retry; never claim stopped
            raise

    def close(self):
        """Last-resort cleanup, including abnormal UI exit (Job close kills tree)."""
        try:
            if self.process:
                if hasattr(self.process, "close"):
                    self.process.close()
                else:
                    self.stop()
                self.process = None
        finally:
            self._release_hotkey()

    def poll(self):
        if self.hotkey and self.hotkey.pressed.is_set():
            self.stop()
        output = ""
        if self.logfile and self.logfile.exists():
            with self.logfile.open("rb") as file:
                file.seek(self.offset)
                data = file.read()
                self.offset += len(data)
            output = self.decoder.decode(data)
        status = {}
        if self.statusfile and self.statusfile.exists():
            try:
                status = json.loads(self.statusfile.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                pass
        if self.process is not None:
            code = self.process.poll()
            if code is not None:
                if hasattr(self.process, "tree_alive") and self.process.tree_alive():
                    # A worker that exits unexpectedly must not leave automation behind.
                    self.process.stop_tree()
                    return output, status
                if hasattr(self.process, "close"):
                    self.process.close()
                self._release_hotkey()
                self.state = (
                    "stopped"
                    if self.stopping
                    else "checked"
                    if self.mode == "check" and code == 0 and status.get("state") == "checked"
                    else "error"
                )
                self.process = None
            elif not self.stopping and status.get("state") == "running":
                self.state = "running"
        return output, status
