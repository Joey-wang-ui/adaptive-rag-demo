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

export function catalogSummary(profile: BusinessProfile): string {
  const documents = profile.knowledge_document_count ?? 0;
  const files = profile.knowledge_file_count;
  if (documents > 0) {
    const knowledge = `知识库已接入 ${files ? `${files} 份资料，` : ""}${documents} 页/章节，可直接提问`;
    const catalog = profile.product_count > 0 ? `可查询产品目录 ${profile.product_count} 条${profile.reference_product_count ? `（参考目录 ${profile.reference_product_count} 条）` : ""}` : "尚未建立可查询产品目录";
    return `${knowledge}；${catalog}。`;
  }
  return profile.catalog_status || (profile.product_count === 0 ? "尚未录入产品资料" : `可查询产品资料：${profile.product_count} 条`);
}

export function BusinessStatus({ profile }: { profile: BusinessProfile }) {
  return <aside className="business-status" aria-label="嘉惠资料与模型状态">
    <strong>{catalogSummary(profile)}</strong>
    <span>{(profile.reference_document_count ?? 0) > 0 && `其中 ${profile.reference_document_count} 页/章节为参考资料，现行有效性待确认。`}第一版仅查询资料；价格、库存和订单尚未接入业务数据。</span>
    <div>{capabilityLabels(profile.capabilities).map((label) => <span key={label}>{label}</span>)}</div>
  </aside>;
}
