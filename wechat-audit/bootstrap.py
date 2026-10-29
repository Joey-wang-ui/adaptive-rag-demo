"""Windows-first installer/launcher. Nothing is changed in the existing RAG .env."""

import argparse
import getpass
import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
DATA = ROOT / "data"
RUNTIME = ROOT / ".runtime"
VENV = ROOT / ".venv"
LOGS = REPO / "logs"
VERSION = "2026.10.8"


def python_path() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def node_command() -> list[str]:
    return [shutil.which("node") or "node", str(RUNTIME / "node_modules/openclaw/openclaw.mjs")]


def run(command: list[str], *, env: dict | None = None, cwd: Path = ROOT) -> None:
    # Keys are never included in command arguments. Dependency output has no customer data.
    subprocess.run(command, cwd=cwd, env=env, check=True)


def prepare() -> None:
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not node or not npm:
        raise RuntimeError("Node.js/npm not found. Install Node 24 LTS, reopen CMD, then retry.")
    version = subprocess.check_output([node, "--version"], text=True).strip().lstrip("v")
    parts = tuple(int(x) for x in version.split(".")[:3])
    if not ((parts[0] == 24 and parts >= (24, 22, 1)) or parts >= (26, 12, 1)):
        raise RuntimeError(
            f"Node {version} is not supported. Use Node 24.22.1+ on 24.x, or 26.12.1+."
        )
    DATA.mkdir(parents=True, exist_ok=True)
    LOGS.mkdir(parents=True, exist_ok=True)
    req = ROOT / "requirements.txt"
    fingerprint = hashlib.sha256(req.read_bytes() + sys.version.encode()).hexdigest()
    marker = DATA / "python-setup.sha256"
    if not python_path().is_file():
        print("Creating the bot Python environment...", flush=True)
        run([sys.executable, "-m", "venv", str(VENV)])
    if not marker.is_file() or marker.read_text() != fingerprint:
        print(
            "Installing small OCR and API components (no local language-model weights)...",
            flush=True,
        )
        run([str(python_path()), "-m", "pip", "install", "-r", str(req)])
        marker.write_text(fingerprint)

    package = ROOT / "runtime-package/package.json"
    lock = ROOT / "runtime-package/package-lock.json"
    fingerprint = hashlib.sha256(
        package.read_bytes() + (lock.read_bytes() if lock.exists() else b"")
    ).hexdigest()
    marker = DATA / "node-setup.sha256"
    entry = RUNTIME / "node_modules/openclaw/openclaw.mjs"
    if not entry.is_file() or not marker.is_file() or marker.read_text() != fingerprint:
        print("Installing pinned OpenClaw and the official Tencent Weixin plugin...", flush=True)
        RUNTIME.mkdir(exist_ok=True)
        shutil.copyfile(package, RUNTIME / "package.json")
        if lock.exists():
            shutil.copyfile(lock, RUNTIME / "package-lock.json")
        mode = "ci" if lock.exists() else "install"
        run(
            [
                npm,
                mode,
                "--prefix",
                str(RUNTIME),
                "--cache",
                str(LOGS / "wechat-npm-cache"),
                "--no-audit",
                "--no-fund",
                "--allow-scripts=openclaw",
            ]
        )
        marker.write_text(fingerprint)


def read_key_file(path: Path) -> str:
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == "DEEPSEEK_API_KEY":
            return value.strip().strip('"').strip("'")
    return ""


def api_key() -> str:
    existing = (
        os.environ.get("DEEPSEEK_API_KEY", "")
        or read_key_file(DATA / ".env")
        or read_key_file(REPO / ".env")
    )
    if existing:
        print("Reusing the saved DeepSeek key; its value will not be displayed.", flush=True)
        return existing
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            value = getpass.getpass("Paste DeepSeek API key (hidden), then press Enter: ").strip()
        except getpass.GetPassWarning:
            raise RuntimeError(
                "Open start-wechat-audit.cmd in an interactive CMD window."
            ) from None
    if not value or "\n" in value or "\r" in value:
        raise RuntimeError("No valid key entered. Reopen the launcher and paste your API key.")
    # This is the user's local Windows machine; this ignored folder is never packaged or pushed.
    path = DATA / ".env"
    path.write_text("DEEPSEEK_API_KEY=" + value + "\n", encoding="utf-8")
    path.chmod(0o600)
    print("Saved only on this PC in wechat-audit/data/.env for later starts.", flush=True)
    return value


def free_port(start: int) -> int:
    for port in range(start, start + 30):
        with socket.socket() as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local port found; close an old bot window and retry.")


def configure(key: str) -> tuple[dict, int]:
    profile = DATA / "openclaw"
    workspace = DATA / "workspace"
    profile.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    bridge_port, gateway_port = free_port(18840), free_port(18940)
    config = {
        "gateway": {
            "mode": "local",
            "bind": "loopback",
            "port": gateway_port,
            "auth": {"mode": "token", "token": secrets.token_urlsafe(32)},
        },
        "agents": {"defaults": {"workspace": str(workspace), "heartbeat": {"every": "0m"}}},
        "tools": {"deny": ["*"]},
        "plugins": {
            "allow": ["openclaw-weixin", "jiahui-shipment"],
            "load": {
                "paths": [
                    str(RUNTIME / "node_modules/@tencent-weixin/openclaw-weixin"),
                    str(ROOT / "plugin"),
                ]
            },
            "entries": {"openclaw-weixin": {"enabled": True}, "jiahui-shipment": {"enabled": True}},
        },
    }
    config_path = profile / "openclaw.json"
    if config_path.exists():
        # The official login command may add per-account settings. Preserve them on restart.
        previous = json.loads(config_path.read_text(encoding="utf-8"))
        for name in ("channels", "commands", "session"):
            if name in previous:
                config[name] = previous[name]
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    config_path.chmod(0o600)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "DEEPSEEK_API_KEY": key,
            "OPENCLAW_STATE_DIR": str(profile),
            "OPENCLAW_CONFIG_PATH": str(config_path),
            "SHIPMENT_DATA_DIR": str(DATA),
            "SHIPMENT_MEDIA_ROOTS": os.pathsep.join(
                [str(profile / "media"), str(workspace / "media")]
            ),
            "SHIPMENT_BRIDGE_PORT": str(bridge_port),
            "SHIPMENT_BRIDGE_URL": f"http://127.0.0.1:{bridge_port}",
            "SHIPMENT_BRIDGE_TOKEN": secrets.token_urlsafe(32),
        }
    )
    return env, gateway_port


