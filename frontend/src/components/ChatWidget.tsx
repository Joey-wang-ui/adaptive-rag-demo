import { useEffect, useRef, useState } from "react";
import { MessageCircle, Plus, X } from "lucide-react";
import type { useChat } from "../hooks/useChat";
import type { ActionProposal } from "../types";
import { Composer } from "./Composer";
import { MessageView } from "./MessageView";

interface ChatWidgetProps {
  chat: ReturnType<typeof useChat>;
  healthy: boolean | null;
  onConfirm: (action: ActionProposal, messageId: string) => void;
}

export function ChatWidget({ chat, healthy, onConfirm }: ChatWidgetProps) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const launcherRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  function close() {
    setOpen(false);
    launcherRef.current?.focus();
  }

  return <>
    {open && <section
      id="agent-chat-widget"
      className="chat-widget"
      role="dialog"
      aria-labelledby="agent-chat-title"
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.stopPropagation();
          close();
        }
      }}
    >
      <header className="chat-widget-header">
        <div><strong id="agent-chat-title">Adaptive RAG 助手</strong><span role="status">{healthy === null ? "正在连接服务…" : healthy ? "政策问答 · 订单与物流" : "服务未连接，请稍后重试"}</span></div>
        <button className="icon-button" aria-label="开始新的助手对话" title="新对话" disabled={chat.busy} onClick={() => { chat.select(null); setDraft(""); inputRef.current?.focus(); }}><Plus size={19} /></button>
        <button className="icon-button" aria-label="收起助手弹窗" title="收起" onClick={close}><X size={20} /></button>
      </header>
      <div className="chat-widget-content">
        {chat.active
          ? <MessageView conversation={chat.active} busy={chat.busy} onConfirm={onConfirm} onCancel={(messageId) => chat.dismissAction(chat.active!.id, messageId)} />
          : <div className="chat-widget-welcome">
              <span className="chat-widget-avatar"><MessageCircle size={25} /></span>
              <h2>你好，有什么可以帮你？</h2>
              <p>可以咨询退货政策、查看订单物流，或了解售后流程。</p>
              {["定制商品过了七天，但有质量问题还能退吗？", "帮我查一下 ORD10001 的物流进度"].map((prompt) => <button key={prompt} onClick={() => { setDraft(prompt); inputRef.current?.focus(); }}>{prompt}</button>)}
            </div>}
      </div>
      <Composer busy={chat.busy} onSend={(message) => void chat.send(message)} onStop={chat.stop} draft={draft} onDraft={setDraft} inputRef={inputRef} />
    </section>}
    <button
      ref={launcherRef}
      className="chat-widget-launcher"
      aria-label={open ? "收起 Agent 助手" : "打开 Agent 助手"}
      aria-expanded={open}
      aria-controls={open ? "agent-chat-widget" : undefined}
      onClick={() => { if (open) close(); else setOpen(true); }}
    ><MessageCircle size={23} /><span>Agent 助手</span>{chat.busy && <span className="chat-widget-busy" aria-label="正在回复" />}</button>
  </>;
}
