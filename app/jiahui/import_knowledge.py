"""Explicit, versioned text knowledge import for the isolated Jiahui corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from app.jiahui.import_catalog import _source_filename

STATUSES = {"current", "reference", "historical", "draft"}


@contextmanager
def _lock(root: Path):
    path = root / ".knowledge-import.lock"
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError(
            "已有知识导入进行中；确认进程已退出后才可移除 .knowledge-import.lock"
        ) from exc
    try:
        os.write(descriptor, str(os.getpid()).encode())
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


def _load_manifest(root: Path) -> list[dict[str, Any]]:
    path = root / "manifest.json"
    if not path.exists():
        return []
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, list):
        raise ValueError("知识manifest必须为数组")
    identities = set()
    for entry in manifest:
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(key), str) and entry[key] for key in ("id", "path")
        ):
            raise ValueError("知识清单记录缺少id或path")
        document = (root / entry["path"]).resolve()
        if not document.is_relative_to(root.resolve()) or document.suffix.lower() not in {
            ".md",
            ".txt",
        }:
            raise ValueError("现有知识清单含越界或不支持的文件路径")
        identity = (entry["id"], entry.get("version", ""))
        if identity in identities:
            raise ValueError("现有知识清单的ID与版本重复")
        identities.add(identity)
    return manifest


def import_knowledge(
    source: str | Path,
    data_dir: str | Path,
    *,
    document_id: str,
    version: str,
    location: str,
    status: str = "draft",
    scope: str = "knowledge",
    title: str = "",
    source_file: str = "",
    effective_date: str = "",
) -> dict[str, Any]:
    source = Path(source)
    if source.suffix.lower() not in {".md", ".txt"}:
        raise ValueError("知识导入只支持显式准备的UTF-8 .txt/.md；PDF请先按页提取并核对")
    content = source.read_bytes()
    if len(content) > 15 * 1024 * 1024:
        raise ValueError("单份知识文档不能超过15MB")
    if not content.decode("utf-8-sig").strip():
        raise ValueError("知识文档内容不能为空")
    if not document_id.strip() or len(document_id) > 200 or any(ord(c) < 32 for c in document_id):
        raise ValueError("知识内部ID不能为空、超过200字符或含控制字符")
    if not version.strip() or len(version) > 200:
        raise ValueError("需要明确的版本标签；未知时可填来源版本待确认")
    if not location.strip():
        raise ValueError("需要来源位置（页码或章节），不能自动丢失来源位置")
    if status not in STATUSES:
        raise ValueError("知识状态必须是current/reference/historical/draft")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", scope):
        raise ValueError("知识访问范围格式无效")
    if effective_date:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", effective_date):
            raise ValueError("有效日期必须采用YYYY-MM-DD格式")
        date.fromisoformat(effective_date)
    source_file = _source_filename(source_file) if source_file else source.name
    root = Path(data_dir) / "knowledge"
    root.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    # Content-addressed files preserve all versions and allow rollback of a
    # manifest backup without losing the source body it referenced.
    identity_digest = hashlib.sha256((document_id + "\x00" + version).encode()).hexdigest()[:20]
    relative_path = f"documents/{identity_digest}-{digest}{source.suffix.lower()}"
    incoming = {
        "id": document_id,
        "path": relative_path,
        "title": title or source.stem,
        "version": version,
        "status": status,
        "scope": scope,
        "source_file": source_file,
        "location": location,
        "effective_date": effective_date,
    }
    with _lock(root):
        existing = _load_manifest(root)
        old_sorted = sorted(existing, key=lambda r: (r["id"], r.get("version", "")))
        records = {(r["id"], r.get("version", "")): dict(r) for r in existing}
        if status in {"current", "reference"}:
            for record in records.values():
                if record["id"] == document_id and record.get("version", "") != version:
                    if record.get("status") in {"current", "reference"}:
                        record["status"] = "historical"
        records[(document_id, version)] = incoming
        merged = sorted(records.values(), key=lambda r: (r["id"], r.get("version", "")))
        destination = root / relative_path
        # Explicitly resolve the destination even though our generated filename
        # is safe: an existing documents symlink must not escape the corpus.
        if not destination.resolve().is_relative_to(root.resolve()):
            raise ValueError("knowledge/documents 不能链接到知识目录以外")
        if merged == old_sorted and destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
                return {"changed": False, "total": len(merged), "backup": None}
            raise ValueError("保留的知识原文校验不一致；请先核查原文，不能静默覆盖")
        target = root / "manifest.json"
        backup = None
        if target.exists():
            backup_dir = root / "backups"
            backup_dir.mkdir(exist_ok=True)
            old_digest = hashlib.sha256(target.read_bytes()).hexdigest()[:16]
            backup = backup_dir / f"manifest-{datetime.now(UTC):%Y%m%dT%H%M%S%fZ}-{old_digest}.json"
            shutil.copy2(target, backup)
        destination.parent.mkdir(exist_ok=True)
        if destination.exists():
            if hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise ValueError("目标原文校验不一致，拒绝覆盖")
        else:
            # Exclusive creation ensures an existing original is never clobbered.
            with destination.open("xb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        descriptor, temporary = tempfile.mkstemp(prefix="manifest-", suffix=".tmp", dir=root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(merged, output, ensure_ascii=False, indent=2)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"changed": True, "total": len(merged), "backup": str(backup) if backup else None}


def main() -> None:
    parser = argparse.ArgumentParser(description="显式导入嘉惠文本知识，默认draft，不当作现行制度")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--id", required=True, dest="document_id")
    parser.add_argument("--version", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--status", choices=sorted(STATUSES), default="draft")
    parser.add_argument("--scope", default="knowledge")
    parser.add_argument("--title", default="")
    parser.add_argument("--source-file", default="")
    parser.add_argument("--effective-date", default="")
    parser.add_argument("--data-dir", type=Path, default=Path("data/jiahui"))
    args = parser.parse_args()
    try:
        result = import_knowledge(**vars(args))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"导入失败：{exc}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
