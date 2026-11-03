"""Versioned textual source import; test documents never enter business data."""

import json
from pathlib import Path

import pytest

from app.jiahui.import_knowledge import import_knowledge


def source(tmp_path):
    path = tmp_path / "page-04.txt"
    path.write_text("测试产品来源原文。未给出的保质期不可推断。", encoding="utf-8")
    return path


def load(tmp_path):
    return json.loads((tmp_path / "data/knowledge/manifest.json").read_text())


def test_knowledge_import_preserves_original_and_source_location(tmp_path):
    path = source(tmp_path)
    imported = import_knowledge(
        path,
        tmp_path / "data",
        document_id="catalog-page04",
        version="2026-04待确认",
        location="PDF第4页",
        status="reference",
        source_file="目录.pdf",
    )
    assert imported["changed"]
    entry = load(tmp_path)[0]
    assert entry["status"] == "reference"
    assert entry["location"] == "PDF第4页"
    assert entry["source_file"] == "目录.pdf"
    assert entry["effective_date"] == ""
    assert (tmp_path / "data/knowledge" / entry["path"]).read_bytes() == path.read_bytes()


def test_repeat_noop_and_new_version_demotes_prior_version(tmp_path):
    path = source(tmp_path)
    kwargs = dict(
        document_id="handbook",
        version="v1",
        location="第二章",
        status="current",
        scope="purchasing",
    )
    import_knowledge(path, tmp_path / "data", **kwargs)
    original_manifest = (tmp_path / "data/knowledge/manifest.json").read_bytes()
    original_entry = load(tmp_path)[0]
    assert not import_knowledge(path, tmp_path / "data", **kwargs)["changed"]
    assert (tmp_path / "data/knowledge/manifest.json").read_bytes() == original_manifest
    path.write_text("第二版，仅测试。", encoding="utf-8")
    result = import_knowledge(path, tmp_path / "data", **{**kwargs, "version": "v2"})
    assert Path(result["backup"]).read_bytes() == original_manifest
    assert {r["version"]: r["status"] for r in load(tmp_path)} == {
        "v1": "historical",
        "v2": "current",
    }
    assert (tmp_path / "data/knowledge" / original_entry["path"]).exists()


def test_default_draft_does_not_replace_existing_current(tmp_path):
    path = source(tmp_path)
    import_knowledge(
        path,
        tmp_path / "data",
        document_id="document",
        version="v1",
        location="第一章",
        status="current",
    )
    import_knowledge(
        path, tmp_path / "data", document_id="document", version="v2", location="第一章"
    )
    assert {r["version"]: r["status"] for r in load(tmp_path)} == {"v1": "current", "v2": "draft"}


def test_same_version_update_can_restore_old_manifest_and_body(tmp_path):
    path = source(tmp_path)
    kwargs = dict(document_id="document", version="v1", location="第一章", status="reference")
    import_knowledge(path, tmp_path / "data", **kwargs)
    old_entry = load(tmp_path)[0]
    old_text = path.read_bytes()
    path.write_text("修订后的来源原文。", encoding="utf-8")
    result = import_knowledge(path, tmp_path / "data", **kwargs)
    assert (tmp_path / "data/knowledge" / old_entry["path"]).read_bytes() == old_text
    assert json.loads(Path(result["backup"]).read_text())[0] == old_entry


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "invented"},
        {"scope": "*"},
        {"location": ""},
        {"version": ""},
        {"effective_date": "20260401"},
        {"effective_date": "2026-02-30"},
        {"source_file": "../private.env"},
    ],
)
def test_invalid_metadata_does_not_write_manifest(tmp_path, kwargs):
    arguments = dict(document_id="document", version="v1", location="第4页")
    arguments.update(kwargs)
    with pytest.raises(ValueError):
        import_knowledge(source(tmp_path), tmp_path / "data", **arguments)
    assert not (tmp_path / "data/knowledge/manifest.json").exists()


def test_existing_invalid_manifest_is_not_overwritten(tmp_path):
    root = tmp_path / "data/knowledge"
    root.mkdir(parents=True)
    (root / "manifest.json").write_text('{"not":"an array"}')
    original = (root / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="数组"):
        import_knowledge(
            source(tmp_path),
            tmp_path / "data",
            document_id="document",
            version="v1",
            location="第4页",
        )
    assert (root / "manifest.json").read_bytes() == original


def test_documents_symlink_cannot_escape_knowledge_root(tmp_path):
    root = tmp_path / "data/knowledge"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "documents").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="不能链接"):
        import_knowledge(
            source(tmp_path),
            tmp_path / "data",
            document_id="document",
            version="v1",
            location="第4页",
        )
    assert list(outside.iterdir()) == []


def test_runtime_reads_import_manifest_with_scopes_versions_and_page_references(tmp_path):
    from types import SimpleNamespace

    from app.jiahui.runtime import ScopedRetriever

    path = source(tmp_path)
    import_knowledge(
        path,
        tmp_path / "data",
        document_id="catalog-page04",
        version="v1待确认",
        location="PDF第4页",
        status="reference",
        source_file="原目录.pdf",
    )
    retriever = ScopedRetriever(
        SimpleNamespace(knowledge_dir=str(tmp_path / "data/knowledge")), None
    )
    assert retriever.knowledge(["products"]) == []
    chunks = retriever.knowledge(["knowledge"])
    assert len(chunks) == 1
    assert "PDF第4页" in chunks[0].section
    assert "原目录.pdf" in chunks[0].section
    assert "reference" in chunks[0].section
    import_knowledge(
        path,
        tmp_path / "data",
        document_id="future-doc",
        version="v1",
        location="第2章",
        status="current",
        effective_date="2999-01-01",
    )
    assert len(retriever.knowledge(["knowledge"])) == 1
    import_knowledge(
        path,
        tmp_path / "data",
        document_id="catalog-page04",
        version="v2",
        location="PDF第4页",
        status="historical",
    )
    assert len(retriever.knowledge(["knowledge"])) == 1
