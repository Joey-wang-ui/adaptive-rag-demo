import { describe, expect, it } from "vitest";
import { importSummary } from "./DesktopSettings";

describe("desktop import outcomes", () => {
  it("makes imported PDF knowledge immediately queryable without claiming confirmed product records", () => {
    const summary = importSummary(true, { pages: 22, imported_pages: 22 }, false);
    expect(summary).toContain("知识库已接入 22 页，可以直接提问");
    expect(summary).toContain("现行有效性待确认");
    expect(summary).not.toContain("草稿");
  });
  it("reports the actual searchable pages when a PDF includes unreadable pages", () => {
    const summary = importSummary(true, { pages: 22, imported_pages: 20, skipped_pages: [{ page: 2, reason: "无可检索文字" }, { page: 3, reason: "需OCR核对" }] }, false);
    expect(summary).toContain("知识库已接入 20 页");
    expect(summary).toContain("2 页未导入");
    expect(summary).not.toContain("接入 22 页");
  });
  it("does not present zero imported pages as success", () => {
    expect(importSummary(true, { pages: 3, imported_pages: 0 }, false)).toContain("未导入可检索的文字页");
  });
  it("keeps draft CSV products distinct from active product retrieval", () => {
    expect(importSummary(false, { imported: 2 }, false)).toContain("产品草稿");
    expect(importSummary(false, { imported: 2 }, false)).toContain("尚不参与普通产品查询");
    expect(importSummary(false, { imported: 2 }, true)).toContain("现行产品资料，现在可以查询");
  });
});

it("reports a PDF reference product catalogue as queryable and persisted without claiming it is current", () => {
  const summary = importSummary(true, { imported_pages: 22, catalog_imported: 324 }, false);
  expect(summary).toContain("参考产品目录 324 条、原文 22 页");
  expect(summary).toContain("已可查询并保存，下次打开可继续使用");
  expect(summary).toContain("现行有效性待确认");
  expect(summary).not.toContain("现行产品资料");
});
