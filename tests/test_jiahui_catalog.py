"""Business-data isolation and import integrity checks; all use temporary folders."""

import csv
import json
from pathlib import Path

import pytest

from app.jiahui.catalog import FIELDS, Catalog
from app.jiahui.import_catalog import import_csv


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=[*FIELDS, "状态", "访问范围"])
        writer.writeheader()
        writer.writerows(rows)
    return path


def record(identity="p1", **fields):
    return {
        "内部记录ID": identity,
        "名称": "测试蛋黄液",
        "商品编号": "00001",
        "规格": "1kg/盒",
        "版本": "v1",
        **fields,
    }


def test_empty_catalog_never_reads_ecommerce(tmp_path):
    assert Catalog.load(tmp_path).records == []
    assert Catalog.load(tmp_path).search("退货政策", ["products"]) == []


def test_codes_remain_strings_duplicate_codes_do_not_overwrite(tmp_path):
    data = tmp_path / "data"
    source = write_csv(
        tmp_path / "catalog.csv", [record(), record("p2", 名称="另一产品", 规格="20kg/箱")]
    )
    import_csv(source, data, status="current")
    catalog = Catalog.load(data)
    assert len(catalog.records) == 2
    found = catalog.search("商品编号00001有什么规格？", ["products"])
    assert len(found) == 2
    assert all(r["code"] == "00001" for r in found)
    assert catalog.needs_clarification(found, "商品编号00001有什么规格？")
    assert not catalog.needs_clarification(found, "比较商品编号00001的两个规格")
    assert not catalog.needs_clarification(found, "我要00001的1kg/盒")


def test_import_repeat_is_noop_and_update_is_backed_up(tmp_path):
    data = tmp_path / "data"
    source = write_csv(tmp_path / "catalog.csv", [record()])
    first = import_csv(source, data, status="current")
    assert first["changed"]
    original = (data / "products.json").read_bytes()
    assert not import_csv(source, data, status="current")["changed"]
    assert (data / "products.json").read_bytes() == original
    write_csv(source, [record(规格="5kg/桶")])
    changed = import_csv(source, data, status="current")
    assert changed["changed"]
    assert Path(changed["backup"]).read_bytes() == original
    assert Catalog.load(data).records[0]["spec"] == "5kg/桶"


def test_versions_and_dates_are_not_guessed(tmp_path):
    data = tmp_path / "data"
    source = write_csv(tmp_path / "catalog.csv", [record()])
    import_csv(source, data, status="current")
    write_csv(source, [record(版本="v2", 规格="2kg/盒")])
    import_csv(source, data, status="current")
    catalog = Catalog.load(data)
    assert {r["version"]: r["status"] for r in catalog.records} == {
        "v1": "historical",
        "v2": "current",
    }
    assert [r["version"] for r in catalog.search("00001", ["products"])] == ["v2"]
    assert len(catalog.search("00001", ["products"], include_history=True)) == 2
    assert catalog.records[0]["effective_date"] == ""
    write_csv(source, [record(版本="v3", 有效日期="2999-01-01")])
    import_csv(source, data, status="current")
    assert Catalog.load(data).search("00001", ["products"]) == []


def test_drafts_and_unauthorized_rows_never_retrieved(tmp_path):
    source = write_csv(tmp_path / "catalog.csv", [record()])
    data = tmp_path / "data"
    import_csv(source, data)
    assert Catalog.load(data).search("00001", ["*"]) == []
    write_csv(source, [record(状态="current", 访问范围="procurement")])
    import_csv(source, data)
    catalog = Catalog.load(data)
    assert catalog.search("00001", ["products"]) == []
    assert len(catalog.search("00001", ["procurement"])) == 1
    assert len(catalog.search("00001", ["*"])) == 1


def test_source_and_alias_fields_preserved_without_inference(tmp_path):
    source = write_csv(
        tmp_path / "catalog.csv",
        [
            record(
                别名="黄液;测试别名",
                来源文件="目录.pdf",
                来源位置="PDF第5页，原表第34行",
                储存条件="冷藏",
            )
        ],
    )
    data = tmp_path / "data"
    import_csv(source, data, status="current")
    row = Catalog.load(data).search("请问测试别名的资料", ["products"])[0]
    assert row["aliases"] == ["黄液", "测试别名"]
    assert row["source_file"] == "目录.pdf"
    assert "PDF第5页" in row["source_location"]
    assert "第2行" in row["source_location"]
    assert row["storage"] == "冷藏"
    assert row["shelf_life"] == row["pricing_unit"] == ""
    assert row["conversions"] == []
    assert "保质期：尚未录入/需要确认" in Catalog.to_document(row)
    assert len(list((data / "sources/imports").glob("*.csv"))) == 1


@pytest.mark.parametrize(
    "fields",
    [
        {"有效日期": "tomorrow"},
        {"来源文件": "../../private.env"},
        {"来源文件": "https://example.com/source"},
        {"状态": "invented"},
        {"访问范围": "*"},
    ],
)
def test_invalid_data_cannot_mutate_catalog(tmp_path, fields):
    source = write_csv(tmp_path / "catalog.csv", [record()])
    data = tmp_path / "data"
    import_csv(source, data, status="current")
    original = (data / "products.json").read_bytes()
    write_csv(source, [record(**fields)])
    with pytest.raises(ValueError):
        import_csv(source, data)
    assert (data / "products.json").read_bytes() == original