def plugin_registered(output: str) -> bool:
    # Native-support diagnostics may precede --json on the first Windows run.
    decoder = json.JSONDecoder()
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            result, _ = decoder.raw_decode(output[index:])
        except (ValueError, TypeError):
            continue
        if not isinstance(result, dict) or result.get("plugin", {}).get("id") != "jiahui-shipment":
            continue
        return (
            result.get("plugin", {}).get("status") == "loaded"
            and any(hook.get("name") == "inbound_claim" for hook in result.get("typedHooks", []))
            and not any(item.get("severity") == "error" for item in result.get("diagnostics", []))
        )
    return False


def has_wechat_account() -> bool:
    index = DATA / "openclaw/openclaw-weixin/accounts.json"
    try:
        accounts = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(accounts, list) and any(isinstance(item, str) and item for item in accounts)


def wait_bridge(port: str, process: subprocess.Popen) -> None:
    # Ignore HTTP proxy settings for this one authenticated loopback service.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(120):
        if process.poll() is not None:
            raise RuntimeError("OCR service stopped. See logs/wechat-audit.log.")
        try:
            with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                data = json.load(response)
            if data.get("ok") is True and data.get("service") == "jiahui-shipment":
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise RuntimeError("OCR service did not become ready. See logs/wechat-audit.log.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true", help="Force a new Weixin QR login")
    parser.add_argument(
        "--pair",
        nargs="?",
        const="",
        metavar="CODE",
        help="List pairing requests, or approve an exact code",
    )
    args = parser.parse_args()
    LOGS.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(REPO))
    from scripts.local_lock import file_lock

    with file_lock(LOGS / "wechat-audit.lock"):
        print("[1/4] Preparing the private WeChat shipment-review environment...", flush=True)
        prepare()
        env, port = configure(api_key())
        if args.pair is not None:
            command = [
                *node_command(),
                "pairing",
                "approve" if args.pair else "list",
                "openclaw-weixin",
            ]
            if args.pair:
                command.append(args.pair)
            run(command, env=env)
            return
        print("[2/4] Checking local OCR and one real API generation...", flush=True)
        run([str(python_path()), "-m", "shipment_bot.check"], env=env)
        print("[3/4] Verifying the real OpenClaw plugin registration...", flush=True)
        inspected = subprocess.run(
            [*node_command(), "plugins", "inspect", "jiahui-shipment", "--runtime", "--json"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        # Detailed metadata can contain local state paths; keep it local and never print tokens.
        (LOGS / "wechat-plugin-check.log").write_text(
            inspected.stdout + inspected.stderr, encoding="utf-8"
        )
        if inspected.returncode or not plugin_registered(inspected.stdout):
            raise RuntimeError(
                "Shipment hook was not registered. See logs/wechat-plugin-check.log."
            )
        # Login is remembered by the official plugin. Do not inspect or print its bot tokens.
        if args.login or not has_wechat_account():
            print("Scan the official WeChat QR code below and confirm on your phone.", flush=True)
            run([*node_command(), "channels", "login", "--channel", "openclaw-weixin"], env=env)
            if not has_wechat_account():
                raise RuntimeError(
                    "The QR command ended without a saved WeChat binding. Reopen and scan again."
                )
        server = None
        gateway = None
        try:
            with (LOGS / "wechat-audit.log").open("a", encoding="utf-8") as log:
                server = subprocess.Popen(
                    [str(python_path()), "-m", "shipment_bot.server"],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                )
                wait_bridge(env["SHIPMENT_BRIDGE_PORT"], server)
                print(
                    "[4/4] Starting WeChat. Keep this window open. Ctrl+C stops only this bot.",
                    flush=True,
                )
                print(
                    "Send '帮助' in the new WeChat private chat to verify real message delivery.",
                    flush=True,
                )
                print(
                    "Audit records: wechat-audit/data/audit.sqlite3 (local, not uploaded to GitHub).",
                    flush=True,
                )
                gateway = subprocess.Popen(
                    [*node_command(), "gateway", "run", "--port", str(port)], cwd=ROOT, env=env
                )
                while gateway.poll() is None:
                    if server.poll() is not None:
                        raise RuntimeError("OCR service stopped. See logs/wechat-audit.log.")
                    time.sleep(0.5)
                if gateway.returncode:
                    raise RuntimeError(
                        "WeChat gateway stopped; keep its output for troubleshooting."
                    )
        finally:
            for process in (gateway, server):
                if process and process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Reopen start-wechat-audit.cmd to start again.")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        # Exception strings from HTTP clients/configuration must never expose credentials.
        if isinstance(error, RuntimeError):
            print(f"ERROR: {error}", flush=True)
        else:
            print(
                f"Setup did not finish ({type(error).__name__}). Keep the output above.", flush=True
            )
        raise SystemExit(1) from None
