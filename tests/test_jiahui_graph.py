"""Grounded graph checks use temporary evidence and deterministic providers only."""

import csv
import json

import pytest

from app.config.settings import Settings
from app.jiahui.catalog import FIELDS
from app.jiahui.graph import build_jiahui_graph, conversion_answer
from app.jiahui.import_catalog import import_csv
from app.jiahui.runtime import ScopedRetriever
from app.providers.embeddings import HashEmbeddingProvider
from app.providers.fake import FakeLLMProvider, OfflineLLMProvider
from app.providers.reranker import LexicalRerankProvider
from app.retrieval.reranker import DocumentReranker


def conversion_row(factor="12"):
    return {"conversions": [{"from": "箱", "to": "盒", "factor": factor}]}


def test_conversion_uses_decimal_without_binary_float_artifacts():
    answer = conversion_answer("0.1箱换算多少盒", conversion_row("0.2"))
    assert "0.1箱 = 0.02盒" in answer
    assert "0.020000000000000004" not in answer
    assert "程序使用十进制" in answer


def test_conversion_keeps_confirmed_direction_and_precise_fraction():
    answer = conversion_answer("2.5箱换算多少盒", conversion_row("12"))
    assert "2.5箱 = 30.0盒" in answer
    reverse = conversion_answer("12盒换算多少箱", conversion_row("12"))
    assert "请明确数量" in reverse
    assert "=" not in reverse


@pytest.mark.parametrize("row", [{}, {"conversions": []}, {"conversions": ""}])
def test_no_confirmed_conversion_cannot_be_inferred_from_spec(row):
    row["spec"] = "1kg*12盒/箱"
    answer = conversion_answer("3箱换算多少盒", row)
    assert "尚未录入" in answer
    assert "不能" in answer
    assert "=" not in answer


@pytest.mark.parametrize("factor", ["0", "-1", "NaN", "Infinity", "not-a-number"])
def test_invalid_conversion_factors_are_never_used(factor):
    answer = conversion_answer("3箱换算多少盒", conversion_row(factor))
    assert "需要确认" in answer
    assert "未执行计算" in answer
    assert "=" not in answer


@pytest.mark.parametrize(
    "query",
    [
        "-3箱换算多少盒",
        "+3箱换算多少盒",
        "1e3箱换算多少盒",
        "1E+3箱换算多少盒",
        "1e-3箱换算多少盒",
        "1,000箱换算多少盒",
        "1.2.3箱换算多少盒",
        "−3箱换算多少盒",  # Unicode mathematical minus must not become +3.
        "－3箱换算多少盒",  # Fullwidth minus commonly pasted from spreadsheets.
        "1，000箱换算多少盒",  # Fullwidth thousands separator.
        "1 000箱换算多少盒",  # Space-grouped thousands must not become 0.
    ],
)
def test_ambiguous_or_unsupported_numeric_notation_does_not_compute(query):
    answer = conversion_answer(query, conversion_row())
    assert "=" not in answer
    assert any(hint in answer for hint in ("请明确", "需要确认", "格式不明确"))


@pytest.mark.parametrize(
    "query", ["三箱换算多少盒", "箱换算多少盒", "3箱换算多少包", "3盒换算多少箱"]
)
def test_missing_quantity_or_unsupported_units_require_clarification(query):
    answer = conversion_answer(query, conversion_row())
    assert "请明确" in answer
    assert "=" not in answer


@pytest.fixture
def knowledge_graph(tmp_path):
    """Compile the production graph over one isolated, source-located manual."""
    root = tmp_path / "isolated-jiahui"
    settings = Settings(
        _env_file=None,
        app_env="testing",
        business_profile="jiahui",
        jiahui_data_dir=str(root),
        deepseek_api_key="",
        embedding_api_key="",
        local_embedding_enabled=False,
        cross_encoder_enabled=False,
    )
    knowledge = root / "knowledge"
    knowledge.mkdir(parents=True)
    content = "测试验收规则：入库时核对箱体批次。\n本文未提供具体保质期或储存温度。"
    (knowledge / "receiving.txt").write_text(content, encoding="utf-8")

    def build(reply=None, *, offline=False, status="current"):
        manifest = [
            {
                "id": "isolated-receiving-manual",
                "path": "receiving.txt",
                "title": "隔离测试验收规则",
                "scope": "knowledge",
                "status": status,
                "version": "test-v2",
                "source_file": "隔离测试手册.pdf",
                "location": "PDF第7页，第二章§2.1",
            }
        ]
        (knowledge / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
        )
        retriever = ScopedRetriever(settings, HashEmbeddingProvider(dim=32))
        chunk = retriever.knowledge({"knowledge"})[0]
        if callable(reply):
            reply = reply(chunk)
        llm = OfflineLLMProvider() if offline else FakeLLMProvider({"测试验收规则": reply or "{}"})
        graph = build_jiahui_graph(
            llm=llm,
            settings=settings,
            retriever=retriever,
            reranker=DocumentReranker(LexicalRerankProvider()),
            registry=None,
        )
        return graph, llm, chunk

    return build


