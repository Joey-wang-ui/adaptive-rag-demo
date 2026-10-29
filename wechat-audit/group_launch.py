"""Independent Windows installer. Does not install Node, OpenClaw or Ollama."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".group-audit-venv"


def main():
    if os.name != "nt":
        raise RuntimeError("Requires Windows with desktop WeChat logged in.")
    python = VENV / "Scripts/python.exe"
    marker = VENV / "requirements.sha256"
    fingerprint = hashlib.sha256(
        (ROOT / "requirements.txt").read_bytes()
        + (ROOT / "group-audit-requirements.txt").read_bytes()
        + sys.version.encode()
    ).hexdigest()
    if not python.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    if not marker.exists() or marker.read_text() != fingerprint:
        print("Installing pinned group transport, small OCR and API dependencies...", flush=True)
        subprocess.run(
            [str(python), "-m", "pip", "install", "-r", str(ROOT / "group-audit-requirements.txt")],
            check=True,
        )
        marker.write_text(fingerprint)
    return subprocess.call(
        [str(python), "-m", "shipment_bot.group_runtime", *sys.argv[1:]], cwd=ROOT
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(f"Setup stopped ({type(error).__name__}). No model/WeChat readiness is confirmed.")
        sys.exit(1)
