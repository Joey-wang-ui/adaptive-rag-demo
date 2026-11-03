import { useState } from "react";
import { ArrowRight, Check, KeyRound, Leaf, ShieldCheck } from "lucide-react";
import { setupDesktop } from "../lib/api";
import type { DesktopStatus, Principal } from "../types";

export function DesktopSetup({ token, status, onReady }: { token: string; status: DesktopStatus; onReady: (principal: Principal) => Promise<void> }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [remember, setRemember] = useState(status.secure_storage);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return <main className="onboarding-shell">
    <section className="onboarding-story"><div className="jiahui-wordmark"><span><Leaf size={24} /></span><b>嘉惠<span>JIAHUI WORKSPACE</span></b></div>
      <div className="story-content"><span className="eyebrow">专属于嘉惠的业务工作台</span><h1>让每一份资料，<br />成为可靠的回答。</h1><p>产品规格、原料知识、资料核实。<br />从纷杂的信息里，找到有依据的答案。</p><div className="story-facts"><span><Check size={15} /> 资料留在本机</span><span><Check size={15} /> 回答可追溯来源</span><span><Check size={15} /> 按岗位管理权限</span></div></div>
      <small>杭州嘉惠食品有限公司 · 内部业务助手</small><div className="story-orbit orbit-one" /><div className="story-orbit orbit-two" />
    </section>
    <section className="onboarding-form"><div className="onboarding-form-inner"><span className="eyebrow">首次使用 · 只需设置一次</span><h2>准备好，开始工作。</h2><p className="form-intro">创建你的管理员账号。以后打开软件，登录即可使用。</p>
      <form onSubmit={(event) => { event.preventDefault(); if (busy) return; if (password !== confirmation) { setError("两次密码不一致"); return; } if (!token) { setError("请关闭此窗口，从桌面程序重新打开以初始化。"); return; } setBusy(true); setError(""); void setupDesktop(token, username, password, apiKey, remember).then(onReady).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "初始化未完成")).finally(() => { setPassword(""); setConfirmation(""); setApiKey(""); setBusy(false); }); }}>
        <div className="field-row"><label>管理员账号<input required autoComplete="username" placeholder="设置你的账号" maxLength={64} value={username} onChange={(e) => setUsername(e.target.value)} /></label></div>
        <div className="field-row two-fields"><label>登录密码<input required type="password" minLength={12} maxLength={1024} autoComplete="new-password" placeholder="至少 12 位" value={password} onChange={(e) => setPassword(e.target.value)} /></label><label>确认密码<input required type="password" minLength={12} autoComplete="new-password" placeholder="再次输入" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} /></label></div>
        <div className="setup-model"><div className="setup-model-heading"><KeyRound size={17} /><strong>连接 DeepSeek</strong><span>可稍后设置</span></div><label className="visually-hidden" htmlFor="setup-key">DeepSeek API Key</label><input id="setup-key" type="password" autoComplete="off" maxLength={1024} placeholder="输入 DeepSeek API Key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />{status.secure_storage && <label className="checkbox-field"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />安全保存到本机 Windows 凭据管理器</label>}<small>暂不填写时可查询原文资料，界面会明确标注。真实模型调用需要联网及可用额度。</small></div>
        {error && <p className="login-error" role="alert">{error}</p>}
        <button className="primary-button" disabled={busy} type="submit">{busy ? "正在准备并检查模型…" : "进入嘉惠工作台"}<ArrowRight size={18} /></button>
        <p className="setup-privacy"><ShieldCheck size={14} />没有默认密码，Key 不会写入普通配置文件。</p>
      </form></div></section>
  </main>;
}