async def ask(graph):
    return await graph.ainvoke(
        {"user_query": "测试验收规则是什么？", "extra": {"scopes": ["knowledge"]}}
    )


async def test_model_selected_verbatim_quote_is_checked_and_cited(knowledge_graph):
    quote = "入库时核对箱体批次。"
    graph, llm, chunk = knowledge_graph(
        lambda c: json.dumps({"quotes": [{"chunk_id": c.chunk_id, "quote": quote}]})
    )
    result = await ask(graph)
    assert result["final_answer"].startswith("根据已导入资料：")
    assert quote in result["final_answer"]
    assert "模型辅助选择" not in result["final_answer"]
    assert result["citations"][0].chunk_id == chunk.chunk_id
    assert "PDF第7页" in result["citations"][0].section
    assert "test-v2" in result["citations"][0].section
    assert result["token_usage"].total_tokens == 8
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    "reply",
    [
        "这款原料的保质期为12个月，可以在4摄氏度保存。",
        '{"answer":"保质期为12个月，可以替代另一款原料"}',
        '{"quotes":[]}',
        '{"quotes":null}',
        '{"quotes":[null]}',
        "[]",
    ],
)
async def test_free_text_or_malformed_model_output_falls_back_without_invention(
    knowledge_graph, reply
):
    graph, _, _ = knowledge_graph(reply)
    result = await ask(graph)
    answer = result["final_answer"]
    assert "已降级为原文节选（非生成式回答）" in answer
    assert "入库时核对箱体批次" in answer
    assert "12个月" not in answer
    assert "4摄氏度" not in answer
    assert "可以替代" not in answer


@pytest.mark.parametrize(
    "quote,wrong_id",
    [
        ("入库时请核对箱体批次。", False),
        ("保质期为12个月。", False),
        ("入库时核对箱体批次。", True),
        (" ", False),
    ],
)
async def test_nonverbatim_or_wrong_source_quote_cannot_be_presented_as_verified(
    knowledge_graph, quote, wrong_id
):
    graph, _, _ = knowledge_graph(
        lambda c: json.dumps(
            {"quotes": [{"chunk_id": "wrong-source" if wrong_id else c.chunk_id, "quote": quote}]}
        )
    )
    result = await ask(graph)
    assert "已降级为原文节选" in result["final_answer"]
    assert "根据已导入资料：" not in result["final_answer"]


async def test_mixed_quotes_only_retain_verbatim_supported_content(knowledge_graph):
    graph, _, _ = knowledge_graph(
        lambda c: json.dumps(
            {
                "quotes": [
                    {"chunk_id": c.chunk_id, "quote": "入库时核对箱体批次。"},
                    {"chunk_id": c.chunk_id, "quote": "保质期为12个月。"},
                ]
            }
        )
    )
    result = await ask(graph)
    assert "根据已导入资料：" in result["final_answer"]
    assert "入库时核对箱体批次。" in result["final_answer"]
    assert "12个月" not in result["final_answer"]


async def test_offline_provider_is_explicitly_labeled_and_not_called(knowledge_graph):
    graph, llm, _ = knowledge_graph(offline=True)
    result = await ask(graph)
    assert "原文节选（非生成式回答）" in result["final_answer"]
    assert "模型辅助选择" not in result["final_answer"]
    assert llm.calls == []


