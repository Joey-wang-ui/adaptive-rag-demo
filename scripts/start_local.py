"""Set up and serve the local demo; invoked by start-local.cmd on Windows."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def prerequisites() -> tuple[str, str]:
    # This bootstrap can run before the project's required Python is selected.
    if sys.version_info < (3, 11):  # noqa: UP036
        raise RuntimeError("Python 3.11 or newer is required; Python 3.12 is recommended.")
    node = shutil.which("node")
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not node or not npm:
        raise RuntimeError("Install Node.js 24 LTS, then reopen this launcher.")
    version = subprocess.check_output([node, "--version"], text=True).strip()
    major, minor = (int(part) for part in version.lstrip("v").split(".")[:2])
    if not ((major == 20 and minor >= 19) or (major == 22 and minor >= 12) or major > 22):
        raise RuntimeError(f"Node.js {version} is unsupported. Install Node.js 24 LTS.")
    for name in ("pyproject.toml", "frontend/package-lock.json", "app/asgi.py"):
        if not (ROOT / name).is_file():
            raise RuntimeError(f"Missing {name}. Extract the complete project ZIP first.")
    return node, npm


def setup(npm: str) -> Path:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.setdefault("NPM_CONFIG_CACHE", str(logs / "npm-cache"))
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    marker = logs / "local-setup.json"
    fingerprint = hashlib.sha256(
        (ROOT / "pyproject.toml").read_bytes()
        + (ROOT / "frontend/package.json").read_bytes()
        + (ROOT / "frontend/package-lock.json").read_bytes()
        + sys.version.encode()
    ).hexdigest()
    try:
        installed = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        installed = {}
    if not python.exists():
        print("Creating the project Python environment...", flush=True)
        run([sys.executable, "-m", "venv", str(ROOT / ".venv")], ROOT, env)
        installed = {}
    if installed.get("fingerprint") != fingerprint:
        print("Installing Python dependencies (first run may take several minutes)...", flush=True)
        run([str(python), "-m", "pip", "install", "--upgrade", "pip"], ROOT, env)
        run([str(python), "-m", "pip", "install", "-e", ".[dev]"], ROOT, env)
    if installed.get("fingerprint") != fingerprint or not (ROOT / "frontend/node_modules").is_dir():
        print("Installing locked frontend dependencies...", flush=True)
        run([npm, "ci"], ROOT / "frontend", env)
    print("Building the chat webpage...", flush=True)
    run([npm, "run", "build"], ROOT / "frontend", env)
    marker.write_text(json.dumps({"fingerprint": fingerprint}), encoding="utf-8")
    return python


def available_port() -> int:
    for port in range(8000, 8021):
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("Ports 8000-8020 are busy. Close an older demo before retrying.")


def serve(python: Path) -> None:
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    log_path = logs / "local-server.log"
    port = available_port()
    url = f"http://127.0.0.1:{port}"
    env = os.environ.copy()
    env.update(
        {
            "APP_ENV": "development",
            "LOG_LEVEL": "INFO",
            "LOCAL_EMBEDDING_ENABLED": "false",
            "CROSS_ENCODER_ENABLED": "false",
            "UNSTRUCTURED_ENABLED": "false",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    # Existing .env/credentials are preserved. No local model weights are downloaded.
    with log_path.open("a", encoding="utf-8") as output:
        process = subprocess.Popen(
            [
                str(python),
                "-m",
                "uvicorn",
                "app.asgi:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 90
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Server stopped during startup. See {log_path}")
                try:
                    with opener.open(url + "/api/v1/health", timeout=2) as response:
                        if response.status == 200 and json.load(response).get("status") == "ok":
                            break
                except (OSError, ValueError, urllib.error.URLError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Server did not become ready. See {log_path}")
                time.sleep(0.5)
            print(f"\nReady: {url}", flush=True)
            print("Click 'Agent' in the bottom-right corner of the webpage.", flush=True)
            print("Keep this window open. Press Ctrl+C to stop the server.", flush=True)
            print(f"Server log: {log_path}", flush=True)
            webbrowser.open(url)
            process.wait()
            if process.returncode:
                raise RuntimeError(f"Server exited with code {process.returncode}. See {log_path}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Check prerequisites without installing or starting."
    )
    args = parser.parse_args()
    _, npm = prerequisites()
    print("Python and Node.js checks passed.", flush=True)
    if not args.check:
        serve(setup(npm))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from None
