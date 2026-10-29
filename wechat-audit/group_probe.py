"""Install a small, isolated Windows UIA probe. No model or WeChat bot login required."""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    if os.name != "nt":
        raise RuntimeError("This checker runs on the Windows PC with WeChat installed.")
    environment = ROOT / ".group-venv"
    python = environment / "Scripts/python.exe"
    requirements = ROOT / "group-requirements.txt"
    marker = environment / ".requirements-sha256"
    digest = hashlib.sha256(requirements.read_bytes() + sys.version.encode()).hexdigest()
    if not python.exists():
        print("Creating the small Windows UI checker environment...", flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(environment)], check=True)
    if not marker.exists() or marker.read_text() != digest:
        print("Installing Windows accessibility components. No API key is needed.", flush=True)
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(requirements)], check=True)
        marker.write_text(digest)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    subprocess.run([str(python), "-m", "shipment_bot.group_ui"], cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. No messages were sent by the checker.")
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        print(
            str(error)
            if isinstance(error, RuntimeError)
            else f"Checker did not finish ({type(error).__name__}). Keep the output above."
        )
        raise SystemExit(1) from None
