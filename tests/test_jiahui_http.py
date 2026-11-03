"""Real-container HTTP regression tests using only temporary, isolated business data."""

import csv
import json
import secrets
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sse_starlette.sse import AppStatus

from app.config.settings import Settings, clear_settings_cache
from app.jiahui.auth import AuthStore
from app.jiahui.catalog import FIELDS
from app.jiahui.import_catalog import import_csv
from app.main import create_app


def import_products(root: Path, path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=[*FIELDS, "状态", "访问范围"])
        writer.writeheader()
        writer.writerows(rows)
    import_csv(path, root, status="current")


def product(identity="test-p1", **fields):
    return {
        "内部记录ID": identity,
        "商品编号": "00001",
        "名称": "隔离测试蛋黄液",
        "品牌": "测试品牌",
        "规格": "1kg/盒",
        "储存条件": "冷藏",
        "来源文件": "隔离测试目录.pdf",
        "来源位置": "PDF第5页，表格第34行",
        "版本": "test-v1",
        "访问范围": "products",
        **fields,
    }


@pytest.fixture
def environment(tmp_path, monkeypatch):
    # sse-starlette keeps this Event at module scope; each TestClient owns a new loop.
    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    root = tmp_path / "private-jiahui"
    monkeypatch.setenv("BUSINESS_PROFILE", "jiahui")
    monkeypatch.setenv("JIAHUI_DATA_DIR", str(root))
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("LLM_BACKEND", "auto")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("EMBEDDING_API_KEY", "")
    monkeypatch.setenv("LOCAL_EMBEDDING_FORCE", "false")
    monkeypatch.setenv("CROSS_ENCODER_FORCE", "false")
    clear_settings_cache()
    settings = Settings(_env_file=None)
    assert Path(settings.knowledge_dir) == root / "knowledge"
    store = AuthStore(root)
    password = secrets.token_urlsafe(24)
    store.create_user("reader", password, "sales", frozenset({"products", "knowledge"}))
    store.create_user(
        "buyer",
        password,
        "procurement",
        frozenset(
            {"products", "products:procurement", "knowledge", "prices", "inventory", "orders"}
        ),
    )
    store.create_user("no-products", password, "finance", frozenset({"knowledge"}))
    yield root, settings, store, password
    clear_settings_cache()


def login(client, password, username="reader"):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text


def chat(client, message, **fields):
    response = client.post("/api/v1/chat", json={"message": message, **fields})
    assert response.status_code == 200, response.text
    return response.json()


def test_empty_catalog_starts_and_requires_server_authentication(environment):
    root, settings, _, password = environment
    assert not (root / "products.json").exists()
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/health").status_code == 200
        assert client.post("/api/v1/chat", json={"message": "产品资料"}).status_code == 401
        assert client.post("/api/v1/chat/stream", json={"message": "产品资料"}).status_code == 401
        login(client, password)
        answer = chat(client, "查询面粉产品资料")
        assert "尚未录入产品资料" in answer["answer"]
        assert answer["citations"] == []
        assert "七天无理由" not in answer["answer"]
    assert not (root / "products.json").exists()


def test_http_import_leading_zero_duplicate_code_clarification_and_sources(environment, tmp_path):
    root, settings, _, password = environment
    import_products(
        root, tmp_path / "isolated.csv", [product(), product("test-p2", 规格="10kg/包")]
    )
    with TestClient(create_app(settings)) as client:
        login(client, password)
        ambiguous = chat(client, "商品编号00001的规格是什么？")
        assert "请先选择" in ambiguous["answer"]
        assert "1kg/盒" in ambiguous["answer"] and "10kg/包" in ambiguous["answer"]
        assert len(ambiguous["citations"]) == 2
        selected = chat(client, "查询00001的1kg/盒资料")
        assert "商品编号：00001" in selected["answer"]
        assert "保质期：尚未录入/需要确认" in selected["answer"]
        assert "10kg/包" not in selected["answer"]
        assert "PDF第5页" in selected["citations"][0]["section"]
        assert "第2行" in selected["citations"][0]["section"]
        assert "test-v1" in selected["citations"][0]["section"]
        comparison = chat(client, "对比00001的两个规格")
        assert "1kg/盒" in comparison["answer"] and "10kg/包" in comparison["answer"]
        missing = chat(client, "商品编号99999资料")
        assert "尚未录入" in missing["answer"] or "需要确认" in missing["answer"]
        assert missing["citations"] == []


def test_server_identity_overrides_payload_and_rejects_foreign_session(environment):
    root, settings, _, password = environment
    with TestClient(create_app(settings)) as client:
        login(client, password)
        first = chat(client, "你好", user_id="buyer")
        with sqlite3.connect(root / "conversations.db") as db:
            owner = db.execute(
                "SELECT user_id FROM sessions WHERE session_id=?", (first["session_id"],)
            ).fetchone()[0]
        assert owner == "reader"
        login(client, password, "buyer")
        denied = client.post(
            "/api/v1/chat",
            json={
                "message": "你好",
                "session_id": first["session_id"],
                "user_id": "reader",
            },
        )
        assert denied.status_code in {401, 403}
        assert "无权访问" in denied.text
        stream_denied = client.post(
            "/api/v1/chat/stream",
            json={"message": "你好", "session_id": first["session_id"], "user_id": "reader"},
        )
        assert stream_denied.status_code in {401, 403}
        assert "无权访问" in stream_denied.text


