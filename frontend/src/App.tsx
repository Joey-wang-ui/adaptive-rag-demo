import { useEffect, useRef, useState } from "react";
import { ArrowDown, CircleHelp, Leaf, Menu, PanelLeftOpen, ShieldCheck, UploadCloud } from "lucide-react";
import { AuthenticationError, checkHealth, getPrincipal, getProfile, getDesktopStatus, login, logout } from "./lib/api";
import { useChat } from "./hooks/useChat";
import { Composer } from "./components/Composer";
import { ChatWidget } from "./components/ChatWidget";
import { Logo } from "./components/Logo";
import { MessageView } from "./components/MessageView";
import { SearchDialog } from "./components/SearchDialog";
import { Sidebar } from "./components/Sidebar";
import type { ActionProposal, BusinessProfile, DesktopStatus, Principal } from "./types";
import { BusinessStatus } from "./components/BusinessStatus";
import { DesktopSetup } from "./components/DesktopSetup";
import { DesktopSettings } from "./components/DesktopSettings";

const SUGGESTIONS = [
  { icon: "↗", title: "退货政策", prompt: "定制商品过了七天，但有质量问题还能退吗？" },
  { icon: "◎", title: "物流进度", prompt: "帮我查一下 ORD10001 的物流进度" },
  { icon: "◇", title: "支付与发票", prompt: "电子发票一般多久可以开具？" },
  { icon: "✳", title: "售后处理", prompt: "退货申请需要提供哪些信息？" },
];

