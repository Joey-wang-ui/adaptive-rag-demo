"""Prepare a real local Ollama model, with no cloud credential requirement."""

import json
import os
import shutil
import subprocess
import time
import urllib.request
import webbrowser
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

BASE_URL = "http://127.0.0.1:11434"


def api(path: str, payload: dict | None = None, *, timeout: float = 3) -> dict:
    request = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        body = json.load(response)
    if not isinstance(body, dict):
        raise ValueError("Ollama response is not an object")
    return body


@contextmanager
def ready_model(root: Path, model: str) -> Iterator[dict[str, str]]:
    if not model.strip() or model.startswith("-"):
        raise RuntimeError("Choose a valid Ollama model name.")
    executable = shutil.which("ollama")
    windows_path = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
    if not executable and os.name == "nt" and windows_path.is_file():
        executable = str(windows_path)
    if not executable:
        if os.name == "nt":
            webbrowser.open("https://ollama.com/download/windows")
        raise RuntimeError(
            "Install Ollama from https://ollama.com/download/windows, then reopen this launcher."
        )
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    child = None
    log = None
    try:
        try:
            tags = api("/api/tags")
        except (OSError, ValueError):
            env = os.environ.copy()
            env["OLLAMA_HOST"] = "127.0.0.1:11434"
            log = (logs / "ollama.log").open("a", encoding="utf-8")
            child = subprocess.Popen(
                [executable, "serve"], env=env, stdout=log, stderr=subprocess.STDOUT
            )
            deadline = time.monotonic() + 30
            while True:
                if child.poll() is not None:
                    raise RuntimeError("Ollama did not start. See logs/ollama.log.") from None
                try:
                    tags = api("/api/tags")
                    break
                except (OSError, ValueError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError(
                            "Ollama did not become ready. See logs/ollama.log."
                        ) from None
                    time.sleep(0.5)
        installed = {entry.get("name") or entry.get("model") for entry in tags.get("models", [])}
        expected = model if ":" in model else model + ":latest"
        if expected not in installed:
            print(
                f"Downloading {model}. This can require several GB and several minutes...",
                flush=True,
            )
            pull_env = os.environ.copy()
            pull_env["OLLAMA_HOST"] = "127.0.0.1:11434"
            subprocess.run([executable, "pull", model], cwd=root, env=pull_env, check=True)
        print(f"Warming up {model} and checking real JSON generation...", flush=True)
        result = api(
            "/api/chat",
            {
                "model": model,
                "messages": [
                    {"role": "user", "content": 'Return only this JSON object: {"ok": true}'}
                ],
                "stream": False,
                "format": "json",
                "options": {"num_ctx": 4096, "num_predict": 40, "temperature": 0},
            },
            timeout=180,
        )
        try:
            valid = json.loads(result["message"]["content"]).get("ok") is True
        except (KeyError, TypeError, ValueError, AttributeError):
            valid = False
        if not valid:
            raise RuntimeError(
                "The model failed the JSON generation check. Try again or select another model."
            )
        print(
            f"Local model ready: {model}. Embedding and reranking remain in lightweight fallback mode.",
            flush=True,
        )
        yield {
            "LLM_BACKEND": "ollama",
            "OLLAMA_BASE_URL": BASE_URL,
            "OLLAMA_MODEL": model,
            "TOKEN_BUDGET": "4096",
        }
    finally:
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if log is not None:
            log.close()
