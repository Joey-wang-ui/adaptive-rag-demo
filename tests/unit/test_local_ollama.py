import pytest

from scripts import local_ollama


@pytest.mark.parametrize("installed", [True, False])
def test_launcher_checks_real_generation_before_selecting_ollama(monkeypatch, tmp_path, installed):
    calls = []
    pulls = []
    monkeypatch.setattr(local_ollama.shutil, "which", lambda name: "ollama")

    def api(path, payload=None, **kwargs):
        calls.append(path)
        if path == "/api/tags":
            return {"models": [{"name": "qwen2.5:3b"}] if installed else []}
        assert payload["stream"] is False
        assert payload["format"] == "json"
        return {"message": {"content": '{"ok":true}'}}

    monkeypatch.setattr(local_ollama, "api", api)
    monkeypatch.setattr(
        local_ollama.subprocess, "run", lambda command, **kw: pulls.append((command, kw))
    )
    with local_ollama.ready_model(tmp_path, "qwen2.5:3b") as env:
        assert env["LLM_BACKEND"] == "ollama"
        assert calls == ["/api/tags", "/api/chat"]
    assert len(pulls) == (0 if installed else 1)
    if pulls:
        assert pulls[0][1]["env"]["OLLAMA_HOST"] == "127.0.0.1:11434"


def test_launcher_does_not_mark_failed_json_generation_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(local_ollama.shutil, "which", lambda name: "ollama")
    monkeypatch.setattr(
        local_ollama,
        "api",
        lambda path, *args, **kwargs: {"models": [{"name": "qwen2.5:3b"}]}
        if path == "/api/tags"
        else {"message": {"content": "not json"}},
    )
    with pytest.raises(RuntimeError, match="JSON generation"):
        with local_ollama.ready_model(tmp_path, "qwen2.5:3b"):
            pytest.fail("Failed generation must stop startup")
