import { useEffect, useRef, useState } from "react";
import { ArrowDown, CircleHelp, Menu, PanelLeftOpen } from "lucide-react";
import { AuthenticationError, checkHealth, getPrincipal, getProfile, login, logout } from "./lib/api";
import { useChat } from "./hooks/useChat";
import { Composer } from "./components/Composer";
import { ChatWidget } from "./components/ChatWidget";
import { Logo } from "./components/Logo";
import { MessageView } from "./components/MessageView";
import { SearchDialog } from "./components/SearchDialog";
import { Sidebar } from "./components/Sidebar";
import type { ActionProposal, BusinessProfile, Principal } from "./types";
import { BusinessStatus } from "./components/BusinessStatus";

const SUGGESTIONS = [
  { icon: "↗", title: "退货政策", prompt: "定制商品过了七天，但有质量问题还能退吗？" },
  { icon: "◎", title: "物流进度", prompt: "帮我查一下 ORD10001 的物流进度" },
  { icon: "◇", title: "支付与发票", prompt: "电子发票一般多久可以开具？" },
  { icon: "✳", title: "售后处理", prompt: "退货申请需要提供哪些信息？" },
];

function Workspace({ profile, principal, onLogout }: { profile: BusinessProfile; principal: Principal | null; onLogout: () => void }) {
  const jiahui = profile.mode === "jiahui";
  const chat = useChat({ persistent: !jiahui, userId: principal?.username, onUnauthorized: onLogout });
  const suggestions = jiahui ? profile.examples.slice(0, 4).map((prompt, index) => ({
    icon: ["◎", "◇", "↗", "✳"][index], title: ["产品资料", "规格核实", "资料比较", "来源与版本"][index], prompt,
  })) : SUGGESTIONS;
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [healthy, setHealthy] = useState<boolean | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">(() => localStorage.getItem("adaptive-rag-theme") === "dark" ? "dark" : "light");
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    document.title = profile.name;
  }, [profile.name]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("adaptive-rag-theme", theme);
  }, [theme]);
  useEffect(() => {
    const controller = new AbortController();
    void checkHealth(controller.signal).then(setHealthy);
    return () => controller.abort();
  }, []);
  useEffect(() => {
    function onKey(event: globalThis.KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setSearchOpen(true); }
      if (event.key === "Escape") { setSearchOpen(false); setSidebarOpen(false); }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function newChat() {
    chat.select(null);
    setDraft("");
    setSidebarOpen(false);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }

  function selectChat(id: string) { chat.select(id); setSidebarOpen(false); setSearchOpen(false); }
  function suggest(prompt: string) { setDraft(prompt); inputRef.current?.focus(); }
  function confirm(action: ActionProposal, messageId: string) {
    if (!chat.active) return;
    const conversationId = chat.active.id;
    void chat.send("确认执行", action.action_id).then((succeeded) => {
      if (succeeded) chat.dismissAction(conversationId, messageId);
    });
  }

  return <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
    <Sidebar conversations={chat.conversations} activeId={chat.active?.id ?? null} open={sidebarOpen} onClose={() => { setSidebarOpen(false); setSidebarCollapsed(true); }} onNew={newChat} onSelect={selectChat} onRemove={chat.remove} onSearch={() => setSearchOpen(true)} onTheme={() => setTheme(theme === "light" ? "dark" : "light")} theme={theme} profileName={profile.name} identity={principal ? `${principal.username} · ${principal.role}` : undefined} />
    <main className="main-panel">
      <header className="topbar">
        <button className="icon-button open-sidebar" aria-label="打开侧栏" title="打开侧栏" onClick={() => { setSidebarCollapsed(false); setSidebarOpen(true); }}>{sidebarCollapsed ? <PanelLeftOpen size={20} /> : <Menu size={20} />}</button>
        <span className="model-title">{profile.name}</span>
        <div className="topbar-right">{jiahui && <button className="logout-button" onClick={onLogout}>退出登录</button>}<span className={`connection-pill ${healthy === false ? "offline" : ""}`}><span />{healthy === null ? "连接中" : healthy ? "已连接" : "服务未连接"}</span><button className="icon-button help-button" title="说明" aria-label="说明" onClick={() => window.open("https://github.com/JEen-wang/Adaptive-rag", "_blank", "noopener,noreferrer")}><CircleHelp size={19} /></button></div>
      </header>
      {jiahui && <BusinessStatus profile={profile} />}
      {chat.active ? <div className="chat-content"><MessageView readOnly={jiahui} conversation={chat.active} busy={chat.busy} onConfirm={confirm} onCancel={(messageId) => chat.dismissAction(chat.active!.id, messageId)} /></div>
        : <div className="welcome"><div className="welcome-inner"><Logo /><h1>{jiahui ? profile.name : "有什么可以帮忙的？"}</h1><p>{jiahui ? profile.welcome : "让知识、订单与售后服务在一次对话中自然协作。"}</p><div className="suggestions">{suggestions.map((item) => <button key={item.title} onClick={() => suggest(item.prompt)}><span className="suggestion-icon">{item.icon}</span><span><strong>{item.title}</strong><small>{item.prompt}</small></span><ArrowDown size={15} /></button>)}</div></div></div>}
      <Composer busy={chat.busy} onSend={(message) => void chat.send(message)} onStop={chat.stop} draft={draft} onDraft={setDraft} inputRef={inputRef} assistantName={jiahui ? profile.name : undefined} />
    </main>
    {searchOpen && <SearchDialog conversations={chat.conversations} onClose={() => setSearchOpen(false)} onSelect={selectChat} />}
    <ChatWidget chat={chat} healthy={healthy} onConfirm={confirm} profile={profile} />
  </div>;
}

function LoginForm({ profile, onLogin }: { profile: BusinessProfile; onLogin: (principal: Principal) => Promise<void> }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return <main className="login-shell"><form className="login-card" onSubmit={(event) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    void login(username, password).then(onLogin).catch((reason: unknown) => {
      setError(reason instanceof AuthenticationError ? "账号或密码不正确，或账号已停用。" : reason instanceof Error ? reason.message : "登录失败，请稍后重试。");
    }).finally(() => { setPassword(""); setBusy(false); });
  }}><Logo /><h1>{profile.name}</h1><p>{profile.welcome}</p>
    <p>请使用管理员初始化的内部账号登录。</p>
    <label>账号<input required autoComplete="username" value={username} maxLength={128} onChange={(event) => setUsername(event.target.value)} /></label>
    <label>密码<input required type="password" autoComplete="current-password" value={password} maxLength={1024} onChange={(event) => setPassword(event.target.value)} /></label>
    {error && <p className="login-error" role="alert">{error}</p>}
    <button type="submit" disabled={busy}>{busy ? "登录中…" : "登录"}</button>
    <small>第一版提供资料查询；价格、库存和订单尚未接入业务数据。</small>
  </form></main>;
}

