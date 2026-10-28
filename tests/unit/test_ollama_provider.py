import json

import httpx
import pytest

from app.config.settings import Settings
from app.core.exceptions import LLMProviderError
from app.providers.ollama import OllamaProvider


async def test_native_json_generation_and_usage():
    def respond(request):
        assert request.url.path == "/api/chat"
        assert "authorization" not in request.headers
        payload = json.loads(request.content)
        assert payload["format"] == "json"
        assert payload["stream"] is False
        assert payload["options"]["num_predict"] == 80
        return httpx.Response(
            200,
            json={
                "message": {"content": '{"answer":"ok"}'},
                "prompt_eval_count": 12,
                "eval_count": 4,
            },
        )

    async with httpx.AsyncClient(
        base_url="http://local", transport=httpx.MockTransport(respond)
    ) as client:
        provider = OllamaProvider(Settings(llm_backend="ollama"), client=client)
        result = await provider.generate(
            [{"role": "user", "content": "check"}],
            max_tokens=80,
            response_format={"type": "json_object"},
        )
    assert result.content == '{"answer":"ok"}'
    assert result.usage.total_tokens == 16


@pytest.mark.parametrize(
    "names,ready", [(["qwen2.5:3b"], True), (["other:latest"], False), ([], False)]
)
async def test_ping_requires_selected_model(names, ready):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"models": [{"name": name} for name in names]})
    )
    async with httpx.AsyncClient(base_url="http://local", transport=transport) as client:
        assert await OllamaProvider(Settings(), client=client).ping() is ready


@pytest.mark.parametrize(
    "status,body", [(404, {"error": "model missing"}), (200, {"message": None})]
)
async def test_invalid_response_is_an_error_not_an_offline_success(status, body):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body))
    async with httpx.AsyncClient(base_url="http://local", transport=transport) as client:
        with pytest.raises(LLMProviderError):
            await OllamaProvider(Settings(), client=client).generate([])
