"""Synthetic PDF import regressions, including partial/scanned documents."""

from __future__ import annotations

import base64
import io
import json
import secrets

import pytest

pytest.importorskip("pypdf")
from pypdf import PdfWriter  # noqa: E402
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject  # noqa: E402

from app.jiahui.pdf_import import PDFImportError, _readable_text, import_pdf  # noqa: E402


def make_pdf(pages: list[str | None], *, password: str = "") -> bytes:
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=595, height=842)
        if text:
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
            stream.set_data(("BT /F1 12 Tf 50 750 Td (" + text + ") Tj ET").encode("ascii"))
            page[NameObject("/Contents")] = writer._add_object(stream)
    if password:
        writer.encrypt(password)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_partial_pdf_keeps_readable_pages_and_reports_scanned_page(tmp_path):
    content = make_pdf(["Isolated flour product code 00001 specifications 25kg", None])
    report = import_pdf(content, tmp_path, filename="isolated.pdf", version="demo")
    assert report["pages"] == 2 and report["imported_pages"] == 1 and report["partial"]
    assert report["skipped_pages"][0]["page"] == 2
    assert report["skipped_pages"][0]["reason"] == "empty_or_scanned"
    assert "第2页" in report["warnings"][0]
    assert report["text_characters"] > 10 and report["source_saved"]
    root = tmp_path / "sources" / "pdf" / report["source_sha256"]
    assert (root / "isolated.pdf").read_bytes() == content
    entries = json.loads((tmp_path / "knowledge" / "manifest.json").read_text())
    assert (
        len(entries) == 1
        and entries[0]["status"] == "reference"
        and entries[0]["location"] == "PDF第1页"
    )
    assert not (tmp_path / "products.json").exists()
    again = import_pdf(content, tmp_path, filename="isolated.pdf", version="demo")
    assert again["imported_pages"] == 1 and again["changed_pages"] == 0
    assert len(json.loads((tmp_path / "knowledge" / "manifest.json").read_text())) == 1


def test_all_scanned_pdf_has_specific_error_and_preserves_original(tmp_path):
    content = make_pdf([None, None])
    with pytest.raises(PDFImportError, match="0/2页") as caught:
        import_pdf(content, tmp_path, filename="scan.pdf")
    assert len(caught.value.report["skipped_pages"]) == 2
    root = tmp_path / "sources" / "pdf" / caught.value.report["source_sha256"]
    assert (root / "scan.pdf").read_bytes() == content
    assert (root / "import-report.json").exists()
    assert not (tmp_path / "knowledge" / "manifest.json").exists()


def test_layout_failure_uses_plain_text_fallback():
    class Page:
        def extract_text(self, *, extraction_mode):
            if extraction_mode == "layout":
                raise ValueError("unsupported layout")
            return "plain readable product text"

    assert _readable_text(Page()) == ("plain readable product text", "plain")


def test_layout_empty_uses_plain_text_fallback():
    class Page:
        def extract_text(self, *, extraction_mode):
            return "" if extraction_mode == "layout" else "plain readable product text"

    assert _readable_text(Page())[1] == "plain"


def test_corrupt_page_cannot_hide_other_pages(tmp_path, monkeypatch):
    import app.jiahui.pdf_import as module

    original = module._readable_text
    count = 0

    def read(page):
        nonlocal count
        count += 1
        if count == 1:
            raise ValueError("page extraction unsupported")
        return original(page)

    monkeypatch.setattr(module, "_readable_text", read)
    report = import_pdf(
        make_pdf(["Bad page", "Other readable product"]), tmp_path, filename="partial.pdf"
    )
    assert report["imported_pages"] == 1 and report["skipped_pages"][0]["page"] == 1
    entries = json.loads((tmp_path / "knowledge" / "manifest.json").read_text())
    assert entries[0]["location"] == "PDF第2页"


def test_multiple_versions_warn_and_keep_prior_reference(tmp_path):
    import_pdf(make_pdf(["Version A product"]), tmp_path, filename="catalog.pdf", version="A")
    report = import_pdf(
        make_pdf(["Version B product"]), tmp_path, filename="catalog.pdf", version="B"
    )
    assert any("同名来源" in warning for warning in report["warnings"])
    entries = json.loads((tmp_path / "knowledge" / "manifest.json").read_text())
    assert {entry["version"] for entry in entries} == {"A", "B"}


def test_corrupt_retained_source_is_not_silently_overwritten(tmp_path):
    content = make_pdf(["Unchanged original text"])
    report = import_pdf(content, tmp_path, filename="catalog.pdf")
    root = tmp_path / "sources" / "pdf" / report["source_sha256"]
    (root / "page-001.txt").write_text("Local operator changed file")
    with pytest.raises(PDFImportError, match="校验不一致"):
        import_pdf(content, tmp_path, filename="catalog.pdf")
    assert (root / "page-001.txt").read_text() == "Local operator changed file"


@pytest.mark.parametrize(
    "content,message", [(b"not a pdf", "不是可识别的PDF"), (b"%PDF-invalid", "无法读取")]
)
def test_invalid_pdf_has_actionable_error(tmp_path, content, message):
    with pytest.raises(ValueError, match=message):
        import_pdf(content, tmp_path, filename="invalid.pdf")


def test_encrypted_pdf_is_rejected_without_claiming_success(tmp_path):
    with pytest.raises(ValueError, match="加密"):
        import_pdf(
            make_pdf(["Private contents"], password="isolated-only"),
            tmp_path,
            filename="encrypted.pdf",
        )


def test_desktop_route_partial_import_and_empty_csv_errors(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sse_starlette.sse import AppStatus

    from app.jiahui.auth import AuthStore
    from app.jiahui.desktop_api import create_desktop_app

    monkeypatch.setattr(AppStatus, "should_exit_event", None)
    password = secrets.token_urlsafe(24)
    store = AuthStore(tmp_path / "data")
    store.create_user("operator", password, "admin")
    with TestClient(create_desktop_app(tmp_path / "home", tmp_path / "data")) as client:
        assert (
            client.post(
                "/api/v1/auth/login", json={"username": "operator", "password": password}
            ).status_code
            == 200
        )
        response = client.post(
            "/api/v1/desktop/import/pdf",
            json={
                "filename": "partial.pdf",
                "content": base64.b64encode(make_pdf(["Product code 00001", None])).decode(),
            },
        )
        assert response.status_code == 200 and response.json()["imported_pages"] == 1
        empty = client.post(
            "/api/v1/desktop/import/products",
            json={"filename": "empty.csv", "content": "内部记录ID,名称\n"},
        )
        assert empty.status_code == 400 and "只有表头" in empty.json()["detail"]
        settings = client.get("/api/v1/desktop/settings").json()
        assert (
            settings["knowledge_document_count"] == 2
            and settings["knowledge_file_count"] == 2
            and settings["reference_document_count"] == 2
        )
        all_docs = client.get("/api/v1/desktop/documents").json()["documents"]
        assert any("AI整理" in row["title"] for row in all_docs)
        listing = [row for row in all_docs if row["source_file"] == "partial.pdf"]
        assert (
            len(listing) == 1
            and listing[0]["source_file"] == "partial.pdf"
            and listing[0]["location"] == "PDF第1页"
        )
        assert set(listing[0]) == {"id", "title", "source_file", "location", "version", "status"}
