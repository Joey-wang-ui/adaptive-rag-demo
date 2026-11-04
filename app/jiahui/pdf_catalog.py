"""Conservative six-column PDF catalogue extraction with stable reference records.

Only explicit table cells become facts. Unsupported tables remain page-level
knowledge; they are never guessed into SKUs or marked as current business data.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.jiahui.catalog import EXTRA_FIELDS, FIELDS, Catalog
from app.jiahui.import_catalog import import_csv


def _cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\x00", "").replace("\u00a0", " ")
    # A PDF cell line-wrap is layout, not an invented separator or new attribute.
    text = re.sub(r"\s*\r?\n\s*", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _header(cells: list[str]) -> bool:
    compact = [re.sub(r"\s+", "", item) for item in cells]
    return (
        len(compact) == 6
        and compact[0] in {"行号", "序号"}
        and compact[1] in {"商品编号", "产品编号"}
        and compact[2] in {"商品名称", "产品名称", "名称"}
        and compact[3] == "规格"
        and compact[4] in {"储存条件", "贮存条件", "储存方式", "贮存方式"}
        and compact[5] in {"产地", "原产地"}
    )


def extract_pdf_catalog(content: bytes, source_file: str, version: str) -> dict[str, Any]:
    """Extract literal rows from recognized six-column tables, never broad text.

    Source bytes, page/table/row coordinates determine identity; importing the
    same PDF again produces the same records, including duplicate product codes.
    """
    import pdfplumber

    if len(content) > 15 * 1024 * 1024:
        raise ValueError("PDF最大15MB")
    if not content.startswith(b"%PDF-"):
        raise ValueError("文件不是可识别的PDF")
    if not source_file or not version.strip():
        raise ValueError("产品目录需要原始来源文件名和版本标签")
    digest = hashlib.sha256(content).hexdigest()
    records: list[dict[str, str]] = []
    skipped: list[dict[str, Any]] = []
    warnings: list[str] = []
    recognized_columns = False
    with pdfplumber.open(io.BytesIO(content)) as document:
        if len(document.pages) > 300:
            raise ValueError("单份PDF最多300页")
        for page_number, page in enumerate(document.pages, 1):
            try:
                tables = page.extract_tables()
            except Exception:
                skipped.append(
                    {"page": page_number, "table": None, "reason": "table_extraction_failed"}
                )
                continue
            for table_number, table in enumerate(tables, 1):
                if not table:
                    continue
                widths = {len(row) for row in table}
                if widths != {6}:
                    skipped.append(
                        {
                            "page": page_number,
                            "table": table_number,
                            "reason": "unsupported_column_count",
                            "columns": sorted(widths),
                        }
                    )
                    continue
                cleaned = [[_cell(value) for value in row] for row in table]
                contains_header = any(_header(row) for row in cleaned)
                if contains_header:
                    recognized_columns = True
                if not recognized_columns:
                    skipped.append(
                        {
                            "page": page_number,
                            "table": table_number,
                            "reason": "unrecognized_header",
                        }
                    )
                    continue
                skipped_rows: list[int] = []
                table_records = 0
                for row_number, cells in enumerate(cleaned, 1):
                    if _header(cells) or not any(cells):
                        continue
                    original_row, code, name, spec, storage, origin = cells
                    # Continuation pages need all identity columns. Numeric row
                    # labels alone are never a global key and can repeat freely.
                    if not re.fullmatch(r"[0-9]+", original_row) or not code or not name:
                        skipped_rows.append(row_number)
                        continue
                    identity = f"PDF-{digest[:24]}-P{page_number:03d}-T{table_number:02d}-R{row_number:03d}"
                    record = dict.fromkeys(FIELDS, "")
                    record.update(
                        {
                            "内部记录ID": identity,
                            "商品编号": code,
                            "名称": name,
                            "规格": spec,
                            "储存条件": storage,
                            "产地": origin,
                            "来源文件": source_file,
                            "来源位置": f"PDF第{page_number}页，第{table_number}表，原行号{original_row}，表内第{row_number}行",
                            "版本": version,
                            "状态": "reference",
                            "访问范围": "products",
                        }
                    )
                    records.append(record)
                    table_records += 1
                if skipped_rows:
                    skipped.append(
                        {
                            "page": page_number,
                            "table": table_number,
                            "reason": "missing_identity_cells",
                            "rows": skipped_rows,
                        }
                    )
                if not table_records and not skipped_rows:
                    skipped.append(
                        {"page": page_number, "table": table_number, "reason": "no_product_rows"}
                    )
    if skipped:
        warnings.append(
            "部分非标准表格或缺少身份字段的行未自动生成产品记录，仍可从已导入的PDF原文核查；请查看表格诊断。"
        )
    if records:
        warnings.append(
            "PDF表格已录入为可查询的参考目录，现行有效性待确认；未提供的品牌、单位换算、保质期等字段保持空白。"
        )
    else:
        warnings.append(
            "未识别到带明确表头的六列产品表格；可读的PDF原文仍可查询，未猜测生成产品目录。"
        )
    return {
        "rows": records,
        "skipped_tables": skipped,
        "warnings": warnings,
        "source_sha256": digest,
    }


def import_pdf_catalog(
    content: bytes,
    data_dir: str | Path,
    *,
    filename: str,
    version: str = "来源版本待确认",
) -> dict[str, Any]:
    """Reuse the atomic, backed-up CSV importer for extracted reference rows."""
    extracted = extract_pdf_catalog(content, filename, version)
    rows = extracted["rows"]
    existing = Catalog.load(data_dir)
    protected = {
        (row["id"], row.get("version", ""))
        for row in existing.records
        if row.get("status") != "reference"
    }
    # Re-import must not downgrade an operator-confirmed current record or
    # reactivate a historical/draft record the operator deliberately deactivated.
    pending = [row for row in rows if (row["内部记录ID"], row["版本"]) not in protected]
    changed = False
    if pending:
        with tempfile.TemporaryDirectory(prefix="jiahui-pdf-catalog-") as temporary:
            # A stable name also keeps the generated CSV source citation stable.
            source = Path(temporary) / f"pdf-catalog-{extracted['source_sha256'][:24]}.csv"
            with source.open("w", encoding="utf-8-sig", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=[*FIELDS, *EXTRA_FIELDS])
                writer.writeheader()
                writer.writerows(pending)
            result = import_csv(source, data_dir, status="reference", scope="products")
            changed = bool(result["changed"])
    catalog = Catalog.load(data_dir)
    digest = extracted["source_sha256"]
    source_root = Path(data_dir) / "sources" / "pdf" / digest
    if source_root.exists():
        _save_catalog_state(source_root, digest, filename, version, rows)
    return {
        "catalog_imported": len(rows),
        "reference_products": sum(row.get("status") == "reference" for row in catalog.records),
        "catalog_changed": changed,
        "skipped_tables": extracted["skipped_tables"],
        "warnings": extracted["warnings"],
    }


EXTRACTOR_VERSION = 1


def _save_catalog_state(
    root: Path, digest: str, filename: str, version: str, rows: list[dict[str, str]]
) -> None:
    state = {
        "extractor_version": EXTRACTOR_VERSION,
        "source_sha256": digest,
        "source_file": filename,
        "version": version,
        "records": [{"id": row["内部记录ID"], "version": row["版本"]} for row in rows],
    }
    path = root / "catalog-state.json"
    encoded = (json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    if path.exists():
        previous = path.read_bytes()
        if previous == encoded:
            return
        backups = root / "backups"
        backups.mkdir(exist_ok=True)
        name = "catalog-state-" + hashlib.sha256(previous).hexdigest()[:24] + ".json"
        if not (backups / name).exists():
            shutil.copy2(path, backups / name)
    descriptor, temporary = tempfile.mkstemp(prefix="catalog-state-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def repair_pdf_catalogs(data_dir: str | Path) -> dict[str, Any]:
    """Recover queryable reference rows for previously retained desktop PDFs.

    No PDF, account or key is moved. Valid state skips re-parsing at subsequent
    launches; deleted product data can be reconstructed from the retained source.
    User-deactivated source versions and confirmed product rows stay unchanged.
    """
    data_dir = Path(data_dir)
    root = data_dir / "sources" / "pdf"
    report: dict[str, Any] = {
        "scanned_files": 0,
        "repaired_files": 0,
        "reused_files": 0,
        "skipped_files": 0,
        "catalog_imported": 0,
        "files": [],
        "warnings": [],
    }
    if not root.exists():
        return report
    try:
        manifest_path = data_dir / "knowledge" / "manifest.json"
        manifest = (
            json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else []
        )
        if not isinstance(manifest, list):
            raise ValueError("知识清单格式无效")
        catalog = Catalog.load(data_dir)
    except (ValueError, OSError):
        report["warnings"].append("已有产品或知识清单无法读取，未自动覆盖；请先核对文件或备份。")
        return report
    present = {(row["id"], row.get("version", "")) for row in catalog.records}
    resolved_root = root.resolve()
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or not directory.resolve().is_relative_to(resolved_root):
            continue
        pdfs = sorted(
            path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".pdf"
        )
        for source in pdfs:
            report["scanned_files"] += 1
            item: dict[str, Any] = {"source_file": source.name}
            try:
                if not source.resolve().is_relative_to(resolved_root):
                    raise ValueError("原件路径不在资料目录内")
                content = source.read_bytes()
                digest = hashlib.sha256(content).hexdigest()
                if directory.name != digest:
                    raise ValueError("保留原件与来源目录摘要不一致，未自动覆盖")
                related = [
                    entry
                    for entry in manifest
                    if isinstance(entry, dict)
                    and (str(entry.get("id", "")).startswith(f"pdf-{digest[:24]}-"))
                ]
                active = [
                    entry for entry in related if entry.get("status") in {"current", "reference"}
                ]
                if related and not active:
                    report["skipped_files"] += 1
                    item.update({"state": "skipped", "reason": "source_not_enabled"})
                    report["files"].append(item)
                    continue
                versions = sorted(
                    {str(entry.get("version", "")) for entry in active if entry.get("version")}
                ) or ["来源版本待确认"]
                # The importer preserves old identities. It does not guess which
                # file/version is newest or current from its filename or mtime.
                version = versions[0]
                state_path = directory / "catalog-state.json"
                try:
                    state = (
                        json.loads(state_path.read_text(encoding="utf-8"))
                        if state_path.exists()
                        else None
                    )
                except (ValueError, OSError):
                    state = None
                if (
                    isinstance(state, dict)
                    and state.get("extractor_version") == EXTRACTOR_VERSION
                    and state.get("source_sha256") == digest
                    and state.get("version") == version
                    and isinstance(state.get("records"), list)
                    and all(
                        isinstance(record, dict)
                        and (record.get("id"), record.get("version", "")) in present
                        for record in state["records"]
                    )
                ):
                    report["reused_files"] += 1
                    item.update(
                        {"state": "reused", "records": len(state["records"]), "version": version}
                    )
                    report["files"].append(item)
                    continue
                if len(versions) > 1:
                    report["warnings"].append(
                        f"{source.name}存在多个启用版本；自动修复仅处理一个明确保留版本，请在资料列表核对其他版本。"
                    )
                result = import_pdf_catalog(
                    content, data_dir, filename=source.name, version=version
                )
                report["repaired_files"] += 1
                report["catalog_imported"] += result["catalog_imported"]
                item.update(
                    {"state": "repaired", "records": result["catalog_imported"], "version": version}
                )
                report["warnings"].extend(result["warnings"])
                present = {
                    (row["id"], row.get("version", "")) for row in Catalog.load(data_dir).records
                }
            except Exception as exc:
                report["skipped_files"] += 1
                item.update(
                    {
                        "state": "failed",
                        "reason": str(exc) if isinstance(exc, ValueError) else type(exc).__name__,
                    }
                )
                report["warnings"].append(
                    f"{source.name}自动目录检查未完成：{item['reason']}。原件已保留，可在资料管理中重试。"
                )
            report["files"].append(item)
    return report
