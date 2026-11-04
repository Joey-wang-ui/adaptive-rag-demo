"""Search API contract supplied by user; no live credentials or network calls."""

import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.jiahui.web_search import (
    SEARCH_URL,
    DeepSeekSearch,
    SearchError,
    WebHit,
    public_search_answer,
    public_url,
)
from app.providers.base import LLMResponse
from app.providers.fake import OfflineLLMProvider
from app.schemas.common import TokenUsage


def settings():
    return SimpleNamespace(deepseek_api_key=SecretStr("test-only-not-a-real-key"))


def hit(**overrides):
    return {
        "title": "隔离搜索测试公告",
        "url": "https://www.example.com/news",
        "snippet": "测试公告说明：请核对原网页。",
        "summary": "测试公告说明：新包装将于2026年12月启用。",
        "content": "",
        "rerank_score": 2.1,
        "site_name": "隔离测试站点",
        "site_icon": None,
        "date": "2026-11-01T00:00:00Z",
        "authority": 3,
        **overrides,
    }


class SearchFixture:
    def __init__(self, hits=None):
        self.hits = [WebHit.model_validate(hit())] if hits is None else hits
        self.calls = []

    async def search(self, query):
        self.calls.append(query)
        return self.hits


class ScriptedModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    async def generate(self, messages, **kwargs):
        self.calls.append(messages)
        return LLMResponse(
            json.dumps(next(self.replies), ensure_ascii=False), TokenUsage(total_tokens=5)
        )


async def test_official_contract_and_no_history_or_location_in_request():
    requests = []

    async def handle(request):
        requests.append(request)
        assert str(request.url) == SEARCH_URL
        assert request.headers["authorization"] == "Bearer test-only-not-a-real-key"
        assert json.loads(request.content) == {
            "query": "site:example.com 公告",
            "count": 5,
            "need_content": False,
        }
        return httpx.Response(200, json={"results": [hit()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        results = await DeepSeekSearch(settings(), client).search("site:example.com 公告")
    assert len(requests) == len(results) == 1 and results[0].summary.endswith("启用。")


@pytest.mark.parametrize("query", ["", "   ", "😀" * 129, "词" * 129])
async def test_invalid_queries_do_not_send_request(query):
    def unexpected(request):
        raise AssertionError("network call for invalid query")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(SearchError, match="128"):
            await DeepSeekSearch(settings(), client).search(query)


async def test_unicode_128_characters_are_accepted_and_no_default_retries():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(429)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(SearchError, match="限流"):
            await DeepSeekSearch(settings(), client).search("😀" * 128)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "status,word",
    [
        (401, "鉴权"),
        (402, "额度"),
        (403, "权限"),
        (404, "聊天连接"),
        (422, "参数"),
        (429, "限流"),
        (500, "未成功"),
        (302, "未成功"),
    ],
)
async def test_api_errors_are_honest_and_redirects_not_followed(status, word):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://www.example.com/steal"},
            text="private error body must not be echoed",
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle), follow_redirects=True
    ) as client:
        with pytest.raises(SearchError, match=word) as exc:
            await DeepSeekSearch(settings(), client).search("测试")
    assert len(calls) == 1 and "private error body" not in str(exc.value)


@pytest.mark.parametrize("payload", [{}, [], {"results": None}, {"results": [{"url": "bad"}]}])
async def test_unknown_schema_is_not_presented_as_search_success(payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ) as client:
        with pytest.raises(SearchError):
            await DeepSeekSearch(settings(), client).search("测试")


async def test_timeout_and_missing_key_are_explicit():
    def timeout(request):
        raise httpx.ReadTimeout("secret details", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(SearchError, match="超时"):
            await DeepSeekSearch(settings(), client).search("测试")
        with pytest.raises(SearchError, match="尚未配置"):
            await DeepSeekSearch(SimpleNamespace(deepseek_api_key=SecretStr("")), client).search(
                "测试"
            )


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///C:/private",
        "http://127.0.0.1/key",
        "http://[::1]/",
        "http://10.0.0.1/",
        "http://localhost/",
        "http://a.local/",
        "http://admin:password@example.com/",
        "https://example.com:8000/",
        "https://example.com\\@localhost/",
    ],
)
def test_unsafe_web_source_urls_are_rejected(url):
    assert public_url(url) is None