async def test_reference_document_retains_noncurrent_caveat_even_with_valid_quote(knowledge_graph):
    graph, _, _ = knowledge_graph(
        lambda c: json.dumps(
            {"quotes": [{"chunk_id": c.chunk_id, "quote": "入库时核对箱体批次。"}]}
        ),
        status="reference",
    )
    result = await ask(graph)
    assert "现行有效性待确认" in result["final_answer"]
    assert "不能作为现行制度或实时业务数据" in result["final_answer"]


async def test_unknown_product_code_cannot_fall_back_to_unrelated_product_specs(
    knowledge_graph, tmp_path
):
    content = "产品规格：隔离测试面粉为12kg/箱；这是另一个商品的资料。"
    (tmp_path / "isolated-jiahui/knowledge/receiving.txt").write_text(content, encoding="utf-8")
    graph, llm, _ = knowledge_graph(offline=True)
    result = await graph.ainvoke(
        {"user_query": "编号999999规格", "extra": {"scopes": ["products", "knowledge"]}}
    )
    assert "找到依据" in result["final_answer"]
    assert "尚未录入" in result["final_answer"] or "需要确认" in result["final_answer"]
    assert "12kg/箱" not in result["final_answer"]
    assert result["citations"] == []
    assert llm.calls == []


async def test_fullwidth_spec_selects_only_matching_product_not_shorter_suffix(
    knowledge_graph, tmp_path
):
    graph, _, _ = knowledge_graph(offline=True)
    source = tmp_path / "isolated-specs.csv"
    with source.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        writer.writeheader()
        for identity, spec in [("p25", "25KG/包"), ("p5", "5KG/包")]:
            writer.writerow(
                {
                    "内部记录ID": identity,
                    "商品编号": "001",
                    "名称": "隔离测试同名面粉",
                    "规格": spec,
                    "版本": "test-v1",
                    "来源文件": "测试规格.pdf",
                    "来源位置": f"PDF第4页，记录{identity}",
                }
            )
    import_csv(source, tmp_path / "isolated-jiahui", status="current")
    result = await graph.ainvoke(
        {"user_query": "编号001 ２５ｋｇ／包", "extra": {"scopes": ["products", "knowledge"]}}
    )
    assert "规格：25KG/包" in result["final_answer"]
    assert "规格：5KG/包" not in result["final_answer"]
    assert "请先选择" not in result["final_answer"]
    assert [citation.document_id for citation in result["citations"]] == ["p25"]


async def test_reference_without_requested_shelf_life_reports_missing_evidence(
    knowledge_graph, tmp_path
):
    content = "产品资料说明：本页仅提供名称与包装规格。"
    (tmp_path / "isolated-jiahui/knowledge/receiving.txt").write_text(content, encoding="utf-8")
    graph, llm, _ = knowledge_graph(offline=True, status="reference")
    result = await graph.ainvoke({"user_query": "产品保质期", "extra": {"scopes": ["knowledge"]}})
    answer = result["final_answer"]
    assert "保质期依据" in answer
    assert "尚未录入或需要业务确认" in answer
    assert "不能按品类推测" in answer
    assert "原文节选" not in answer
    assert content not in answer
    assert llm.calls == []


async def test_unspecified_this_product_requires_name_code_or_spec(knowledge_graph):
    graph, llm, _ = knowledge_graph(offline=True)
    result = await graph.ainvoke(
        {"user_query": "这款产品的储存条件是什么？", "extra": {"scopes": ["products", "knowledge"]}}
    )
    answer = result["final_answer"]
    assert "请先提供具体商品名称、编号、品牌或规格" in answer
    assert "原文节选" not in answer
    assert result["citations"] == []
    assert llm.calls == []


@pytest.mark.parametrize(
    "status, expected",
    [
        ("deepseek", "已连接DeepSeek"),
        ("deepseek_unavailable", "真实调用失败"),
        ("deepseek_unverified", "尚未验证"),
        ("offline_excerpt", "没有连接可用"),
    ],
)
def test_connection_answer_uses_probe_status_not_knowledge(status, expected):
    from types import SimpleNamespace

    from app.jiahui.graph import model_status_answer

    retriever = SimpleNamespace(capabilities={"llm": status, "embedding": "hash"})
    assert expected in model_status_answer("查一下模型链接", retriever)
    assert model_status_answer("蛋液有哪些", retriever) is None
