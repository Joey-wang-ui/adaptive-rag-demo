"""PDF table interpretation tests use synthetic cells, never real business data."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from app.jiahui.catalog import Catalog
from app.jiahui.pdf_catalog import extract_pdf_catalog, import_pdf_catalog

HEADER = ["行号", "商品编号", "商品名称", "规格", "储存条件", "产地"]
CONTENT = b"%PDF-1.7\nSynthetic isolated table bytes; not a real business PDF."


class FakeDocument:
    def __init__(self, pages):
        self.pages = [
            SimpleNamespace(extract_tables=lambda tables=tables: tables) for tables in pages
        ]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def pdf_tables(monkeypatch, pages):
    module = SimpleNamespace(open=lambda stream: FakeDocument(pages))
    monkeypatch.setitem(sys.modules, "pdfplumber", module)


def sample_pages():
    return [
        [
            [
                HEADER,
                ["1", "00001", "【演示】同名面粉", "25kg/包", "常温", "示例产地"],
                ["2", "00001", "【演示】同名面粉", "5kg/包", "常温", "示例产地"],
            ]
        ],
        [
            [
                ["82", "00123", "【演示】跨页原料", "970*12盒/箱", "冷藏", ""],
                [
                    "82",
                    "CODE-4",
                    "【演示】名称\n换行",
                    "1kg*10包/箱",
                    "冷\n藏",
                    "上海、天津、宁\n夏",
                ],
            ]
        ],
    ]


def test_literal_fields_leading_zero_duplicate_codes_and_stable_independent_ids(monkeypatch):
    pdf_tables(monkeypatch, sample_pages())
    result = extract_pdf_catalog(CONTENT, "隔离产品表.pdf", "TEST-v1")
    rows = result["rows"]
    assert len(rows) == 4
    assert rows[0]["商品编号"] == rows[1]["商品编号"] == "00001"
    assert rows[0]["内部记录ID"] != rows[1]["内部记录ID"]
    assert len({row["内部记录ID"] for row in rows}) == 4
    assert rows[2]["商品编号"] == "00123"
    assert rows[2]["规格"] == "970*12盒/箱"  # Missing weight unit must stay missing.
    assert rows[2]["产地"] == ""
    assert all(row["状态"] == "reference" and row["访问范围"] == "products" for row in rows)
    for row in rows:
        assert row["品牌"] == row["别名"] == row["保质期"] == row["已确认的单位换算"] == ""
        assert row["有效日期"] == "" and row["来源文件"] == "隔离产品表.pdf"
    assert "PDF第2页，第1表，原行号82，表内第1行" == rows[2]["来源位置"]
    assert rows[3]["名称"] == "【演示】名称换行"
    assert rows[3]["储存条件"] == "冷藏"
    assert rows[3]["产地"] == "上海、天津、宁夏"
    again = extract_pdf_catalog(CONTENT, "隔离产品表.pdf", "TEST-v1")
    assert again == result


def test_nonstandard_tables_are_reported_and_never_guessed_into_products(monkeypatch):
    pdf_tables(
        monkeypatch,
        [
            [
                [
                    ["系列", "品名", "系列", "品名"],
                    ["测试系", "演示馅料甲", "测试系乙", "演示馅料乙"],
                ],
                [["1", "00001", "演示原料", "恒温", "示例产地"]],
            ]
        ],
    )
    result = extract_pdf_catalog(CONTENT, "隔离表.pdf", "TEST-v1")
    assert result["rows"] == []
    assert len(result["skipped_tables"]) == 2
    assert all(row["reason"] == "unsupported_column_count" for row in result["skipped_tables"])
    assert any("未猜测" in warning for warning in result["warnings"])


def test_six_columns_without_known_header_cannot_establish_column_meanings(monkeypatch):
    pdf_tables(monkeypatch, [[[["1", "00001", "unrelated", "label", "other", "meaning"]]]])
    result = extract_pdf_catalog(CONTENT, "隔离表.pdf", "TEST-v1")
    assert result["rows"] == []
    assert result["skipped_tables"][0]["reason"] == "unrecognized_header"


def test_bad_identity_cells_are_skipped_without_losing_valid_rows(monkeypatch):
    pdf_tables(
        monkeypatch,
        [
            [
                [
                    HEADER,
                    ["1", "00001", "【演示】有效行", "1kg", "", ""],
                    ["", "00002", "【演示】缺行号", "2kg", "", ""],
                    ["2", "", "【演示】缺编号", "3kg", "", ""],
                    ["3", "00003", "", "4kg", "", ""],
                ]
            ]
        ],
    )
    result = extract_pdf_catalog(CONTENT, "隔离表.pdf", "TEST-v1")
    assert len(result["rows"]) == 1
    assert result["skipped_tables"][0]["rows"] == [3, 4, 5]


def test_reference_catalogue_import_is_queryable_reusable_and_idempotent(monkeypatch, tmp_path):
    pdf_tables(monkeypatch, sample_pages())
    first = import_pdf_catalog(CONTENT, tmp_path, filename="隔离产品表.pdf", version="TEST-v1")
    assert first["catalog_imported"] == first["reference_products"] == 4
    assert first["catalog_changed"]
    before = (tmp_path / "products.json").read_bytes()
    second = import_pdf_catalog(CONTENT, tmp_path, filename="隔离产品表.pdf", version="TEST-v1")
    assert second["catalog_imported"] == 4
    assert second["reference_products"] == 4
    assert not second["catalog_changed"]
    assert (tmp_path / "products.json").read_bytes() == before
    # Recreating Catalog simulates loading the same persistent data on restart.
    reloaded = Catalog.load(tmp_path)
    assert len(reloaded.search("编号00001", {"products"})) == 2
    assert not reloaded.search("编号00001", {"knowledge"})
    rows = reloaded.search("编号00001", {"products"})
    assert reloaded.needs_clarification(rows, "编号00001是什么规格")
    assert all(row["status"] == "reference" for row in rows)