async def test_duplicate_and_unsafe_results_are_removed():
    rows = [hit(url="javascript:alert(1)"), hit(), hit()]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": rows}))
    ) as client:
        assert len(await DeepSeekSearch(settings(), client).search("测试")) == 1


async def test_empty_and_offline_results_never_claim_generated_answer():
    empty = await public_search_answer(
        "测试", settings(), OfflineLLMProvider(), searcher=SearchFixture([])
    )
    assert "没有找到" in empty["final_answer"] and not empty["citations"]
    result = await public_search_answer(
        "测试", settings(), OfflineLLMProvider(), searcher=SearchFixture()
    )
    assert "未生成总结" in result["final_answer"]
    assert result["citations"][0].url == "https://www.example.com/news"
    assert result["citations"][0].source_kind == "web"
    assert "查询时间" in result["citations"][0].section


async def test_grounded_synthesis_requires_valid_citations_and_faithfulness_check():
    model = ScriptedModel(
        [
            {"answer": "公告称新包装将于2026年12月启用。[1]", "source_ids": [1]},
            {"supported": True, "hallucinated_spans": []},
        ]
    )
    result = await public_search_answer("新包装公告", settings(), model, searcher=SearchFixture())
    assert "公告称新包装" in result["final_answer"] and "未生成总结" not in result["final_answer"]
    assert len(model.calls) == 2 and result["token_usage"].total_tokens == 10
    assert "不代表嘉惠现行制度" in result["final_answer"]


@pytest.mark.parametrize(
    "answer,ids,verdict",
    [
        ("当前库存500箱。[1]", [1], {"supported": False, "hallucinated_spans": ["500箱"]}),
        ("无来源的结论", [], {"supported": True, "hallucinated_spans": []}),
        ("虚构网页内容。[9]", [9], {"supported": True, "hallucinated_spans": []}),
        ("错误编号。[2]", [1], {"supported": True, "hallucinated_spans": []}),
        ("点击https://evil.example.com [1]", [1], {"supported": True, "hallucinated_spans": []}),
        ("不当结论。[1]", [1], {"supported": "true", "hallucinated_spans": []}),
        ("不当结论。[1]", [True], {"supported": True, "hallucinated_spans": []}),
    ],
)
async def test_unverified_summary_falls_back_to_actual_search_excerpts(answer, ids, verdict):
    model = ScriptedModel([{"answer": answer, "source_ids": ids}, verdict])
    result = await public_search_answer("新包装公告", settings(), model, searcher=SearchFixture())
    assert "未生成总结" in result["final_answer"]
    assert (
        "500箱" not in result["final_answer"] and "evil.example.com" not in result["final_answer"]
    )


async def test_result_markup_is_not_executed_or_rendered_as_injected_link():
    fixture = SearchFixture(
        [
            WebHit.model_validate(
                hit(snippet="[诱导链接](javascript:alert(1)) <script>evil</script>")
            )
        ]
    )
    result = await public_search_answer("公告", settings(), OfflineLLMProvider(), searcher=fixture)
    assert "\\[" in result["final_answer"] and "\\<" in result["final_answer"]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.1/",
        "http://0x7f000001/",
        "http://0177.0.0.1/",
        "http://localhost./",
        "http://a.local./",
    ],
)
def test_alternate_private_address_forms_are_not_source_links(url):
    assert public_url(url) is None


