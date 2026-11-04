"""Page-by-page PDF import with explicit partial results and retained originals."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.jiahui.import_knowledge import import_knowledge


class PDFImportError(ValueError):
    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = report


def _preserve(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(
                "保留的PDF原件或逐页文本校验不一致，请核对原件后再导入，未覆盖已有资料"
            )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def _save_report(root: Path, report: dict[str, Any]) -> None:
    path = root / "import-report.json"
    encoded = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if path.exists():
        if path.read_bytes() == encoded:
            return
        backup = root / "backups"
        backup.mkdir(exist_ok=True)
        shutil.copy2(path, backup / f"import-report-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}.json")
    descriptor, temporary = tempfile.mkstemp(prefix="report-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _readable_text(page) -> tuple[str, str]:
    errors = []
    parsed = False
    for mode in ["layout", "plain"]:
        try:
            text = page.extract_text(extraction_mode=mode) or ""
            parsed = True
            if text.strip():
                return text, mode
        except Exception as exc:
            errors.append(type(exc).__name__)
    if errors and not parsed:
        raise ValueError("该页文字解析失败，可能需要重新导出PDF或OCR核对")
    return "", "none"


def import_pdf(
    content: bytes, data_dir: str | Path, *, filename: str, version: str = "来源版本待确认"
) -> dict[str, Any]:
    from pypdf import PdfReader

    if len(content) > 15 * 1024 * 1024:
        raise ValueError("PDF最大15MB")
    if not content.startswith(b"%PDF-"):
        raise ValueError("文件不是可识别的PDF，请确认选择了PDF原件而不是改名后的图片或压缩包")
    try:
        document = PdfReader(io.BytesIO(content))
        if document.is_encrypted:
            raise ValueError("PDF已加密，请先提供可直接读取的未加密原件")
        page_count = len(document.pages)
    except ValueError:
        raise
    except Exception:
        raise ValueError("PDF文件无法读取，可能已损坏；请重新导出PDF后再导入") from None
    if not page_count:
        raise ValueError("PDF没有页面")
    if page_count > 300:
        raise ValueError("单份PDF最多300页，请按章节拆分")
    digest = hashlib.sha256(content).hexdigest()
    data_dir = Path(data_dir)
    root = data_dir / "sources" / "pdf" / digest
    root.mkdir(parents=True, exist_ok=True)
    _preserve(root / filename, content)
    warnings = []
    manifest = data_dir / "knowledge" / "manifest.json"
    if manifest.exists():
        existing = json.loads(manifest.read_text(encoding="utf-8"))
        versions = {
            entry.get("version", "")
            for entry in existing
            if entry.get("source_file") == filename
            and entry.get("status") in {"current", "reference"}
        }
        if versions and version not in versions:
            warnings.append(
                "同名来源已有其他可查询版本；本次保留各版本及其引用，没有自动覆盖或删除旧资料。"
            )
    imported, skipped = [], []
    changed = 0
    text_characters = 0
    for index, page in enumerate(document.pages, 1):
        try:
            text, mode = _readable_text(page)
            if not text.strip():
                skipped.append(
                    {
                        "page": index,
                        "reason": "empty_or_scanned",
                        "message": "空白页或扫描页，未提取到文字；需要时请先OCR核对",
                    }
                )
                continue
            source = root / f"page-{index:03d}.txt"
            _preserve(source, text.encode("utf-8"))
            result = import_knowledge(
                source,
                data_dir,
                document_id=f"pdf-{digest[:24]}-p{index}",
                version=version,
                location=f"PDF第{index}页",
                status="reference",
                scope="products",
                title=f"{filename} · 第{index}页",
                source_file=filename,
            )
            changed += bool(result["changed"])
            text_characters += len("".join(text.split()))
            imported.append(
                {
                    "page": index,
                    "characters": len("".join(text.split())),
                    "extraction_mode": mode,
                    "changed": bool(result["changed"]),
                }
            )
        except (ValueError, OSError) as exc:
            skipped.append({"page": index, "reason": "import_failed", "message": str(exc)})
        except Exception:
            skipped.append(
                {
                    "page": index,
                    "reason": "extraction_failed",
                    "message": "该页解析失败，其他可读取页面仍可导入；请重新导出或OCR核对这一页",
                }
            )
    if skipped:
        pages = "、".join(str(item["page"]) for item in skipped[:20]) + (
            "等" if len(skipped) > 20 else ""
        )
        warnings.append(f"第{pages}页未导入；请查看逐页结果，扫描页需要OCR核对。")
    count = len(imported)
    message = f"已导入{count}/{page_count}页参考原文，可直接查询资料；现行有效性待确认，未自动生成SKU或食品属性。"
    report: dict[str, Any] = {
        "pages": page_count,
        "total_pages": page_count,
        "imported_pages": count,
        "changed_pages": changed,
        "skipped_pages": skipped,
        "page_results": imported,
        "warnings": warnings,
        "partial": bool(skipped),
        "text_characters": text_characters,
        "source_file": filename,
        "source_sha256": digest,
        "source_saved": True,
        "status": "reference",
        "message": message,
    }
    report.update(
        {
            "catalog_imported": 0,
            "reference_products": 0,
            "catalog_changed": False,
            "skipped_tables": [],
        }
    )
    if count:
        try:
            from app.jiahui.pdf_catalog import import_pdf_catalog

            catalog_result = import_pdf_catalog(
                content, data_dir, filename=filename, version=version
            )
            warnings.extend(catalog_result.pop("warnings", []))
            report.update(catalog_result)
            report["message"] = (
                f"已导入{count}/{page_count}页参考原文，并录入{report['catalog_imported']}条可查询参考产品；现行有效性待确认。"
            )
        except ImportError:
            report["catalog_error"] = (
                "缺少PDF产品目录解析组件，请更新完整桌面软件；已导入的参考原文仍可查询。"
            )
            warnings.append(report["catalog_error"])
        except Exception as exc:
            report["catalog_error"] = (
                "PDF产品目录解析未完成，参考原文已保留并可查询；请查看日志，核对后可重试。"
            )
            report["catalog_error_type"] = type(exc).__name__
            warnings.append(report["catalog_error"])
    _save_report(root, report)
    if not count:
        raise PDFImportError(
            f"原件已保留，但0/{page_count}页可读取或导入。"
            + (
                "；".join(f"第{item['page']}页：{item['message']}" for item in skipped[:3])
                or "请核对文字可复制性或重新导出PDF。"
            ),
            report,
        )
    return report
