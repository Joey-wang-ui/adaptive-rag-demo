import type { BusinessProfile } from "../types";

export function capabilityLabels(capabilities: BusinessProfile["capabilities"]): string[] {
  const llm = capabilities.llm?.toLowerCase() ?? "";
  const embedding = capabilities.embedding?.toLowerCase() ?? "";
  const reranker = capabilities.reranker?.toLowerCase() ?? "";
  return [
    llm === "offline_excerpt" ? "离线资料节选，非生成式回答" : ["deepseek", "ollama"].includes(llm) ? `生成模型：${capabilities.llm}` : `生成模型状态：${capabilities.llm || "未核验"}`,
    embedding === "hash" ? "检索向量：哈希降级，非真实语义 Embedding" : ["local", "remote"].includes(embedding) ? `语义 Embedding：${embedding === "local" ? "本地" : "远程"}` : `Embedding 状态：${capabilities.embedding || "未核验"}`,
    reranker === "lexical" ? "重排序：词项匹配降级" : reranker === "cross_encoder" ? "重排序：Cross-Encoder" : `重排序状态：${capabilities.reranker || "未核验"}`,
  ];
}

export function BusinessStatus({ profile }: { profile: BusinessProfile }) {
  return <aside className="business-status" aria-label="嘉惠资料与模型状态">
    <strong>{profile.catalog_status || (profile.product_count === 0 ? "尚未录入产品资料" : `可查询产品资料：${profile.product_count} 条`)}</strong>
    <span>第一版仅查询资料；价格、库存和订单尚未接入业务数据。</span>
    <div>{capabilityLabels(profile.capabilities).map((label) => <span key={label}>{label}</span>)}</div>
  </aside>;
}
