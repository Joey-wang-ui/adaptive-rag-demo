"""Repeatable CSV import with validation, source retention and pre-change backup.

Usage: python -m app.jiahui.import_catalog --csv products.csv --data-dir data/jiahui
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from app.jiahui.catalog import EXTRA_FIELDS, FIELDS, VALID_STATUSES, Catalog


@contextmanager
def import_lock(root: Path):
    """An exclusive lock file prevents two CLI imports from losing updates.

    The file is never treated as a stale timeout lock: a crashed importer requires
    an operator to inspect the process before explicitly removing its lock.
    """
    lock = root / ".catalog-import.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError(
            "已有产品导入进行中；若进程已退出，确认后移除 .catalog-import.lock"
        ) from exc
    try:
        os.write(descriptor, str(os.getpid()).encode())
        yield
    finally:
        os.close(descriptor)
        lock.unlink(missing_ok=True)


def _source_filename(value: str) -> str:
    if not value:
        return ""
    if "\x00" in value or "://" in value or value.startswith(("/", "\\")):
        raise ValueError("来源文件必须是相对文件名，不能包含 URL 或绝对路径")
    parts = value.replace("\\", "/").split("/")
    if any(part in {"", ".", ".."} for part in parts) or ":" in value:
        raise ValueError("来源文件不能包含路径穿越或驱动器路径")
    return "/".join(parts)


def parse_csv(
    path: Path, *, status: str = "draft", scope: str = "products"
) -> list[dict[str, Any]]:
    if status not in VALID_STATUSES:
        raise ValueError("资料状态必须为 current、historical 或 draft")
    raw = path.read_bytes()
    if len(raw) > 15 * 1024 * 1024:
        raise ValueError("CSV 最大允许15MB")
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if not reader.fieldnames or not {"内部记录ID", "名称"}.issubset(reader.fieldnames):
        raise ValueError("CSV 至少需要内部记录ID、名称两列；请使用 UTF-8 模板")
    if len(reader.fieldnames) != len(set(reader.fieldnames)):
        raise ValueError("CSV 表头列名重复")
    known = {**FIELDS, **EXTRA_FIELDS}
    unknown = set(reader.fieldnames) - set(known)
    if unknown:
        raise ValueError("CSV 包含未知字段：" + "、".join(sorted(unknown)))
    records = []
    seen: set[tuple[str, str]] = set()
    for row in reader:
        line = reader.line_num
        if None in row:
            raise ValueError(f"CSV 第{line}行列数过多")
        if not any(value and value.strip() for value in row.values()):
            continue
        record = {key: (row.get(label) or "").strip() for label, key in FIELDS.items()}
        if not record["id"] or not record["name"]:
            raise ValueError(f"CSV 第{line}行内部记录ID和名称不能为空")
        if len(record["id"]) > 200 or any(ord(c) < 32 for c in record["id"]):
            raise ValueError(f"CSV 第{line}行内部记录ID无效")
        identity = (record["id"], record["version"])
        if identity in seen:
            raise ValueError(f"CSV 第{line}行内部记录ID与版本重复，不能覆盖另一行")
        seen.add(identity)
        record["aliases"] = [s.strip() for s in re.split(r"[;；]", record["aliases"]) if s.strip()]
        conversion_text = record["conversions"]
        conversions = json.loads(conversion_text) if conversion_text else []
        if not isinstance(conversions, list):
            raise ValueError(f"CSV 第{line}行单位换算必须为JSON数组")
        for conversion in conversions:
            if not isinstance(conversion, dict) or set(conversion) != {"from", "to", "factor"}:
                raise ValueError(f"CSV 第{line}行换算必须包含from/to/factor三个字段")
            if not all(
                isinstance(conversion[key], str) and conversion[key].strip()
                for key in ("from", "to", "factor")
            ):
                raise ValueError(f"CSV 第{line}行换算单位及系数必须为非空字符串")
            try:
                factor = Decimal(conversion["factor"])
            except InvalidOperation as exc:
                raise ValueError(f"CSV 第{line}行换算系数必须为明确十进制数字") from exc
            if not factor.is_finite() or factor <= 0:
                raise ValueError(f"CSV 第{line}行换算系数必须为有限正数")
        record["conversions"] = conversions
        record["status"] = (row.get("状态") or status).strip()
        if record["status"] not in VALID_STATUSES:
            raise ValueError(f"CSV 第{line}行状态无效")
        record["scope"] = (row.get("访问范围") or scope).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", record["scope"]):
            raise ValueError(f"CSV 第{line}行访问范围格式无效")
        if record["effective_date"]:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["effective_date"]):
                raise ValueError(f"CSV 第{line}行有效日期必须采用YYYY-MM-DD格式")
            date.fromisoformat(record["effective_date"])
        record["source_file"] = _source_filename(record["source_file"]) or path.name
        location = record["source_location"]
        record["source_location"] = (
            location + "；" if location else ""
        ) + f"导入CSV {path.name} 第{line}行"
        if len(record["source_location"]) > 2000:
            raise ValueError(f"CSV 第{line}行来源位置过长")
        records.append(record)
    return records


def import_csv(
    csv_path: str | Path,
    data_dir: str | Path,
    *,
    status: str = "draft",
    scope: str = "products",
) -> dict[str, Any]:
    source = Path(csv_path)
    incoming = parse_csv(source, status=status, scope=scope)
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    with import_lock(root):
        catalog = Catalog.load(root)
        records = {(r["id"], r.get("version", "")): r for r in catalog.records}
        # Multiple current versions of one identity in a single CSV are ambiguous.
        current_ids = [r["id"] for r in incoming if r["status"] == "current"]
        if len(current_ids) != len(set(current_ids)):
            raise ValueError("同一内部记录ID不能同时导入多个现行版本")
        for record in incoming:
            if record["status"] == "current":
                for old in records.values():
                    if old["id"] == record["id"] and old.get("version", "") != record["version"]:
                        old["status"] = "historical"
            records[(record["id"], record["version"])] = record
        merged = sorted(records.values(), key=lambda r: (r["id"], r.get("version", "")))
        old_sorted = sorted(catalog.records, key=lambda r: (r["id"], r.get("version", "")))
        if merged == old_sorted:
            return {
                "changed": False,
                "imported": len(incoming),
                "total": len(merged),
                "backup": None,
            }
        target = root / "products.json"
        backup = None
        if target.exists():
            backups = root / "backups"
            backups.mkdir(exist_ok=True)
            digest = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
            backup = backups / f"products-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{digest}.json"
            shutil.copy2(target, backup)
        imports = root / "sources" / "imports"
        imports.mkdir(parents=True, exist_ok=True)
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        source_copy = imports / f"{source_hash}.csv"
        if not source_copy.exists():
            shutil.copy2(source, source_copy)
        data = {"schema_version": 1, "records": merged}
        descriptor, temp_name = tempfile.mkstemp(prefix="products-", suffix=".tmp", dir=root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_name, target)
        finally:
            Path(temp_name).unlink(missing_ok=True)
        return {
            "changed": True,
            "imported": len(incoming),
            "total": len(merged),
            "backup": str(backup) if backup else None,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="导入嘉惠 UTF-8 CSV；默认草稿，不自动当作现行资料")
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path("data/jiahui"))
    parser.add_argument("--status", choices=sorted(VALID_STATUSES), default="draft")
    parser.add_argument("--scope", default="products")
    args = parser.parse_args()
    try:
        result = import_csv(args.csv, args.data_dir, status=args.status, scope=args.scope)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"导入失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
