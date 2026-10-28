"""Check actual official DeepSeek generation; configuration alone is not readiness."""

import asyncio
import json

from app.config.settings import Settings
from app.core.exceptions import LLMProviderError
from app.providers.deepseek import DeepSeekProvider


async def check() -> None:
    # Explicit official endpoint also overrides stale custom URLs in .env.
    settings = Settings(
        llm_backend="auto",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-chat",
        llm_max_retries=1,
        llm_timeout_seconds=60,
    )
    if not settings.llm_configured:
        raise RuntimeError(
            "No DeepSeek API key configured. Create one at "
            "https://platform.deepseek.com/api_keys and enter it in this local launcher."
        )
    provider = DeepSeekProvider(settings)
    try:
        result = await provider.generate(
            [{"role": "user", "content": 'Return only this JSON object: {"ok": true}'}],
            response_format={"type": "json_object"},
            max_tokens=64,
        )
        try:
            valid = json.loads(result.content).get("ok") is True
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise RuntimeError("The API returned no valid JSON generation. Chat was not started.")
    finally:
        await provider.aclose()
    print("Real DeepSeek API generation verified. No local Ollama model is used.", flush=True)
    print("Embedding and reranking remain in lightweight fallback mode.", flush=True)


def explain(error: LLMProviderError) -> str:
    message = str(error)
    for status, hint in {
        400: "API request rejected; check the selected model and API compatibility.",
        401: "API key rejected. Check that you entered a valid DeepSeek API key.",
        402: "Insufficient API balance. Check your DeepSeek account balance/credits.",
        403: "API access denied. Check account permissions and provider access rules.",
        404: "API model or endpoint was not found.",
        422: "API request format was rejected.",
    }.items():
        if f"HTTP {status}" in message:
            return hint
    if error.error_code.value == "LLM_RATE_LIMITED":
        return "API rate limited. Wait and retry, or check your account quota."
    if error.error_code.value == "LLM_TIMEOUT":
        return "API request timed out. Check your network/proxy and try again."
    return "API request failed. Check provider availability and your network/proxy."


def main() -> int:
    try:
        asyncio.run(check())
    except LLMProviderError as error:
        print("API check failed: " + explain(error))
        return 1
    except RuntimeError as error:
        print("API check failed: " + str(error))
        return 1
    except (OSError, ValueError, TypeError, KeyError):
        print("API check failed: invalid configuration or unexpected API response.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
