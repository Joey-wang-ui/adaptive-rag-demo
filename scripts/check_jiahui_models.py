"""Probe actual model operations without printing configuration secrets or answers."""

from __future__ import annotations

import asyncio
import json
import math
import os
from typing import Any

from app.config.settings import Settings
from app.providers.deepseek import DeepSeekProvider
from app.providers.embeddings import HashEmbeddingProvider, build_embedding_provider
from app.providers.ollama import OllamaProvider
from app.providers.reranker import CrossEncoderRerankProvider


async def check_models(settings: Settings) -> dict[str, dict[str, Any]]:
    """Require generation/embedding/prediction, not configuration presence alone."""
    result: dict[str, dict[str, Any]] = {}
    if settings.llm_backend != "ollama" and not settings.llm_configured:
        result["llm"] = {
            "backend": "offline_excerpt",
            "available": False,
            "status": "not_configured",
        }
    else:
        provider = (
            OllamaProvider(settings)
            if settings.llm_backend == "ollama"
            else DeepSeekProvider(settings)
        )
        try:
            response = await provider.generate(
                [{"role": "user", "content": 'Return only the JSON object {"status":"ok"}.'}],
                max_tokens=64,
                response_format={"type": "json_object"},
            )
            passed = json.loads(response.content).get("status") == "ok"
            result["llm"] = {
                "backend": "ollama" if settings.llm_backend == "ollama" else "deepseek",
                "available": passed,
                "status": "generation_verified" if passed else "invalid_probe_response",
            }
        except Exception as exc:
            result["llm"] = {
                "backend": "ollama" if settings.llm_backend == "ollama" else "deepseek",
                "available": False,
                "status": type(exc).__name__,
            }
        finally:
            await provider.aclose()
    embedder = None
    try:
        embedder = build_embedding_provider(settings)
        vectors = await embedder.embed(["嘉惠产品资料查询", "包装规格与储存条件"])
        valid = len(vectors) == 2 and all(v and all(math.isfinite(x) for x in v) for v in vectors)
        semantic = not isinstance(embedder, HashEmbeddingProvider)
        result["embedding"] = {
            "backend": embedder.backend,
            "available": bool(valid and semantic),
            "status": "embedding_verified" if valid and semantic else "hash_fallback_not_semantic",
        }
    except Exception as exc:
        result["embedding"] = {
            "backend": "unavailable",
            "available": False,
            "status": type(exc).__name__,
        }
    finally:
        closer = getattr(embedder, "aclose", None)
        if closer:
            await closer()
    try:
        reranker = CrossEncoderRerankProvider(settings)
        ranked = await reranker.rerank(
            "包装规格", ["产品包装规格资料", "与问题无关的内容"], top_n=2
        )
        neural = reranker.backend == "cross-encoder"
        valid = bool(ranked) and all(math.isfinite(score) for _, score in ranked)
        result["reranker"] = {
            "backend": reranker.backend,
            "available": bool(neural and valid),
            "status": "prediction_verified"
            if neural and valid
            else "lexical_fallback_not_cross_encoder",
        }
    except Exception as exc:
        result["reranker"] = {
            "backend": "unavailable",
            "available": False,
            "status": type(exc).__name__,
        }
    return result


def main() -> None:
    # Use existing cached local models; downloads require an explicit user opt-in.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    try:
        settings = Settings(business_profile="jiahui")
        result = asyncio.run(check_models(settings))
    except Exception as exc:
        # Settings validation errors may contain supplied values. Report the
        # category alone instead of leaking credentials in a traceback.
        print(json.dumps({"status": "check_failed", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
