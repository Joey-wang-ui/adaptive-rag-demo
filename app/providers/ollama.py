"""Native Ollama chat API, without cloud credentials or silent provider switching."""

from collections.abc import AsyncIterator
from urllib.parse import urlsplit

import httpx

from app.config.settings import Settings
from app.core.exceptions import LLMProviderError
from app.providers.base import LLMResponse
from app.schemas.common import TokenUsage


class OllamaProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._owns_client = client is None
        local = urlsplit(settings.ollama_base_url).hostname in {"127.0.0.1", "localhost", "::1"}
        self._client = client or httpx.AsyncClient(
            base_url=settings.ollama_base_url.rstrip("/"),
            timeout=httpx.Timeout(settings.ollama_timeout_seconds, connect=3.0),
            trust_env=not local,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def ping(self) -> bool:
        try:
            response = await self._client.get("/api/tags")
            response.raise_for_status()
            model = self._settings.ollama_model
            expected = model if ":" in model else model + ":latest"
            return any(
                entry.get("name") == expected or entry.get("model") == expected
                for entry in response.json()["models"]
            )
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            return False

    async def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        response_format: dict[str, str] | None = None,
    ) -> LLMResponse:
        payload: dict = {
            "model": self._settings.ollama_model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
                "num_ctx": self._settings.ollama_num_ctx,
            },
        }
        if response_format and response_format.get("type") == "json_object":
            payload["format"] = "json"
        try:
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise LLMProviderError(
                "Ollama generation timed out", timeout=True, retryable=False
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMProviderError(
                "Ollama request failed; check the local service and model", retryable=False
            ) from exc
        try:
            body = response.json()
            content = body["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("Ollama content is not text")
            prompt_tokens = int(body.get("prompt_eval_count") or 0)
            completion_tokens = int(body.get("eval_count") or 0)
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMProviderError("Ollama returned an invalid response", retryable=False) from exc
        return LLMResponse(
            content=content,
            model=self._settings.ollama_model,
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
        )

    async def stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int = 1024,
        response_format: dict[str, str] | None = None,
    ) -> AsyncIterator[str]:
        # The application validates a complete answer before streaming it to the UI.
        response = await self.generate(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
        if response.content:
            yield response.content
