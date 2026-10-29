"""Desktop configuration and owned worker lifecycle; no WeChat or API calls."""

import codecs
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path


def data_directory():
    if getattr(sys, "frozen", False):
        return Path(os.environ["LOCALAPPDATA"]) / "JiahuiGroupAudit"
    return Path(__file__).resolve().parents[1] / "data/group-audit"


def save_settings(directory, group, nickname, key, remember):
    group, nickname = group.strip(), nickname.strip()
    if not group or not nickname or any("\n" in x or "\r" in x for x in (group, nickname)):
        raise ValueError("请填写完整的群名和群昵称，不能包含换行。")
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "config.json"
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if (config.get("group_name"), config.get("bot_nickname")) != (group, nickname):
        config.pop("binding", None)
    config.update(group_name=group, bot_nickname=nickname)
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    key_path = directory / "api-key.txt"
    if remember and key.strip():
        key_path.write_text(key.strip(), encoding="utf-8")
    elif not remember:
        key_path.unlink(missing_ok=True)
    return config


class Controller:
    def __init__(self, directory, root):
        self.directory, self.root = directory, root
        self.process = None
        self.mode = None
        self.stopping = False
        self.state = "idle"
        self.logfile = None
        self.statusfile = None
        self.offset = 0
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    @property
    def busy(self):
        return self.process is not None and self.process.poll() is None

    def start(self, mode, key=""):
        if self.busy:
            raise ValueError("已有检测或审单任务正在运行，请先停止。")
        if mode not in {"check", "run"}:
            raise ValueError("未知运行模式。")
        config = json.loads((self.directory / "config.json").read_text(encoding="utf-8"))
        if mode == "run" and not config.get("binding"):
            raise ValueError("请先点击“检测连接”，检查显示的账号和群是否正确。")
        if mode == "run" and not key.strip() and not (self.directory / "api-key.txt").exists():
            raise ValueError("请填写 DeepSeek API Key。")
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
        with self.logfile.open("ab") as output:
            self.process = subprocess.Popen(
                command,
                cwd=self.root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        self.mode, self.stopping, self.state = mode, False, "starting"

    def stop(self):
        if not self.busy:
            return
        self.stopping, self.state = True, "stopping"
        if os.name == "nt":
            # Kill only our owned worker and its installer/runtime children.
            subprocess.Popen(
                ["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            self.process.terminate()

    def poll(self):
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
