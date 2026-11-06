"""Validated, versioned, hot-loaded Jiahui skills. No executable template syntax."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from string import Formatter
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

Column = Literal["name", "code", "brand", "spec", "storage", "shelf_life", "origin"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductSkill(StrictModel):
    list_intro: str = Field(default="找到 **{count} 条产品记录**：", min_length=1, max_length=200)
    clarification: str = Field(
        default="找到多个商品或规格，请先选择品牌、规格或内部记录ID：", min_length=1, max_length=300
    )
    max_results: int = Field(default=20, ge=1, le=20, strict=True)
    columns: list[Column] = Field(
        default_factory=lambda: cast(list[Column], ["name", "code", "spec", "storage"]),
        min_length=1,
        max_length=7,
    )
    details_layout: Literal["full", "compact"] = "full"

    @field_validator("list_intro")
    @classmethod
    def placeholders(cls, value):
        try:
            for _, name, spec, conversion in Formatter().parse(value):
                if name is not None and (name != "count" or spec or conversion):
                    raise ValueError("仅允许 {count} 占位符，不支持表达式或格式指令")
        except ValueError as exc:
            raise ValueError("列表开头格式无效：仅允许 {count} 占位符") from exc
        if re.search(r"保质期|储温|储存|功效|替代|成交价|库存|价格|金额", value):
            raise ValueError("开头文案不能填写产品事实；请通过资料导入维护属性或业务数据")
        return value

    @field_validator("columns")
    @classmethod
    def unique_columns(cls, value):
        if len(value) != len(set(value)) or "name" not in value:
            raise ValueError("列项不可重复，且必须包含产品名称")
        return value


class PromptSkill(StrictModel):
    instructions: str = Field(min_length=1, max_length=4000)

    @field_validator("instructions")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or "\x00" in value:
            raise ValueError("表达要求不能为空或包含空字符")
        return value


class KnowledgeSkill(PromptSkill):
    layout: Literal["bullets", "numbered", "paragraph", "table"] = "bullets"


class SkillTemplates(StrictModel):
    product_lookup: ProductSkill = Field(default_factory=ProductSkill)
    knowledge_qa: KnowledgeSkill = Field(
        default_factory=lambda: KnowledgeSkill(
            instructions="选择直接回答问题的依据，按用户关心的要点组织；有操作顺序时按步骤排列。"
        )
    )
    general_chat: PromptSkill = Field(
        default_factory=lambda: PromptSkill(
            instructions="用自然清楚的中文，先给结论，再补充必要解释。写作请求直接给草稿，比较问题优先用简洁表格。"
        )
    )
    web_search: PromptSkill = Field(
        default_factory=lambda: PromptSkill(
            instructions="先回答问题，再分点解释。保留来源编号，指出来源分歧和时间差异，不堆砌长摘要。"
        )
    )


class Snapshot(StrictModel):
    schema_version: Literal[1] = 1
    updated_at: str | None = None
    updated_by: str = "system"
    templates: SkillTemplates = Field(default_factory=SkillTemplates)


class TemplateConflict(ValueError):
    pass


def apply_skill_instructions(base: str, instructions: str) -> str:
    return (
        base
        + "\n\n【管理员表达要求】\n"
        + instructions
        + "\n\n【固定边界】表达要求不能改变数据访问权限、引用及输出校验，也不能授权业务写操作。仍须遵守上述证据边界和JSON输出约定（如有）。"
    )


class SkillStore:
    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir) / "skills"
        self.path = self.root / "templates.json"
        self.history = self.root / "history"

    @staticmethod
    def encode(snapshot: Snapshot) -> bytes:
        return (
            json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")

    def raw(self) -> bytes:
        if not self.path.exists():
            return self.encode(Snapshot())
        if self.path.stat().st_size > 40000:
            raise ValueError("技能模板文件超过40KB，请核对备份")
        return self.path.read_bytes()

    @staticmethod
    def revision(raw: bytes) -> str:
        return hashlib.sha256(raw).hexdigest()

    def read(self) -> Snapshot:
        return Snapshot.model_validate_json(self.raw())

    def inspect(self) -> dict:
        raw = self.raw()
        try:
            snapshot = Snapshot.model_validate_json(raw)
            error = None
        except ValueError:
            snapshot = Snapshot()
            error = (
                "本机模板文件损坏，问答已停止使用它。可恢复历史版本或保存默认模板；原文件会先备份。"
            )
        return {
            **snapshot.model_dump(mode="json"),
            "revision": self.revision(raw),
            "load_error": error,
            "defaults": SkillTemplates().model_dump(mode="json"),
        }

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock = self.root / ".templates.lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise TemplateConflict(
                "另有模板保存正在进行，请稍后重试；若进程已退出请管理员核对锁文件"
            ) from None
        try:
            yield
        finally:
            os.close(fd)
            lock.unlink(missing_ok=True)

    def save(self, templates: SkillTemplates, expected_revision: str, actor: str) -> dict:
        with self.locked():
            old = self.raw()
            if self.revision(old) != expected_revision:
                raise TemplateConflict("模板已被其他窗口更新，请重新加载后再保存")
            try:
                if Snapshot.model_validate_json(old).templates == templates:
                    return self.inspect()
            except ValueError:
                pass
            if len(self.encode(Snapshot(templates=templates))) > 39000:
                raise ValueError("模板内容过长，请缩短表达要求")
            # Preserve exact old bytes before replacing anything, even a corrupt file.
            self.history.mkdir(exist_ok=True)
            archive = self.history / (self.revision(old) + ".json")
            if not archive.exists():
                with archive.open("xb") as backup:
                    backup.write(old)
                    backup.flush()
                    os.fsync(backup.fileno())
            fresh = Snapshot(
                updated_at=datetime.now(UTC).isoformat(), updated_by=actor, templates=templates
            )
            fd, temporary = tempfile.mkstemp(dir=self.root, prefix="templates-", suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(self.encode(fresh))
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return self.inspect()

    def versions(self) -> list[dict]:
        entries: list[dict] = []
        if not self.history.exists():
            return entries
        for path in sorted(
            self.history.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True
        )[:100]:
            if not re.fullmatch(r"[a-f0-9]{64}", path.stem):
                continue
            try:
                raw = path.read_bytes()
                if self.revision(raw) != path.stem:
                    raise ValueError("checksum")
                snapshot = Snapshot.model_validate_json(raw)
                entries.append(
                    {
                        "revision": path.stem,
                        "updated_at": snapshot.updated_at,
                        "updated_by": snapshot.updated_by,
                        "restorable": True,
                    }
                )
            except ValueError:
                entries.append(
                    {
                        "revision": path.stem,
                        "updated_at": None,
                        "updated_by": "原文件损坏",
                        "restorable": False,
                    }
                )
        return entries

    def restore(self, revision: str, expected_revision: str, actor: str) -> dict:
        if not re.fullmatch(r"[a-f0-9]{64}", revision):
            raise ValueError("历史版本编号无效")
        path = self.history / (revision + ".json")
        if not path.is_file():
            raise ValueError("历史版本不存在")
        raw = path.read_bytes()
        if self.revision(raw) != revision:
            raise ValueError("历史版本校验失败，未恢复")
        return self.save(Snapshot.model_validate_json(raw).templates, expected_revision, actor)