function Workspace({ profile, principal, onLogout, desktop, onUpdated }: { profile: BusinessProfile; principal: Principal | null; onLogout: () => void; desktop: boolean; onUpdated: () => Promise<void> }) {
  const jiahui = profile.mode === "jiahui";
  const chat = useChat({ persistent: !jiahui, userId: principal?.username, onUnauthorized: onLogout });
  const suggestions = jiahui ? profile.examples.slice(0, 4).map((prompt, index) => ({
    icon: ["◎", "◇", "↗", "✳"][index], title: ["产品资料", "规格核实", "资料比较", "来源与版本"][index], prompt,
  })) : SUGGESTIONS;
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [webSearch, setWebSearch] = useState(false);
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
    setWebSearch(false);
    setSidebarOpen(false);
    window.setTimeout(() => inputRef.current?.focus(), 0);
  }

  function selectChat(id: string) { setWebSearch(false); chat.select(id); setSidebarOpen(false); setSearchOpen(false); }
  function suggest(prompt: string) { setDraft(prompt); inputRef.current?.focus(); }
  function confirm(action: ActionProposal, messageId: string) {
    if (!chat.active) return;
    const conversationId = chat.active.id;
    void chat.send("确认执行", action.action_id).then((succeeded) => {
      if (succeeded) chat.dismissAction(conversationId, messageId);
    });
  }

  return <div className={`app-shell ${jiahui ? "jiahui-shell" : ""} ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
    <Sidebar conversations={chat.conversations} activeId={chat.active?.id ?? null} open={sidebarOpen} onClose={() => { setSidebarOpen(false); setSidebarCollapsed(true); }} onNew={newChat} onSelect={selectChat} onRemove={chat.remove} onSearch={() => setSearchOpen(true)} onTheme={() => setTheme(theme === "light" ? "dark" : "light")} theme={theme} profileName={profile.name} identity={principal ? `${principal.username} · ${principal.role}` : undefined} onSettings={desktop && principal?.role === "admin" ? () => setSettingsOpen(true) : undefined} />
    <main className="main-panel">
      <header className="topbar">
        <button className="icon-button open-sidebar" aria-label="打开侧栏" title="打开侧栏" onClick={() => { setSidebarCollapsed(false); setSidebarOpen(true); }}>{sidebarCollapsed ? <PanelLeftOpen size={20} /> : <Menu size={20} />}</button>
        <span className="model-title">{profile.name}</span>
        <div className="topbar-right">{jiahui && <button className="logout-button" onClick={onLogout}>退出登录</button>}<span className={`connection-pill ${healthy === false ? "offline" : ""}`}><span />{healthy === null ? "连接中" : healthy ? "已连接" : "服务未连接"}</span><button className="icon-button help-button" title="说明" aria-label="说明" onClick={() => window.alert(jiahui ? "嘉惠助手基于已授权的产品资料回答，并给出来源。价格、库存、订单尚未接入。关闭软件窗口即可停止本地服务。" : "请参考项目操作说明。")}><CircleHelp size={19} /></button></div>
      </header>
      {jiahui && <BusinessStatus profile={profile} />}
      {chat.active ? <div className="chat-content"><MessageView readOnly={jiahui} conversation={chat.active} busy={chat.busy} onConfirm={confirm} onCancel={(messageId) => chat.dismissAction(chat.active!.id, messageId)} /></div>
        : <div className="welcome"><div className="welcome-inner"><Logo />{jiahui && <span className="eyebrow welcome-eyebrow">JIAHUI · YOUR BUSINESS COMPANION</span>}<h1>{jiahui ? "你好，今天需要核实什么？" : "有什么可以帮忙的？"}</h1><p>{jiahui ? profile.welcome : "让知识、订单与售后服务在一次对话中自然协作。"}</p><div className="suggestions">{suggestions.map((item) => <button key={item.title} onClick={() => suggest(item.prompt)}><span className="suggestion-icon">{item.icon}</span><span><strong>{item.title}</strong><small>{item.prompt}</small></span><ArrowDown size={15} /></button>)}</div>{jiahui && <div className="welcome-trust"><ShieldCheck size={14} /><span>依据业务资料 · 保留来源与版本</span>{desktop && principal?.role === "admin" && <button onClick={() => setSettingsOpen(true)}><UploadCloud size={14} />导入资料</button>}</div>}</div></div>}
      <Composer busy={chat.busy} onSend={(message) => { void chat.send(message, undefined, webSearch); setWebSearch(false); }} onStop={chat.stop} draft={draft} onDraft={setDraft} inputRef={inputRef} assistantName={jiahui ? profile.name : undefined} webSearch={webSearch} onWebSearch={jiahui && (principal?.scopes.includes("knowledge") || principal?.scopes.includes("*")) ? setWebSearch : undefined} />
    </main>
    {searchOpen && <SearchDialog conversations={chat.conversations} onClose={() => setSearchOpen(false)} onSelect={selectChat} />}
    {!jiahui && <ChatWidget chat={chat} healthy={healthy} onConfirm={confirm} profile={profile} />}
    {settingsOpen && <DesktopSettings onClose={() => setSettingsOpen(false)} onUpdated={onUpdated} />}
  </div>;
}

function LoginForm({ profile, onLogin, version }: { profile: BusinessProfile; onLogin: (principal: Principal) => Promise<void>; version?: string }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  return <main className="login-shell"><section className="login-story"><div className="jiahui-wordmark"><span><Leaf size={24} /></span><b>嘉惠<span>JIAHUI WORKSPACE</span></b></div><div><span className="eyebrow">你的食品原料业务伙伴</span><h2>工作里的每个问题，<br />都值得一个靠谱答案。</h2><p>让产品资料更好查，让业务核实更从容。</p><span className="login-proof"><ShieldCheck size={16} />仅使用获授权的业务资料</span></div><small>杭州嘉惠食品有限公司</small></section><form className="login-card" onSubmit={(event) => {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError("");
    void login(username.trim(), password).then(onLogin).catch((reason: unknown) => {
      setError(reason instanceof AuthenticationError ? "账号或密码不正确，或账号已停用。" : reason instanceof Error ? reason.message : "登录失败，请稍后重试。");
    }).finally(() => { setBusy(false); });
  }}><Logo /><h1>{profile.name}</h1><p>{profile.welcome}</p>
    <p>请使用首次创建的账号和密码，软件没有预设的 admin 密码。</p>
    <label>账号<input required autoComplete="username" value={username} maxLength={128} onChange={(event) => setUsername(event.target.value)} /></label>
    <label>密码<input required type={showPassword ? "text" : "password"} autoComplete="current-password" value={password} maxLength={1024} onChange={(event) => setPassword(event.target.value)} /></label>
    <label className="login-show-password"><input type="checkbox" checked={showPassword} onChange={(event) => setShowPassword(event.target.checked)} />显示密码</label>
    {error && <p className="login-error" role="alert">{error}</p>}
    <button type="submit" disabled={busy}>{busy ? "登录中…" : "登录"}</button>
    <details className="login-recovery-help"><summary>升级后原账号无法登录？查看本机检查步骤</summary><p>先关闭软件，双击同目录的“检查账号和资料目录.cmd”，核对原资料目录和已有账号。这不会修改原密码。</p><p>仅在确需重设密码时使用“恢复账号.cmd”，会保留资料、Key和岗位权限。</p></details><small>{version && `桌面版 ${version}`}</small>
  </form></main>;
}

function App() {
  const [profile, setProfile] = useState<BusinessProfile | null>(null);
  const [principal, setPrincipal] = useState<Principal | null>(null);
  const [desktop, setDesktop] = useState<DesktopStatus | null>(null);
  const [setupToken] = useState(() => { const token = new URLSearchParams(window.location.hash.slice(1)).get("setup") ?? ""; return token; });
  useEffect(() => { if (setupToken) window.history.replaceState(null, "", window.location.pathname); }, [setupToken]);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState("");
  const channelRef = useRef<BroadcastChannel | null>(null);
  useEffect(() => {
    let alive = true;
    async function initialize() {
      try {
        const [next, desktopState] = await Promise.all([getProfile(), getDesktopStatus()]);
        if (alive) setDesktop(desktopState);
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
  if (desktop?.setup_required && !principal) return <DesktopSetup token={setupToken} status={desktop} onReady={async (user) => { const next = await getProfile(); setProfile(next); setPrincipal(user); setDesktop({ ...desktop, setup_required: false }); }} />;
  if (profile.mode === "jiahui" && !principal) return <LoginForm profile={profile} version={desktop?.version} onLogin={async (user) => {
    const next = await getProfile(); setProfile(next); setPrincipal(user); channelRef.current?.postMessage("signed-in");
  }} />;
  return <Workspace key={`${profile.mode}:${JSON.stringify(principal ?? "demo")}`} profile={profile} principal={principal} onLogout={() => void signOut()} desktop={desktop?.desktop ?? false} onUpdated={async () => setProfile(await getProfile())} />;
}

export default App;
