"""Permission-filtered Jiahui corpus using the original retrieval components."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path

from app.jiahui.catalog import Catalog, normalized
from app.retrieval.bm25 import BM25Retriever
from app.retrieval.hybrid import HybridRetriever
from app.retrieval.tokenizer import tokenize
from app.retrieval.vector import InMemoryVectorStore, VectorRetriever, build_records
from app.schemas.retrieval import Citation, RetrievedChunk

EXAMPLES = [
    "查一下商品编号06401的规格和来源",
    "天成蛋黄液有哪些已录入规格？",
    "对比两个指定商品的规格和储存条件",
    "这款产品的保质期是否已录入？",
    "这份资料是什么版本，是否已确认有效？",
]


def text_chunks(text: str, target: int = 2200) -> list[str]:
    """Keep table rows and nearby lines together instead of slicing through cells."""
    chunks: list[str] = []
    lines: list[str] = []
    length = 0
    for line in text.splitlines(keepends=True):
        if lines and length + len(line) > target:
            chunks.append("".join(lines))
            overlap = lines[-2:]
            lines = overlap if sum(map(len, overlap)) < target // 2 else []
            length = sum(map(len, lines))
        lines.append(line)
        length += len(line)
    if lines and "".join(lines).strip():
        chunks.append("".join(lines))
    return chunks


class ScopedRetriever:
    def __init__(self, settings, embedder):
        self.settings = settings
        self.embedder = embedder

    async def retrieve(self, query: str, *, top_k: int):
        # Legacy tools without server-owned scopes must never recall this corpus.
        return []

    def knowledge(self, scopes) -> list[RetrievedChunk]:
        root = Path(self.settings.knowledge_dir).resolve()
        manifest = root / "manifest.json"
        if not manifest.exists():
            return []
        documents = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(documents, list):
            raise ValueError("嘉惠知识manifest必须是文档数组")
        chunks = []
        for item in documents:
            if item.get("scope", "knowledge") not in scopes and "*" not in scopes:
                continue
            status = item.get("status", "draft")
            if status not in {"current", "reference"}:
                continue
            effective = item.get("effective_date", "")
            if effective and date.fromisoformat(effective) > date.today():
                continue
            path = (root / item["path"]).resolve()
            if not path.is_relative_to(root) or path.suffix.lower() not in {".md", ".txt"}:
                raise ValueError("知识文档必须位于独立知识目录内")
            text = path.read_text(encoding="utf-8")
            for offset, content in enumerate(text_chunks(text)):
                if not content.strip():
                    continue
                key = hashlib.sha256(
                    (str(path) + item.get("version", "") + str(offset)).encode()
                ).hexdigest()[:20]
                location = item.get("location", "章节未提供")
                version = item.get("version") or "未提供"
                chunks.append(
                    RetrievedChunk(
                        document_id=item["id"],
                        chunk_id="jh-k-" + key,
                        title=item.get("title", path.stem),
                        content=content,
                        score=0,
                        section=f"{item.get('source_file', path.name)} · {location} · 版本 {version} · {status}",
                        policy_version=version,
                        metadata={
                            "status": status,
                            "scope": item.get("scope", "knowledge"),
                            "source_file": item.get("source_file", path.name),
                            "location": location,
                        },
                    )
                )
        return chunks

    def knowledge_documents(self, scopes) -> list[dict[str, str]]:
        documents = {}
        for chunk in self.knowledge(scopes):
            documents[(chunk.document_id, chunk.policy_version)] = {
                "id": chunk.document_id,
                "title": chunk.title,
                "source_file": chunk.metadata.get("source_file", chunk.title),
                "location": chunk.metadata.get("location", chunk.section or "位置未提供"),
                "version": chunk.policy_version,
                "status": chunk.metadata.get("status", "reference"),
            }
        return list(documents.values())

    def knowledge_stats(self, scopes) -> dict[str, int]:
        documents = self.knowledge_documents(scopes)
        return {
            "knowledge_document_count": len(documents),
            "knowledge_file_count": len({d["source_file"] for d in documents}),
            "reference_document_count": sum(d["status"] == "reference" for d in documents),
        }

    async def search_scoped(self, query, chunks, *, top_k=8):
        if not chunks:
            return []
        tokens = {t for t in tokenize(query) if len(t) > 1}
        explicit_code = re.search(
            r"(?:编号|货号|编码)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9_*.-]*)", query
        )
        if explicit_code:
            code = explicit_code.group(1)
            anchor = re.compile(
                r"(?<![A-Za-z0-9_*.-])"
                + r"\s*".join(re.escape(c) for c in code)
                + r"(?![A-Za-z0-9_*.-])",
                re.IGNORECASE,
            )
            relevant = [c for c in chunks if anchor.search(c.content)]
        else:
            relevant = [
                c for c in chunks if any(normalized(t) in normalized(c.content) for t in tokens)
            ]
        if not relevant:
            return []
        vectors = await self.embedder.embed([c.content for c in relevant])
        store = InMemoryVectorStore()
        store.upsert(build_records(relevant, vectors))
        # Permission filtering is BEFORE indexing. No shared cache or stale vectors.
        hybrid = HybridRetriever(
            BM25Retriever(relevant),
            VectorRetriever(store, self.embedder),
            rrf_k=self.settings.rrf_k,
            cache=None,
        )
        return await hybrid.retrieve(query, top_k=top_k)


def record_chunk(row):
    key = hashlib.sha256((row["id"] + "|" + row.get("version", "")).encode()).hexdigest()[:20]
    return RetrievedChunk(
        chunk_id="jh-p-" + key,
        document_id=row["id"],
        title=row["name"],
        content=Catalog.to_document(row),
        score=1,
        section=f"{row.get('source_file') or '来源文件未提供'} · {row.get('source_location') or '位置未提供'} · 版本 {row.get('version') or '未提供'}",
        policy_version=row.get("version") or "未提供",
        metadata={"scope": row.get("scope", "products")},
    )


def citations(chunks):
    return [
        Citation(document_id=c.document_id, title=c.title, chunk_id=c.chunk_id, section=c.section)
        for c in chunks
    ]


async def probe_capabilities(llm, retriever, reranker, settings):
    """Verify real operations once at startup; configuration alone is not health."""
    from app.providers.embeddings import HashEmbeddingProvider
    from app.providers.fake import OfflineLLMProvider

    caps = {"llm": "offline_excerpt", "embedding": "未核验", "reranker": "未核验"}
    if not isinstance(llm, OfflineLLMProvider):
        backend = "ollama" if settings.llm_backend == "ollama" else "deepseek"
        try:
            response = await llm.generate(
                [{"role": "user", "content": 'Return JSON only: {"status":"ok"}'}],
                max_tokens=64,
                response_format={"type": "json_object"},
            )
            caps["llm"] = (
                backend
                if json.loads(response.content).get("status") == "ok"
                else backend + "_unverified"
            )
        except Exception:
            caps["llm"] = backend + "_unavailable"
    embedder = retriever.embedder
    try:
        vectors = await embedder.embed(["嘉惠产品资料"])
        if not vectors or not vectors[0]:
            raise ValueError("empty embedding")
        caps["embedding"] = (
            "hash"
            if isinstance(embedder, HashEmbeddingProvider)
            else ("remote" if settings.embedding_configured else "local")
        )
    except Exception:
        # Availability failure must not silently claim semantic retrieval.
        retriever.embedder = HashEmbeddingProvider(dim=settings.embedding_dim)
        caps["embedding"] = "hash"
    try:
        await reranker._provider.rerank("规格", ["产品规格", "无关"], top_n=2)
        caps["reranker"] = (
            "cross_encoder" if reranker._provider.backend == "cross-encoder" else "lexical"
        )
    except Exception:
        from app.providers.reranker import LexicalRerankProvider

        reranker._provider = LexicalRerankProvider()
        caps["reranker"] = "lexical"
    retriever.capabilities = caps
    return caps
