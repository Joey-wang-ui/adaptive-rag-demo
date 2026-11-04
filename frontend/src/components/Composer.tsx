import type { FormEvent, KeyboardEvent, RefObject } from "react";
import { ArrowUp, Globe, Square } from "lucide-react";

interface ComposerProps {
  busy: boolean;
  onSend: (message: string) => void;
  onStop: () => void;
  draft: string;
  onDraft: (value: string) => void;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  assistantName?: string;
  webSearch?: boolean;
  onWebSearch?: (enabled: boolean) => void;
}

export function Composer({ busy, onSend, onStop, draft, onDraft, inputRef, assistantName, webSearch = false, onWebSearch }: ComposerProps) {
  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!busy && draft.trim() && (!webSearch || Array.from(draft.trim()).length <= 128)) { onSend(draft); onDraft(""); }
  }
  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      submit();
    }
  }
  return <div className="composer-wrap">
    <form className="composer" onSubmit={submit}>
      <textarea ref={inputRef} value={draft} onChange={(event) => { onDraft(event.target.value); event.target.style.height = "auto"; event.target.style.height = `${Math.min(event.target.scrollHeight, 180)}px`; }} onKeyDown={keyDown} placeholder="询问任何问题" rows={1} maxLength={4000} aria-label="输入消息" />
      <div className="composer-bottom"><div className="composer-options">{onWebSearch && <button type="button" className={`web-search-toggle ${webSearch ? "active" : ""}`} aria-pressed={webSearch} disabled={busy} onClick={() => onWebSearch(!webSearch)}><Globe size={15} />联网搜索</button>}<span className="composer-hint">Shift + Enter 换行</span></div>
        {busy ? <button type="button" className="send-button stop-button" aria-label="停止生成" title="停止生成" onClick={onStop}><Square size={15} fill="currentColor" /></button>
          : <button type="button" className="send-button" aria-label="发送消息" title="发送消息" disabled={!draft.trim() || (webSearch && Array.from(draft.trim()).length > 128)} onClick={() => submit()}><ArrowUp size={19} strokeWidth={2.5} /></button>}
      </div>
    </form>
    {webSearch && <p className="web-search-notice" role="status">仅本次提问联网，发送后自动关闭。搜索词最多128字，会发送给DeepSeek，可能产生API费用；请勿输入内部敏感信息。{Array.from(draft.trim()).length > 128 && <strong> 当前超出128字，请缩短搜索词。</strong>}</p>}
    <p className="composer-disclaimer">{assistantName ? `${assistantName}也可能出错。请核对资料版本和重要信息。` : "Adaptive RAG 也可能出错。请核对重要信息。"}</p>
  </div>;
}
