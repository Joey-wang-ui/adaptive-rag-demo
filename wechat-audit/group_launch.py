"""Independent Windows installer. Does not install Node, OpenClaw or Ollama."""

import contextlib
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".group-audit-venv"


def main():
    if os.name != "nt":
        raise RuntimeError("Requires Windows. Local window review does not need WeChat.")
    python = VENV / "Scripts/python.exe"
    marker = VENV / "requirements.sha256"
    fingerprint = hashlib.sha256(
        (ROOT / "requirements.txt").read_bytes()
        + (ROOT / "group-audit-requirements.txt").read_bytes()
        + sys.version.encode()
    ).hexdigest()
    if not python.is_file():
        subprocess.run(
            [sys.executable, "-m", "venv", str(VENV)],
            check=True,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    if not marker.exists() or marker.read_text() != fingerprint:
        print("Installing pinned group transport, small OCR and API dependencies...", flush=True)
        subprocess.run(
            [str(python), "-m", "pip", "install", "-r", str(ROOT / "group-audit-requirements.txt")],
            check=True,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        marker.write_text(fingerprint)
    if sys.argv[1:] == ["desktop"]:
        return subprocess.call([str(python), str(ROOT / "desktop.py")], cwd=ROOT)
    module = (
        "shipment_bot.local_review" if sys.argv[1:] == ["local"] else "shipment_bot.group_runtime"
    )
    return subprocess.call(
        [str(python), "-m", module, *([] if module.endswith("local_review") else sys.argv[1:])],
        cwd=ROOT,
        stdout=sys.stdout,
        stderr=sys.stderr,
    )


if __name__ == "__main__":
    try:
        log_path = os.environ.get("JIAHUI_AUDIT_LOG")
        if log_path:
            with open(log_path, "a", encoding="utf-8", buffering=1) as log:
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    sys.exit(main())
        else:
            sys.exit(main())
    except Exception as error:
        print(f"Setup stopped ({type(error).__name__}). No model/WeChat readiness is confirmed.")
        sys.exit(1)
