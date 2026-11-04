"""Real synthetic PDF table import, restart repair and accurate inventory reporting."""

import base64
import hashlib
import json
import secrets

import pytest
from fastapi.testclient import TestClient
from sse_starlette.sse import AppStatus

pytest.importorskip("pdfplumber")
pytest.importorskip("pypdf")
from app.jiahui.auth import AuthStore  # noqa: E402
from app.jiahui.catalog import Catalog  # noqa: E402
from app.jiahui.desktop_api import create_desktop_app  # noqa: E402
from app.jiahui.pdf_catalog import repair_pdf_catalogs  # noqa: E402
from scripts.jiahui_pdf_fixture import synthetic_catalog_pdf  # noqa: E402


def test_pdf_products_are_actually_queryable_and_restart_reuses_preserved_sources(
    tmp_path, monkeypatch
):
    data, home = tmp_path / "data", tmp_path / "home"
    password = secrets.token_urlsafe(24)
    AuthStore(data).create_user("operator", password, "admin")
    content = synthetic_catalog_pdf()
    body = {"filename": "test-only.pdf", "content": base64.b64encode(content).decode()}
    for cycle in range(2):
        monkeypatch.setattr(AppStatus, "should_exit_event", None)
        with TestClient(create_desktop_app(home, data)) as client:
            assert not client.get("/api/v1/desktop/status").json()["setup_required"]
            assert (
                client.post(
                    "/api/v1/auth/login", json={"username": "operator", "password": password}
                ).status_code
                == 200
            )
            if not cycle:
                result = client.post("/api/v1/desktop/import/pdf", json=body)
                assert result.status_code == 200 and result.json()["catalog_imported"] == 2
            else:
                result = client.post("/api/v1/desktop/import/pdf", json=body)
                assert not result.json()["catalog_changed"]
            profile = client.get("/api/v1/profile").json()
            assert profile["product_count"] == 2 and profile["reference_product_count"] == 2
            overview = client.post("/api/v1/chat", json={"message": "没资料吗"}).json()
            assert "有资料" in overview["answer"] and "2 条产品" in overview["answer"]
            assert "test-only.pdf" in overview["answer"] and "AI整理" in overview["answer"]
            query = client.post("/api/v1/chat", json={"message": "编号00009"}).json()
            assert "请先选择" in query["answer"] and len(query["citations"]) == 2
            selected = client.post("/api/v1/chat", json={"message": "编号00009 25kg/包"}).json()
            assert (
                "商品编号：00009" in selected["answer"] and "现行有效性待确认" in selected["answer"]
            )
            assert "PDF第1页" in selected["citations"][0]["section"]
        if not cycle:
            # Simulate the old software's PDF-only state, using test data only.
            (data / "products.json").unlink()
    assert len(Catalog.load(data).records) == 2
    assert repair_pdf_catalogs(data)["reused_files"] == 1


def test_repair_does_not_reactivate_deactivated_pdf_or_change_confirmed_record(tmp_path):
    from app.jiahui.pdf_import import import_pdf

    content = synthetic_catalog_pdf()
    import_pdf(content, tmp_path, filename="isolated.pdf")
    path = tmp_path / "products.json"
    records = json.loads(path.read_text())
    records["records"][0]["status"] = "current"
    path.write_text(json.dumps(records, ensure_ascii=False))
    state = tmp_path / "sources/pdf" / hashlib.sha256(content).hexdigest() / "catalog-state.json"
    state.unlink()
    repair_pdf_catalogs(tmp_path)
    assert sum(r["status"] == "current" for r in Catalog.load(tmp_path).records) == 1
    manifest = tmp_path / "knowledge/manifest.json"
    entries = json.loads(manifest.read_text())
    for entry in entries:
        entry["status"] = "historical"
    manifest.write_text(json.dumps(entries))
    state.unlink()
    result = repair_pdf_catalogs(tmp_path)
    assert result["skipped_files"] == 1 and result["repaired_files"] == 0