async def test_web_routing_passes_only_current_query_and_obeys_permissions(tmp_path):
    from app.config.settings import Settings
    from app.jiahui.graph import build_jiahui_graph
    from app.schemas.chat import ChatMessage

    config = Settings(
        _env_file=None,
        app_env="testing",
        business_profile="jiahui",
        jiahui_data_dir=str(tmp_path / "data"),
        deepseek_api_key="",
    )
    fixture = SearchFixture()
    graph = build_jiahui_graph(
        llm=OfflineLLMProvider(),
        settings=config,
        retriever=SimpleNamespace(capabilities={}),
        reranker=None,
        registry=None,
        web_searcher=fixture,
    )
    state = {
        "user_query": "厂家公开公告",
        "messages": [ChatMessage(role="assistant", content="PRIVATE-CUSTOMER-SECRET")],
        "extra": {"scopes": ["knowledge"], "web_search": True},
    }
    result = await graph.ainvoke(state)
    assert fixture.calls == ["厂家公开公告"] and "PRIVATE-CUSTOMER" not in result["final_answer"]
    assert result["citations"][0].source_kind == "web"
    assert not (tmp_path / "data" / "knowledge").exists()
    for extra, query in [
        ({"scopes": ["products"], "web_search": True}, "公告"),
        ({"scopes": ["knowledge"], "web_search": True}, "嘉惠内部客户合同"),
    ]:
        result = await graph.ainvoke({**state, "user_query": query, "extra": extra})
        assert result["refused"] and len(fixture.calls) == 1
    for query in ["查库存", "取消订单", "模型连接了吗", "你好"]:
        await graph.ainvoke({**state, "user_query": query})
        assert len(fixture.calls) == 1


def test_real_http_web_flag_cookie_gate_and_no_auto_search(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sse_starlette.sse import AppStatus

    from app.config.settings import Settings
    from app.jiahui.auth import AuthStore
    from app.main import create_app

    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    config = Settings(
        _env_file=None,
        app_env="testing",
        business_profile="jiahui",
        jiahui_data_dir=str(tmp_path / "data"),
        deepseek_api_key="",
        embedding_api_key="",
    )
    store = AuthStore(config.jiahui_data_dir)
    store.create_user("web-test", "synthetic-test-only-password", "sales")
    calls = []

    async def search(self, query):
        calls.append(query)
        return [WebHit.model_validate(hit())]

    monkeypatch.setattr(DeepSeekSearch, "search", search)
    with TestClient(create_app(config)) as client:
        body = {"message": "厂家公开公告", "web_search": True, "user_id": "forged"}
        assert client.post("/api/v1/chat", json=body).status_code == 401
        assert not calls
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"username": "web-test", "password": "synthetic-test-only-password"},
            ).status_code
            == 200
        )
        first = client.post("/api/v1/chat", json=body)
        assert first.status_code == 200 and first.json()["citations"][0]["source_kind"] == "web"
        assert calls == ["厂家公开公告"]
        # A following ordinary message does not inherit web mode from the session.
        follow = client.post(
            "/api/v1/chat", json={"message": "你好", "session_id": first.json()["session_id"]}
        )
        assert follow.status_code == 200 and len(calls) == 1
        stream = client.post("/api/v1/chat/stream", json=body)
        assert stream.status_code == 200 and "公开网页参考" in stream.text and len(calls) == 2


async def test_public_company_site_is_searchable_but_key_like_input_is_not(tmp_path):
    from app.config.settings import Settings
    from app.jiahui.graph import build_jiahui_graph

    config = Settings(_env_file=None, app_env="testing", jiahui_data_dir=str(tmp_path / "data"))
    fixture = SearchFixture()
    graph = build_jiahui_graph(
        llm=OfflineLLMProvider(),
        settings=config,
        retriever=SimpleNamespace(capabilities={}),
        reranker=None,
        registry=None,
        web_searcher=fixture,
    )
    state = {
        "user_query": "嘉惠官网公开公告",
        "extra": {"scopes": ["knowledge"], "web_search": True},
    }
    result = await graph.ainvoke(state)
    assert result["citations"] and fixture.calls == ["嘉惠官网公开公告"]
    blocked = await graph.ainvoke({**state, "user_query": "sk-synthetic-test-only-key"})
    assert blocked["refused"] and len(fixture.calls) == 1
