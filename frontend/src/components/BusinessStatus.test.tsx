import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BusinessStatus, capabilityLabels } from "./BusinessStatus";
import type { BusinessProfile } from "../types";

const profile: BusinessProfile = {
  mode: "jiahui", name: "嘉惠内部业务助手", welcome: "查询已录入的食品原料资料",
  examples: [], product_count: 0, catalog_status: "尚未录入产品资料",
  capabilities: { llm: "offline_excerpt", embedding: "hash", reranker: "lexical" },
};

describe("Jiahui readiness presentation", () => {
  it("shows an empty catalog and accurately identifies all degraded providers", () => {
    const html = renderToStaticMarkup(<BusinessStatus profile={profile} />);
    expect(html).toContain("尚未录入产品资料");
    expect(html).toContain("离线资料节选，非生成式回答");
    expect(html).toContain("非真实语义 Embedding");
    expect(html).toContain("词项匹配降级");
    expect(html).toContain("价格、库存和订单尚未接入业务数据");
  });

  it("preserves an authorization-specific catalog status without revealing counts", () => {
    const html = renderToStaticMarkup(<BusinessStatus profile={{ ...profile, catalog_status: "无产品资料访问权限" }} />);
    expect(html).toContain("无产品资料访问权限");
    expect(html).not.toContain("尚未录入产品资料");
  });

  it("does not promote unknown provider status to verified readiness", () => {
    expect(capabilityLabels({ llm: "unavailable", embedding: "", reranker: "unknown" })).toEqual([
      "生成模型状态：unavailable", "Embedding 状态：未核验", "重排序状态：unknown",
    ]);
  });
});