function App() {
  const [profile, setProfile] = useState<BusinessProfile | null>(null);
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const channelRef = useRef<BroadcastChannel | null>(null);
  useEffect(() => {
    let alive = true;
    async function initialize() {
      try {
        const next = await getProfile();
        if (!alive) return;
        setProfile(next);
        if (next.mode === "jiahui") {
          try {
            const user = await getPrincipal();
            const personalized = await getProfile();
            if (alive) { setPrincipal(user); setProfile(personalized); }
          } catch (reason) { if (!(reason instanceof AuthenticationError)) throw reason; }
        }
        if (alive) setReady(true);
      } catch (reason) { if (alive) setError(reason instanceof Error ? reason.message : "无法读取服务配置。"); }
    }
    void initialize();
    return () => { alive = false; };
  }, []);

  useEffect(() => {
    if (profile?.mode !== "jiahui" || typeof BroadcastChannel === "undefined") return;
    const channel = new BroadcastChannel("jiahui-auth-state");
    channelRef.current = channel;
    channel.onmessage = () => setPrincipal(null);
    return () => { channel.close(); channelRef.current = null; };
  }, [profile?.mode]);

  useEffect(() => {
    if (profile?.mode !== "jiahui" || !principal) return;
    let active = true;
    async function verifyAccess() {
      if (document.visibilityState !== "visible") return;
      try {
        const user = await getPrincipal();
        if (!active) return;
        if (JSON.stringify(user) !== JSON.stringify(principal)) {
          const next = await getProfile();
          if (active) { setProfile(next); setPrincipal(user); }
        }
      } catch (reason) {
        if (active && reason instanceof AuthenticationError) setPrincipal(null);
      }
    }
    const timer = window.setInterval(() => void verifyAccess(), 60_000);
    document.addEventListener("visibilitychange", verifyAccess);
    return () => { active = false; window.clearInterval(timer); document.removeEventListener("visibilitychange", verifyAccess); };
  }, [profile?.mode, principal]);

  async function signOut() {
    // Unmount immediately: no response from an old identity remains on screen or in local storage.
    setPrincipal(null); setError("");
    channelRef.current?.postMessage("signed-out");
    try { await logout(); } catch (reason) { setError(reason instanceof Error ? reason.message : "退出未完成，请重试。"); }
  }
  if (error) return <main className="login-shell"><div className="login-card" role="alert"><h1>服务暂时不可用</h1><p>{error}</p><button onClick={() => window.location.reload()}>重新连接</button></div></main>;
  if (!ready || !profile) return <main className="login-shell" role="status">正在连接服务…</main>;
  if (profile.mode === "jiahui" && !principal) return <LoginForm profile={profile} onLogin={async (user) => {
    const next = await getProfile(); setProfile(next); setPrincipal(user); channelRef.current?.postMessage("signed-in");
  }} />;
  return <Workspace key={`${profile.mode}:${JSON.stringify(principal ?? "demo")}`} profile={profile} principal={principal} onLogout={() => void signOut()} />;
}

export default App;
