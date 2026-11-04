"""Isolated desktop setup/security/import tests; no real model or credentials."""

from __future__ import annotations

import base64
import csv
import io
import secrets
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sse_starlette.sse import AppStatus

from app.jiahui.auth import AuthStore
from app.jiahui.catalog import Catalog
from app.jiahui.desktop_api import create_desktop_app
from scripts.jiahui_desktop import AlreadyRunningError, InstanceLock, data_directory


class FakeVault:
    persistent = True

    def __init__(self):
        self.saved = ""

    def read(self):
        return self.saved

    def write(self, value):
        self.saved = value


@pytest.fixture
def desktop(tmp_path, monkeypatch):
    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    token, password = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    vault = FakeVault()
    app = create_desktop_app(tmp_path / "home", tmp_path / "data", token, vault)
    with TestClient(app) as client:
        yield client, token, password, vault, tmp_path


def initialize(desktop):
    client, token, password, _, _ = desktop
    response = client.post(
        "/api/v1/desktop/setup",
        headers={"x-jiahui-setup": token},
        json={"username": "admin", "password": password},
    )
    assert response.status_code == 200
    return client


def test_bootstrap_requires_private_token_and_never_overwrites_existing_account(desktop):
    client, token, password, _, root = desktop
    assert client.get("/api/v1/desktop/status").json()["setup_required"]
    payload = {"username": "admin", "password": password}
    assert client.post("/api/v1/desktop/setup", json=payload).status_code == 403
    assert (
        client.post(
            "/api/v1/desktop/setup",
            headers={"x-jiahui-setup": token, "origin": "https://attacker.invalid"},
            json=payload,
        ).status_code
        == 403
    )
    initialize(desktop)
    assert client.cookies.get("jiahui_session")
    assert client.get("/api/v1/auth/me").json()["username"] == "admin"
    assert not client.get("/api/v1/desktop/status").json()["setup_required"]
    assert (
        client.post(
            "/api/v1/desktop/setup",
            headers={"x-jiahui-setup": token},
            json={"username": "new-admin", "password": password},
        ).status_code
        == 409
    )
    assert not (root / "home" / ".env").exists()


def test_desktop_settings_and_imports_require_real_admin(desktop):
    client = initialize(desktop)
    client.post("/api/v1/auth/logout")
    assert client.get("/api/v1/desktop/settings").status_code == 401
    assert (
        client.post(
            "/api/v1/desktop/import/products", json={"filename": "a.csv", "content": "abc"}
        ).status_code
        == 401
    )
    root = desktop[-1]
    store = AuthStore(root / "data")
    password = secrets.token_urlsafe(20)
    store.create_user("sales", password, "sales")
    client.post("/api/v1/auth/login", json={"username": "sales", "password": password})
    assert client.get("/api/v1/desktop/settings").status_code == 403
    assert (
        client.post(
            "/api/v1/desktop/model", json={"api_key": "fake-test-not-a-real-key"}
        ).status_code
        == 403
    )


def csv_content():
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output, fieldnames=["内部记录ID", "商品编号", "名称", "规格", "状态", "版本"]
    )
    writer.writeheader()
    for identity, spec in [("TEST-001", "25kg/包"), ("TEST-002", "5kg/包")]:
        writer.writerow(
            {
                "内部记录ID": identity,
                "商品编号": "00001",
                "名称": "【隔离演示】原料",
                "规格": spec,
                "状态": "current",
                "版本": "demo",
            }
        )
    return output.getvalue()


def test_csv_ui_draft_choice_cannot_activate_embedded_current_status_and_repeat_is_safe(desktop):
    client = initialize(desktop)
    body = {"filename": "input.csv", "content": csv_content(), "status": "draft"}
    first = client.post("/api/v1/desktop/import/products", json=body)
    assert first.status_code == 200 and first.json()["imported"] == 2
    root = desktop[-1] / "data"
    catalog = Catalog.load(root)
    assert not catalog.visible_records({"products"})
    assert all(row["code"] == "00001" and row["status"] == "draft" for row in catalog.records)
    assert not client.post("/api/v1/desktop/import/products", json=body).json()["changed"]
    body["status"] = "current"
    assert client.post("/api/v1/desktop/import/products", json=body).status_code == 200
    assert len(Catalog.load(root).visible_records({"products"})) == 2
    assert list((root / "backups").glob("products-*.json"))
    answer = client.post("/api/v1/chat", json={"message": "编号00001"}).json()
    assert "请先选择" in answer["answer"] and answer["citations"]


def pdf_content():
    pytest.importorskip("pypdf")
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 50 750 Td (Isolated reference flour specifications.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return base64.b64encode(output.getvalue()).decode()


def test_pdf_import_retains_pages_reference_status_and_idempotency(desktop):
    client = initialize(desktop)
    body = {"filename": "isolated.pdf", "content": pdf_content(), "version": "test-reference"}
    first = client.post("/api/v1/desktop/import/pdf", json=body)
    assert first.status_code == 200 and first.json()["pages"] == 1
    assert first.json()["status"] == "reference"
    assert client.post("/api/v1/desktop/import/pdf", json=body).json()["changed_pages"] == 0
    settings = client.app.state.container.settings
    chunks = client.app.state.container.retriever.knowledge({"products"})
    assert (
        len(chunks) == 1
        and "PDF第1页" in chunks[0].section
        and chunks[0].metadata["status"] == "reference"
    )
    knowledge_only = client.app.state.container.retriever.knowledge({"knowledge"})
    assert all("AI整理" in chunk.title for chunk in knowledge_only)
    assert not any(chunk.metadata.get("source_file") == "isolated.pdf" for chunk in knowledge_only)
    assert Path(settings.jiahui_data_dir).is_dir()


def test_model_key_is_not_echoed_or_written_to_plain_files(desktop, monkeypatch):
    client = initialize(desktop)

    async def fake_update(container, key):
        container.model_capabilities["llm"] = "deepseek_unavailable"

    monkeypatch.setattr("app.jiahui.desktop_api.update_model", fake_update)
    secret = secrets.token_urlsafe(32)
    response = client.post("/api/v1/desktop/model", json={"api_key": secret, "remember_key": True})
    assert response.status_code == 200 and secret not in response.text
    assert desktop[3].saved == secret
    assert secret not in client.get("/api/v1/desktop/settings").text
    for path in (desktop[-1] / "home").rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


def test_desktop_api_not_exposed_on_normal_server():
    from app.config.settings import Settings
    from app.main import create_app

    app = create_app(Settings(business_profile="jiahui"))
    assert not any(getattr(route, "path", "").startswith("/api/v1/desktop") for route in app.routes)


def test_single_instance_lock_releases_and_default_data_stays_outside_install(tmp_path):
    with InstanceLock(tmp_path):
        with pytest.raises(AlreadyRunningError), InstanceLock(tmp_path):
            pass
    with InstanceLock(tmp_path):
        assert data_directory(tmp_path) == tmp_path / "data"