def test_scope_filter_before_retrieval_and_same_question_after_scope_change(environment, tmp_path):
    root, settings, store, password = environment
    import_products(
        root,
        tmp_path / "isolated.csv",
        [
            product(),
            product(
                "secret-p", 商品编号="00002", 名称="采购秘密奶油", 访问范围="products:procurement"
            ),
        ],
    )
    with TestClient(create_app(settings)) as client:
        login(client, password, "buyer")
        question = "商品编号00002的产品资料"
        visible = chat(client, question)
        assert "采购秘密奶油" in visible["answer"]
        store.set_access("buyer", "procurement", frozenset({"products", "knowledge"}))
        hidden = chat(
            client, question, session_id=visible["session_id"], user_id="admin", scopes=["*"]
        )
        assert "采购秘密奶油" not in hidden["answer"]
        assert hidden["citations"] == []
        login(client, password, "no-products")
        unauthorized = chat(
            client, "商品编号00001的产品资料", user_id="reader", scopes=["products"]
        )
        assert "隔离测试蛋黄液" not in unauthorized["answer"]
        assert unauthorized["citations"] == []


def test_stream_returns_final_answer_and_sources(environment, tmp_path):
    root, settings, _, password = environment
    import_products(root, tmp_path / "isolated.csv", [product()])
    with TestClient(create_app(settings)) as client:
        login(client, password)
        with client.stream(
            "POST", "/api/v1/chat/stream", json={"message": "查询商品编号00001", "user_id": "admin"}
        ) as response:
            assert response.status_code == 200
            assert "text/event-stream" in response.headers["content-type"]
            events = [
                json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")
            ]
        final = next(event["data"] for event in events if event["event"] == "answer")
        assert "商品编号：00001" in final["answer"]
        assert final["citations"][0]["document_id"] == "test-p1"
        assert events[-1]["event"] == "done"


def test_business_data_is_unconnected_and_business_writes_are_rejected(environment):
    _, settings, _, password = environment
    with TestClient(create_app(settings)) as client:
        login(client, password, "buyer")
        for question in ["查询奶油当前价格", "奶油有多少库存", "查询订单ORD10001"]:
            answer = chat(client, question)
            assert answer["answer"] == "尚未接入业务数据"
            assert answer["pending_action"] is None
        for question in ["取消订单ORD10001", "申请退款", "扣减库存"]:
            answer = chat(client, question)
            assert answer["refused"] is True
            assert "不开放业务写操作" in answer["answer"]
            assert answer["pending_action"] is None


def test_knowledge_permissions_versions_and_source_citations(environment):
    root, settings, _, password = environment
    knowledge = root / "knowledge"
    knowledge.mkdir()
    documents = [
        ("current", "current", "knowledge", "", "测试验收规则：入库时核对箱体批次。"),
        ("old", "historical", "knowledge", "", "测试验收规则：旧制度秘密文本。"),
        ("future", "current", "knowledge", "2999-01-01", "测试验收规则：未来制度秘密文本。"),
        ("private", "current", "knowledge:finance", "", "测试验收规则：财务秘密文本。"),
    ]
    manifest = []
    for name, status, scope, effective, content in documents:
        (knowledge / f"{name}.md").write_text(content, encoding="utf-8")
        manifest.append(
            {
                "id": name,
                "path": f"{name}.md",
                "title": "隔离知识测试",
                "scope": scope,
                "status": status,
                "effective_date": effective,
                "version": "test-v2",
                "source_file": "测试手册.md",
                "location": "第二章§2.1",
            }
        )
    (knowledge / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )
    with TestClient(create_app(settings)) as client:
        login(client, password)
        answer = chat(client, "测试验收规则是什么？")
        assert "入库时核对箱体批次" in answer["answer"]
        assert "非生成式回答" in answer["answer"]
        assert "秘密文本" not in answer["answer"]
        assert len(answer["citations"]) == 1
        section = answer["citations"][0]["section"]
        assert "第二章§2.1" in section and "test-v2" in section
        assert answer["citations"][0]["document_id"] == "current"


def test_profile_counts_only_authorized_rows_and_uncached_permission_changes(environment, tmp_path):
    root, settings, store, password = environment
    import_products(
        root,
        tmp_path / "isolated.csv",
        [
            product(),
            product(
                "secret-p", 商品编号="00002", 名称="采购秘密奶油", 访问范围="products:procurement"
            ),
        ],
    )
    with TestClient(create_app(settings)) as client:
        before = client.get("/api/v1/profile")
        assert before.status_code == 200
        assert before.json()["product_count"] == 0
        assert "请登录" in before.json()["catalog_status"]
        assert before.headers["cache-control"] == "no-store"
        login(client, password, "buyer")
        assert client.get("/api/v1/profile").json()["product_count"] == 2
        store.set_access("buyer", "procurement", frozenset({"knowledge"}))
        after = client.get("/api/v1/profile").json()
        assert after["product_count"] == 0
        assert after["mode"] == "jiahui"
        assert "嘉惠" in after["name"]


def test_business_adapter_and_admin_introspection_enforce_same_server_scopes(environment):
    _, settings, store, password = environment
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/business/prices").status_code == 401
        assert client.get("/api/v1/admin/tools").status_code == 401
        login(client, password)
        assert client.get("/api/v1/business/prices").status_code == 403
        assert client.get("/api/v1/admin/tools").status_code == 403
        store.set_access("reader", "sales", frozenset({"prices", "inventory", "orders", "audit"}))
        for kind in ["prices", "inventory", "orders"]:
            response = client.get(f"/api/v1/business/{kind}", params={"identifier": "00001"})
            assert response.status_code == 200
            assert response.json()["message"] == "尚未接入业务数据"
            assert response.json()["connected"] is False
        tools = client.get("/api/v1/admin/tools")
        assert tools.status_code == 200
        assert tools.json()["tools"] == []
        assert client.get("/api/v1/business/refund").status_code == 404
