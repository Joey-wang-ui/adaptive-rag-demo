from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError

import pytest

from scripts import auto_local
from scripts.local_lock import file_lock


def test_lock_prevents_duplicate_installers_and_releases_after_error(tmp_path):
    lock_path = tmp_path / "installer.lock"
    with pytest.raises(ValueError):
        with file_lock(lock_path):
            with pytest.raises(RuntimeError, match="Another installer"):
                with file_lock(lock_path):
                    pytest.fail("A second installer must not acquire the same lock")
            raise ValueError("Failed install")
    with file_lock(lock_path):
        pass


def test_auto_launch_serves_only_after_real_model_check(monkeypatch, tmp_path):
    steps = []
    monkeypatch.setattr(auto_local.start_local, "ROOT", tmp_path)
    monkeypatch.setattr(auto_local.start_local, "prerequisites", lambda: ("node", "npm"))
    monkeypatch.setattr(auto_local.start_local, "setup", lambda npm: Path("python"))
    monkeypatch.setattr(auto_local, "prepare_model", lambda: steps.append("download"))

    @contextmanager
    def ready_model(root, model, *, install_missing, warmup_timeout):
        install_missing()
        steps.append("real generation check passed")
        assert warmup_timeout == 600
        yield {"LLM_BACKEND": "ollama"}

    monkeypatch.setattr(auto_local.local_ollama, "ready_model", ready_model)
    monkeypatch.setattr(auto_local.start_local, "serve", lambda python, env: steps.append("serve"))
    assert auto_local.main() == 0
    assert steps == ["download", "real generation check passed", "serve"]


def test_auto_failure_does_not_start_webpage_or_expose_signed_urls(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(auto_local.start_local, "ROOT", tmp_path)
    monkeypatch.setattr(auto_local.start_local, "prerequisites", lambda: ("node", "npm"))
    monkeypatch.setattr(auto_local.start_local, "setup", lambda npm: Path("python"))
    monkeypatch.setattr(
        auto_local.start_local,
        "serve",
        lambda *args: pytest.fail("No webpage on failed model setup"),
    )

    def failed_check(*args, **kwargs):
        raise HTTPError("https://cdn.example/?signature=hidden", 503, "Unavailable", {}, None)

    monkeypatch.setattr(auto_local.local_ollama, "ready_model", failed_check)
    assert auto_local.main() == 1
    output = capsys.readouterr().out
    log = (tmp_path / "logs/auto-install.log").read_text()
    assert "HTTP status: 503" in output
    assert "HTTP status: 503" in log
    assert "signature=hidden" not in output + log
