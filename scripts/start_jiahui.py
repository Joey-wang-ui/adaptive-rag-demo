"""Start the isolated Jiahui profile without changing the e-commerce launcher."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

# Support both ``python scripts/start_jiahui.py`` and ``python -m scripts.start_jiahui``.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.start_local import available_port, prerequisites  # noqa: E402


def setup(npm: str) -> Path:
    """Refresh only changed dependencies, using the repository's frozen lock."""
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    env = os.environ.copy()
    env.setdefault("NPM_CONFIG_CACHE", str(logs / "npm-cache"))
    python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    marker = logs / "jiahui-setup.json"
    inputs = [ROOT / "pyproject.toml", ROOT / "uv.lock", ROOT / "frontend/package-lock.json"]
    fingerprint = hashlib.sha256(
        b"".join(p.read_bytes() for p in inputs) + sys.version.encode()
    ).hexdigest()
    try:
        installed = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        installed = {}
    if installed.get("fingerprint") != fingerprint or not python.exists():
        uv = shutil.which("uv")
        if uv:
            print("Installing Python dependencies from uv.lock...", flush=True)
            subprocess.run(
                [uv, "sync", "--frozen", "--extra", "dev"], cwd=ROOT, env=env, check=True
            )
        else:
            print(
                "uv is unavailable; using the compatible pip installation path (not frozen).",
                flush=True,
            )
            if not python.exists():
                subprocess.run(
                    [sys.executable, "-m", "venv", str(ROOT / ".venv")],
                    cwd=ROOT,
                    env=env,
                    check=True,
                )
            subprocess.run(
                [str(python), "-m", "pip", "install", "-e", ".[dev]"], cwd=ROOT, env=env, check=True
            )
    if installed.get("fingerprint") != fingerprint or not (ROOT / "frontend/node_modules").is_dir():
        print("Installing locked frontend dependencies...", flush=True)
        subprocess.run([npm, "ci"], cwd=ROOT / "frontend", env=env, check=True)
    # Builds are small and must include any changed business-profile frontend.
    subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", env=env, check=True)
    marker.write_text(json.dumps({"fingerprint": fingerprint}), encoding="utf-8")
    return python


def serve(python: Path, *, port: int | None = None, open_browser: bool = True) -> None:
    """Run on loopback; never seed demonstration products or business orders."""
    env = os.environ.copy()
    env["BUSINESS_PROFILE"] = "jiahui"
    env["PYTHONIOENCODING"] = "utf-8"
    # Existing cached model weights remain usable. A launcher must not silently
    # download gigabytes; users can explicitly opt in with HF_HUB_OFFLINE=0.
    env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("TRANSFORMERS_OFFLINE", "1")
    selected_port = port if port is not None else available_port()
    url = f"http://127.0.0.1:{selected_port}"
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    log_path = logs / "jiahui-server.log"
    prefix = (
        subprocess.check_output(
            [
                str(python),
                "-c",
                "from app.config.settings import Settings; print(Settings(business_profile='jiahui').api_v1_prefix)",
            ],
            cwd=ROOT,
            env=env,
            text=True,
        )
        .strip()
        .rstrip("/")
    )
    if not prefix.startswith("/") or "?" in prefix or "#" in prefix:
        raise RuntimeError("API_V1_PREFIX must be an absolute URL path.")
    print(
        "Starting Jiahui profile; existing .env and model configuration are preserved.", flush=True
    )
    print("No demonstration products, orders, prices or stock will be imported.", flush=True)
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
                str(selected_port),
            ],
            cwd=ROOT,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
        )
        try:
            # Only local readiness bypasses the outbound HTTP proxy. Remote model
            # requests keep the inherited platform proxy and TLS trust.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 120
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Server stopped during startup. See {log_path}")
                try:
                    with opener.open(url + prefix + "/profile", timeout=3) as response:
                        profile = json.load(response)
                        if response.status == 200 and profile.get("mode") == "jiahui":
                            break
                except (OSError, ValueError, urllib.error.URLError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Jiahui profile did not become ready. See {log_path}")
                time.sleep(0.5)
            print("Jiahui profile is ready. Sign in using an initialized account.", flush=True)
            print(f"Local address: {url}", flush=True)
            print(f"Server log: {log_path}", flush=True)
            print("Keep this window open. Press Ctrl+C to stop.", flush=True)
            if open_browser:
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
        "--check", action="store_true", help="Check Python/Node without installing or starting."
    )
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="Prepare dependencies and build frontend, then exit.",
    )
    parser.add_argument(
        "--skip-setup",
        action="store_true",
        help="Use already installed dependencies and built frontend.",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Do not open a browser automatically."
    )
    parser.add_argument("--port", type=int, help="Explicit loopback port; fail if occupied.")
    args = parser.parse_args()
    if args.port is not None and not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")
    _, npm = prerequisites()
    print("Python and Node.js checks passed.", flush=True)
    if args.check:
        return
    if args.skip_setup:
        python = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.exists() or not (ROOT / "frontend/dist/index.html").is_file():
            raise RuntimeError(
                "--skip-setup requires .venv and a built frontend. Run --setup-only first."
            )
    else:
        python = setup(npm)
    if not args.setup_only:
        serve(python, port=args.port, open_browser=not args.no_browser)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"\nERROR: {error}", file=sys.stderr)
        raise SystemExit(1) from None