def test_duplicate_record_identity_rejected_before_writing(tmp_path):
    source = write_csv(tmp_path / "catalog.csv", [record(), record(规格="2kg/盒")])
    with pytest.raises(ValueError, match="内部记录ID与版本重复"):
        import_csv(source, tmp_path / "data")
    assert not (tmp_path / "data/products.json").exists()


def test_unknown_catalog_schema_is_not_overwritten(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "products.json").write_text(json.dumps({"schema_version": 999, "records": []}))
    original = (data / "products.json").read_bytes()
    source = write_csv(tmp_path / "catalog.csv", [record()])
    with pytest.raises(ValueError, match="不支持"):
        import_csv(source, data)
    assert (data / "products.json").read_bytes() == original


def test_unknown_codes_and_generic_questions_do_not_match_products(tmp_path):
    source = write_csv(tmp_path / "catalog.csv", [record()])
    data = tmp_path / "data"
    import_csv(source, data, status="current")
    catalog = Catalog.load(data)
    assert catalog.search("编号99999的蛋黄液", ["products"]) == []
    assert catalog.search("99999蛋黄液", ["products"]) == []
    assert catalog.search("保质期是多少", ["products"]) == []
    assert catalog.search("测试蛋黄液的保质期是多少", ["products"])


@pytest.mark.parametrize(
    "conversion",
    [
        "{}",
        '[{"from":"箱","to":"盒","factor":"0"}]',
        '[{"from":"箱","to":"盒","factor":"NaN"}]',
        '[{"from":"箱","to":"盒","factor":"2*12"}]',
        '[{"from":"","to":"盒","factor":"12"}]',
    ],
)
def test_unconfirmed_or_invalid_conversions_rejected(tmp_path, conversion):
    source = write_csv(tmp_path / "catalog.csv", [record(已确认的单位换算=conversion)])
    with pytest.raises(ValueError):
        import_csv(source, tmp_path / "data")


def test_explicit_unit_conversion_remains_decimal_string(tmp_path):
    conversion = '[{"from":"箱","to":"盒","factor":"12"}]'
    source = write_csv(tmp_path / "catalog.csv", [record(已确认的单位换算=conversion)])
    import_csv(source, tmp_path / "data", status="current")
    row = Catalog.load(tmp_path / "data").records[0]
    assert row["conversions"] == [{"from": "箱", "to": "盒", "factor": "12"}]


def test_internal_id_selects_one_duplicate_code_record(tmp_path):
    source = write_csv(
        tmp_path / "catalog.csv", [record("JH-001"), record("JH-002", 规格="20kg/箱")]
    )
    import_csv(source, tmp_path / "data", status="current")
    selected = Catalog.load(tmp_path / "data").search("我要JH-002的资料", ["products"])
    assert [r["id"] for r in selected] == ["JH-002"]


def test_code_prefix_does_not_match_hyphenated_code(tmp_path):
    source = write_csv(
        tmp_path / "catalog.csv", [record("p1", 商品编号="001"), record("p2", 商品编号="001-2")]
    )
    import_csv(source, tmp_path / "data", status="current")
    selected = Catalog.load(tmp_path / "data").search("查一下编号001-2", ["products"])
    assert [r["code"] for r in selected] == ["001-2"]


@pytest.mark.parametrize(
    "question",
    [
        "查询内部记录ID DEMO-ONLY-001",
        "查询内部记录IDDEMO-ONLY-001",
        "查询内部记录 ID：DEMO-ONLY-001",
        "我要 DEMO-ONLY-001 的资料",
        "内部记录ＩＤ　ＤＥＭＯ－ＯＮＬＹ－００１",
    ],
)
def test_natural_identifier_labels_resolve_one_record(tmp_path, question):
    source = write_csv(
        tmp_path / "catalog.csv", [record("DEMO-ONLY-001"), record("DEMO-ONLY-002", 规格="20kg/箱")]
    )
    import_csv(source, tmp_path / "data", status="current")
    selected = Catalog.load(tmp_path / "data").search(question, ["products"])
    assert [row["id"] for row in selected] == ["DEMO-ONLY-001"]


@pytest.mark.parametrize(
    "question",
    ["SKU ABC001 是什么商品", "code ABC001 的资料", "商品编号 ABC001", "商品编号ＡＢＣ００１"],
)
def test_code_boundaries_preserve_spaces_between_description_and_code(tmp_path, question):
    source = write_csv(
        tmp_path / "catalog.csv",
        [record("p1", 商品编号="ABC001"), record("p2", 商品编号="ABC001-2")],
    )
    import_csv(source, tmp_path / "data", status="current")
    selected = Catalog.load(tmp_path / "data").search(question, ["products"])
    assert [row["code"] for row in selected] == ["ABC001"]
