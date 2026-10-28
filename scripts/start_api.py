"""Prepare app dependencies, privately accept a key, check API, then serve.

Run with the system Python. The real provider check runs with the project's
virtualenv Python. Keys go only through the child environment, never argv/files.
"""

import getpass
import os
import subprocess
import sys
import warnings

from scripts import start_local
from scripts.local_lock import file_lock

BASE_URL = "https://api.deepseek.com"
MODEL = "deepseek-chat"


def api_environment() -> dict[str, str]:
    print("Paste your DeepSeek API key below. Input is hidden; press Enter after pasting.")
    print("Press Enter without typing to use DEEPSEEK_API_KEY from your environment or .env.")
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            key = getpass.getpass("DeepSeek API key (hidden): ").strip()
        except getpass.GetPassWarning:
            raise RuntimeError("Open this launcher in an interactive Windows CMD window.") from None
    env = {
        "LLM_BACKEND": "auto",
        "DEEPSEEK_BASE_URL": BASE_URL,
        "DEEPSEEK_MODEL": MODEL,
        "LLM_TIMEOUT_SECONDS": "60",
        "LLM_MAX_RETRIES": "3",
        "LOG_LEVEL": "INFO",
    }
    if key:
        env["DEEPSEEK_API_KEY"] = key
    return env


def main() -> None:
    with file_lock(start_local.ROOT / "logs/api-launcher.lock"):
        print("[1/3] Checking and preparing project dependencies...", flush=True)
        _, npm = start_local.prerequisites()
        python = start_local.setup(npm)
        overrides = api_environment()
        env = os.environ.copy()
        env.update(overrides)
        env["PYTHONIOENCODING"] = "utf-8"
        print("[2/3] Checking one real API JSON generation (up to 64 output tokens)...", flush=True)
        result = subprocess.run(
            [str(python), "-m", "scripts.check_api"], cwd=start_local.ROOT, env=env
        )
        if result.returncode:
            raise RuntimeError("API generation check failed; see the explanation above.")
        print("[3/3] API verified; starting the chat application...", flush=True)
        start_local.serve(python, overrides)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
    except EOFError:
        print("\nNo API key was entered. Reopen start-api.cmd in an interactive terminal.")
        raise SystemExit(1) from None
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        # Never print arbitrary configuration / subprocess payloads containing a key.
        if isinstance(error, RuntimeError):
            print(f"\nERROR: {error}", file=sys.stderr)
        else:
            print(
                f"\nAPI setup failed ({type(error).__name__}). Check the output above.",
                file=sys.stderr,
            )
        raise SystemExit(1) from None
