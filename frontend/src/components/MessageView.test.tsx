import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { MessageView } from "./MessageView";
import type { Conversation } from "../types";

describe("answer presentation", () => {
  it("keeps citations but hides business write confirmations in read-only mode", () => {
    const conversation: Conversation = {
      id: "jiahui", title: "产品资料", updatedAt: 0,
      messages: [{ id: "answer", role: "assistant", state: "complete", content: "资料中的规格为测试规格。",
        citations: [{ chunk_id: "page4-row3", document_id: "catalog", title: "目录", section: "PDF第4页 行3" }],
        pendingAction: { action_id: "old-action", tool_name: "cancel_order", arguments: {}, risk: "high", summary: "取消订单" },
      }],
    };
    const html = renderToStaticMarkup(<MessageView conversation={conversation} busy={false} onConfirm={() => {}} onCancel={() => {}} readOnly />);
    expect(html).toContain("PDF第4页 行3");
    expect(html).not.toContain("确认执行");
    expect(html).not.toContain("取消订单");
  });
  it("shows the answer and source without internal timing or route metadata", () => {
    const conversation: Conversation = {
      id: "chat-1",
      title: "退货政策",
      updatedAt: 0,
      messages: [{
        id: "answer-1",
        role: "assistant",
        state: "complete",
        content: "根据退货规则，质量问题可以申请售后。",
        strategy: "multi",
        latencyMs: 4200,
        citations: [{ chunk_id: "chunk-1", document_id: "policy-1", title: "退货规则", section: null }],
      }],
    };
    const html = renderToStaticMarkup(
      <MessageView conversation={conversation} busy={false} onConfirm={() => {}} onCancel={() => {}} />,
    );
    expect(html).toContain("质量问题可以申请售后");
    expect(html).toContain("退货规则");
    expect(html).not.toContain("多跳检索");
    expect(html).not.toContain("4.2 秒");
  });
});

it("renders public source links with provenance and blocks executable URLs", () => {
  const conversation: Conversation = { id: "web", title: "搜索", updatedAt: 0, messages: [{ id: "answer", role: "assistant", state: "complete", content: "公开网页参考", citations: [
    { document_id: "web1", chunk_id: "w1", title: "公开公告", section: "公开网页 · 查询时间", source_kind: "web", url: "https://example.com/news" },
    { document_id: "web2", chunk_id: "w2", title: "无效来源", section: null, source_kind: "web", url: "javascript:alert(1)" },
  ] }] };
  const html = renderToStaticMarkup(<MessageView conversation={conversation} busy={false} onConfirm={() => {}} onCancel={() => {}} />);
  expect(html).toContain('href="https://example.com/news"');
  expect(html).toContain('rel="noopener noreferrer"');
  expect(html).toContain("公开网页 · 查询时间");
  expect(html).not.toContain('href="javascript:');
});
