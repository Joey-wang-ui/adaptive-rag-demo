import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Composer } from "./Composer";

describe("opt-in web search", () => {
  function html(enabled: boolean, draft = "查公开公告") {
    return renderToStaticMarkup(<Composer busy={false} onSend={() => {}} onStop={() => {}} draft={draft} onDraft={() => {}} inputRef={createRef<HTMLTextAreaElement>()} webSearch={enabled} onWebSearch={() => {}} />);
  }
  it("defaults to off and explains per-question query sharing when enabled", () => {
    expect(html(false)).toContain('aria-pressed="false"');
    expect(html(false)).not.toContain("仅本次提问联网");
    expect(html(true)).toContain('aria-pressed="true"');
    expect(html(true)).toContain("发送后自动关闭");
    expect(html(true)).toContain("可能产生API费用");
  });
  it("counts Unicode characters and disables an overlong search", () => {
    expect(html(true, "😀".repeat(128))).not.toContain("当前超出128字");
    expect(html(true, "😀".repeat(129))).toContain("当前超出128字");
  });
});
