"""Isolated, versioned Jiahui product catalogue. No inferred business facts."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

FIELDS = {
    "内部记录ID": "id",
    "商品编号": "code",
    "名称": "name",
    "品牌": "brand",
    "品类": "category",
    "别名": "aliases",
    "规格": "spec",
    "包装单位": "packaging_unit",
    "计价单位": "pricing_unit",
    "已确认的单位换算": "conversions",
    "储存条件": "storage",
    "保质期": "shelf_life",
    "产地": "origin",
    "来源文件": "source_file",
    "来源位置": "source_location",
    "版本": "version",
    "有效日期": "effective_date",
}
EXTRA_FIELDS = {"状态": "status", "访问范围": "scope"}
VALID_STATUSES = {"current", "reference", "historical", "draft"}


def normalized(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def is_listing_query(message: str) -> bool:
    if "有什么规格" in message:
        return False
    return any(
        word in message
        for word in ("有哪些", "有什么", "哪些", "哪几款", "几种", "几款", "列出", "所有")
    )


def spec_selected(spec: str, message: str) -> bool:
    """Match confirmed specs without confusing 5kg with 25kg or merging IDs."""
    value = normalized(spec)
    if not value:
        return False
    query = unicodedata.normalize("NFKC", message).casefold()
    pattern = r"(?<![a-z0-9.])" + r"\s*".join(re.escape(char) for char in value) + r"(?![a-z0-9.])"
    return re.search(pattern, query) is not None


def allowed(record: dict[str, Any], scopes: list[str] | set[str]) -> bool:
    """Every retrieval and tool caller uses the same explicit visibility rule."""
    return "*" in scopes or record.get("scope", "products") in scopes


class Catalog:
    def __init__(self, records: list[dict[str, Any]], data_dir: Path | None = None) -> None:
        self.records = records
        self.data_dir = data_dir

    @classmethod
    def load(cls, data_dir: str | Path) -> Catalog:
        root = Path(data_dir)
        path = root / "products.json"
        if not path.exists():
            return cls([], root)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("不支持的产品目录格式，请从备份恢复或使用导入命令")
        records = data.get("records")
        if not isinstance(records, list) or not all(isinstance(row, dict) for row in records):
            raise ValueError("产品目录 records 必须是记录列表")
        seen: set[tuple[str, str]] = set()
        for row in records:
            if not isinstance(row.get("id"), str) or not row["id"]:
                raise ValueError("产品目录记录缺少内部记录ID")
            if not isinstance(row.get("code", ""), str):
                raise ValueError("商品编号必须按字符串保存")
            if row.get("status") not in VALID_STATUSES:
                raise ValueError("产品目录记录状态无效")
            identity = (row["id"], row.get("version", ""))
            if identity in seen:
                raise ValueError("产品目录内部记录ID与版本重复")
            seen.add(identity)
        return cls(records, root)

    def visible_records(
        self, scopes: list[str] | set[str], *, include_history: bool = False
    ) -> list[dict[str, Any]]:
        today = date.today().isoformat()
        return [
            dict(row)
            for row in self.records
            if allowed(row, scopes)
            and (include_history or row.get("status") in {"current", "reference"})
            and (include_history or not row.get("effective_date") or row["effective_date"] <= today)
        ]

    def search(
        self, message: str, scopes: list[str] | set[str], *, include_history: bool = False
    ) -> list[dict[str, Any]]:
        query = normalized(message)
        # Keep separators for identifier boundaries: removing spaces glues the
        # natural label "内部记录ID DEMO-001" into "iddemo-001" and hides it.
        identifier_query = unicodedata.normalize("NFKC", message).casefold()
        identifier_query = re.sub(
            r"(?:内部记录\s*id|记录\s*id|商品\s*id|产品\s*id)[:：]?\s*",
            " ",
            identifier_query,
        )
        rows = self.visible_records(scopes, include_history=include_history)
        exact_id = [
            row
            for row in rows
            if normalized(row["id"])
            and re.search(
                r"(?<![a-z0-9_-])"
                + r"\s*".join(re.escape(char) for char in normalized(row["id"]))
                + r"(?![a-z0-9_-])",
                identifier_query,
            )
        ]
        if exact_id:
            return exact_id
        exact_code = []
        for row in rows:
            code = normalized(row.get("code", ""))
            # A short code must not match the suffix of a different product code.
            if code and re.search(
                r"(?<![a-z0-9_*-])"
                + r"\s*".join(re.escape(char) for char in code)
                + r"(?![a-z0-9_*-])",
                identifier_query,
            ):
                exact_code.append(row)
        if exact_code:
            return exact_code
        explicit_code = re.search(r"(?:编号|货号|编码)[:：]?([a-z0-9][a-z0-9_.*-]*)", query)
        if explicit_code:
            return []
        for match in re.finditer(r"(?<![a-z0-9])([a-z]*[0-9][a-z0-9_*-]*)(?![a-z0-9])", query):
            token = match.group(1)
            suffix = query[match.end() :]
            quantity_unit = suffix.startswith(
                ("箱", "盒", "包", "桶", "袋", "瓶", "支", "个", "枚", "斤", "公斤", "克", "升")
            ) or token.endswith(("kg", "g", "ml", "l"))
            if len(token) >= 3 and not quantity_unit:
                return []
        stopwords = [
            "帮我查下",
            "帮我查",
            "帮我看看",
            "查下",
            "我们有",
            "咱们有",
            "请问",
            "查一下",
            "查询",
            "产品",
            "商品",
            "资料",
            "有哪些",
            "有什么",
            "哪些",
            "哪几款",
            "几种",
            "几款",
            "所有",
            "列出",
            "什么",
            "规格",
            "储存",
            "条件",
            "保质期",
            "比较",
            "对比",
            "分别",
            "来源",
            "编号",
            "品牌",
            "名称",
            "包装",
            "是多少",
            "怎么样",
            "是否",
            "有没有",
            "多少",
            "请",
            "看看",
            "一下",
        ]
        meaningful = query
        for word in stopwords:
            meaningful = meaningful.replace(word, "")
        # Query vocabulary only: never write inferred categories into product data.
        # Catalogues often label liquid egg products as 杀菌蛋黄/蛋白 without 液.
        egg_family = "蛋液" in meaningful and not any(
            x in meaningful for x in ("蛋黄液", "蛋白液", "全蛋液")
        )
        ranked = []
        for row in rows:
            names = [row.get("name", ""), *row.get("aliases", [])]
            score = 0.0
            for value in names:
                text = normalized(value)
                if not text:
                    continue
                if egg_family and re.search(r"蛋液|蛋[黄白]液|全蛋液|杀菌蛋[黄白](?!粉)", text):
                    score = max(score, 85)
                if text in query:
                    score = max(score, 100 + len(text))
                elif meaningful and len(meaningful) > 1 and meaningful in text:
                    score = max(score, 80 + len(meaningful))
                else:
                    # Chinese names can contain supplier prefixes; short contiguous
                    # fragments allow "天成蛋黄液" to find the full label verbatim.
                    grams = {meaningful[i : i + 2] for i in range(len(meaningful) - 1)}
                    overlap = {
                        gram
                        for gram in grams
                        if gram in text and not re.search(r"[^\w\u4e00-\u9fff]", gram)
                    }
                    if len(overlap) >= 2:
                        score = max(score, len(overlap) * 3.0)
            for field in ("brand", "spec", "category"):
                value = normalized(row.get(field, ""))
                if value and value in query:
                    score += 12
            if score >= 3:
                ranked.append((score, row))
        ranked.sort(key=lambda x: (-x[0], x[1]["id"], x[1].get("version", "")))
        return [row for _, row in ranked]

    @staticmethod
    def needs_clarification(records: list[dict[str, Any]], message: str) -> bool:
        if is_listing_query(message) or any(
            word in message for word in ("对比", "比较", "区别", "差别", "分别")
        ):
            return False
        identities = {(r["id"], r.get("spec", "")) for r in records}
        if len(identities) < 2:
            return False
        # Explicitly selecting a unique confirmed specification resolves ambiguity.
        specs = [r for r in records if r.get("spec") and spec_selected(r["spec"], message)]
        return len(specs) != 1

    @staticmethod
    def to_document(record: dict[str, Any]) -> str:
        lines = ["嘉惠产品资料（仅列已录入字段，不推断缺失信息）"]
        for label, key in FIELDS.items():
            value = record.get(key, "")
            if key == "conversions" and isinstance(value, list):
                value = json.dumps(value, ensure_ascii=False) if value else ""
            elif isinstance(value, list):
                value = "；".join(value)
            lines.append(f"{label}：{value or '尚未录入/需要确认'}")
        lines.append(f"资料状态：{record.get('status', 'draft')}")
        return "\n".join(lines)
