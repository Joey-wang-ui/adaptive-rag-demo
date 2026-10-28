import asyncio
import json
import subprocess
from pathlib import Path

import httpx
import pytest

from app.core.exceptions import LLMProviderError
from app.providers.deepseek import DeepSeekProvider
from scripts import check_api, start_api


def test_key_input_is_private_and_only_forwarded_in_environment(monkeypatch, capsys):
    monkeypatch.setattr(start_api.getpass, "getpass", lambda prompt: "unit-test-private-key")
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    env = start_api.api_environment()
    assert env["DEEPSEEK_API_KEY"] == "unit-test-private-key"
    assert env["LLM_BACKEND"] == "auto"
    assert env["DEEPSEEK_BASE_URL"] == "https://api.deepseek.com"
    assert "unit-test-private-key" not in capsys.readouterr().out


def test_echoed_secret_input_is_rejected(monkeypatch):
    def unprotected(prompt):
        raise start_api.getpass.GetPassWarning("No terminal")

    monkeypatch.setattr(start_api.getpass, "getpass", unprotected)
    with pytest.raises(RuntimeError, match="interactive"):
        start_api.api_environment()


@pytest.mark.parametrize("verified", [True, False])
def test_launcher_only_opens_app_after_successful_api_check(monkeypatch, tmp_path, verified):
    monkeypatch.setattr(start_api.start_local, "ROOT", tmp_path)
    monkeypatch.setattr(start_api.start_local, "prerequisites", lambda: ("node", "npm"))
    monkeypatch.setattr(start_api.start_local, "setup", lambda npm: Path("venv-python"))
    monkeypatch.setattr(start_api.getpass, "getpass", lambda prompt: "unit-test-private-key")
    checks = []
    launches = []

    def run(command, **kwargs):
        assert "unit-test-private-key" not in str(command)
        assert kwargs["env"]["DEEPSEEK_API_KEY"] == "unit-test-private-key"
        checks.append(command)
        return subprocess.CompletedProcess(command, 0 if verified else 1)

    monkeypatch.setattr(start_api.subprocess, "run", run)
    monkeypatch.setattr(start_api.start_local, "serve", lambda *args: launches.append(args))
    if verified:
        start_api.main()
        assert len(launches) == 1
        assert launches[0][1]["LLM_BACKEND"] == "auto"
    else:
        with pytest.raises(RuntimeError, match="generation check failed"):
            start_api.main()
        assert launches == []
    assert checks == [["venv-python", "-m", "scripts.check_api"]]
    assert not (tmp_path / ".env").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("valid_json", [True, False])
async def test_api_check_uses_real_provider_contract_and_requires_valid_generation(
    monkeypatch, valid_json
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-private-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://wrong-provider.example")
    monkeypatch.setenv("LLM_BACKEND", "ollama")
    seen = []

    def handler(request):
        seen.append(request)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer unit-test-private-key"
        body = json.loads(request.content)
        assert body["model"] == "deepseek-chat"
        assert body["max_tokens"] == 64
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": '{"ok":true}' if valid_json else "invalid generation"}}
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://api.deepseek.com"
    ) as client:
        monkeypatch.setattr(
            check_api,
            "DeepSeekProvider",
            lambda settings: DeepSeekProvider(settings, client=client),
        )
        if valid_json:
            await check_api.check()
        else:
            with pytest.raises(RuntimeError, match="no valid JSON"):
                await check_api.check()
    assert len(seen) == 1


@pytest.mark.parametrize("status", [401, 402, 403, 404])
def test_api_auth_balance_errors_fail_without_leaking_key(monkeypatch, capsys, status):
    async def failed():
        raise LLMProviderError(f"DeepSeek HTTP {status}", retryable=False)

    monkeypatch.setattr(check_api, "check", failed)
    assert check_api.main() == 1
    output = capsys.readouterr().out
    assert "API check failed" in output
    assert "unit-test-private-key" not in output
    if status == 402:
        assert "balance" in output


def test_empty_key_does_not_pass_api_readiness(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    with pytest.raises(RuntimeError, match="No DeepSeek API key"):
        asyncio.run(check_api.check())
