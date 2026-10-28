"""One entry point: install app dependencies, prepare a real model, and serve."""

import datetime
import http.client
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

from scripts import download_ollama_model, local_ollama, start_local
from scripts.local_lock import file_lock


class ConsoleLog:
    def __init__(self, console, log):
        self.console = console
        self.log = log

    def write(self, value):
        self.console.write(value)
        self.log.write(value)
        self.flush()
        return len(value)

    def flush(self):
        self.console.flush()
        self.log.flush()


def prepare_model() -> None:
    model_dir = Path(os.environ.get("OLLAMA_MODELS") or Path.home() / ".ollama/models")
    with file_lock(model_dir / ".adaptive-rag-download.lock"):
        download_ollama_model.install(model_dir)


def launch() -> None:
    print("[1/4] Checking Python, Node.js, and project files...", flush=True)
    _, npm = start_local.prerequisites()
    print("[2/4] Installing or reusing app dependencies and building the webpage...", flush=True)
    python = start_local.setup(npm)
    print("[3/4] Preparing Qwen2.5 3B and checking real model generation...", flush=True)
    print("The first download and model load can take several minutes. Keep this window open.")
    with local_ollama.ready_model(
        start_local.ROOT,
        download_ollama_model.MODEL,
        install_missing=prepare_model,
        warmup_timeout=600,
    ) as overrides:
        print("[4/4] Starting the backend and checking its health...", flush=True)
        start_local.serve(python, overrides)


def main() -> int:
    logs = start_local.ROOT / "logs"
    logs.mkdir(exist_ok=True)
    with (logs / "auto-install.log").open("a", encoding="utf-8") as log:
        console = sys.stdout
        sys.stdout = ConsoleLog(console, log)
        try:
            print(f"\nAutomatic setup: {datetime.datetime.now().isoformat(timespec='seconds')}")
            with file_lock(logs / "auto-install.lock"):
                launch()
        except KeyboardInterrupt:
            print(
                "\nStopped. Saved download progress is kept. Reopen install-and-run.cmd to resume."
            )
            return 0
        except (
            OSError,
            ValueError,
            RuntimeError,
            http.client.HTTPException,
            subprocess.SubprocessError,
        ) as error:
            print(
                f"\nSetup failed ({type(error).__name__}). Real model readiness is not confirmed."
            )
            if isinstance(error, urllib.error.HTTPError):
                print(f"HTTP status: {error.code}")
            elif isinstance(error, RuntimeError | ValueError | subprocess.SubprocessError):
                print(str(error))
            print("Details: logs/auto-install.log and logs/local-server.log (if backend started).")
            return 1
        finally:
            sys.stdout = console
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
