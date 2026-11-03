import { useEffect, useRef, useState } from "react";
import { CheckCircle2, FileText, FolderOpen, KeyRound, UploadCloud, X } from "lucide-react";
import { getDesktopSettings, importDesktopFile, updateDesktopModel } from "../lib/api";
import type { DesktopSettings as Settings } from "../types";

export function DesktopSettings({ onClose, onUpdated }: { onClose: () => void; onUpdated: () => Promise<void> }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [tab, setTab] = useState<"data" | "model">("data");
  const [key, setKey] = useState("");
  const [remember, setRemember] = useState(false);
  const [current, setCurrent] = useState(false);
  const [version, setVersion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { closeRef.current?.focus(); void getDesktopSettings().then((value) => { setSettings(value); setRemember(value.secure_storage); }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取设置")); }, []);
  async function refresh() { await onUpdated(); setSettings(await getDesktopSettings()); }
  async function upload(file: File) {
    if (busy) return;
    setError(""); setNotice("");
    if (file.size > 15 * 1024 * 1024) { setError("单份资料不能超过 15MB"); return; }
    const pdf = file.name.toLowerCase().endsWith(".pdf");
    if (!pdf && !file.name.toLowerCase().endsWith(".csv")) { setError("请选择 UTF-8 CSV 或文字版 PDF"); return; }
    if (pdf && !version.trim()) { setError("请先填写 PDF 的来源版本；未知可填“来源版本待确认”。"); return; }
    setBusy(true);
    try {
      let content: string;
      if (pdf) {
        const bytes = new Uint8Array(await file.arrayBuffer()); let raw = "";
        for (let i = 0; i < bytes.length; i += 8192) raw += String.fromCharCode(...bytes.subarray(i, i + 8192));
        content = btoa(raw);
      } else content = await file.text();
      const result = await importDesktopFile(pdf ? "pdf" : "products", file.name, content, current ? "current" : "draft", version.trim() || "来源版本待确认");
      setNotice(pdf ? `已导入 ${result.pages} 页参考原文，现行有效性待确认。` : `已导入 ${result.imported} 条${current ? "现行资料" : "草稿"}，${current ? "现在可以查询。" : "复核后再以现行状态导入，即可查询。"}`);
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "导入未完成"); }
    finally { setBusy(false); }
  }
  return <div className="dialog-backdrop settings-backdrop" onKeyDown={(e) => { if (e.key === "Escape" && !busy) onClose(); }}><section className="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title"><header><div><span className="eyebrow">WORKSPACE SETTINGS</span><h2 id="settings-title">工作台设置</h2></div><button ref={closeRef} className="icon-button" aria-label="关闭设置" onClick={onClose} disabled={busy}><X size={20} /></button></header>
    <nav className="settings-tabs"><button className={tab === "data" ? "active" : ""} onClick={() => setTab("data")}><FolderOpen size={17} />资料管理</button><button className={tab === "model" ? "active" : ""} onClick={() => setTab("model")}><KeyRound size={17} />模型连接</button></nav>
    <div className="settings-body">{tab === "data" ? <><div className="data-stats"><div><span>可查询产品</span><strong>{settings?.current_products ?? "—"}<small>条</small></strong></div><div><span>待核实草稿</span><strong>{settings?.draft_products ?? "—"}<small>条</small></strong></div><div><span>导入与更新</span><strong className="stat-text">自动备份</strong></div></div>
      <div className="upload-area"><span className="upload-symbol"><UploadCloud size={28} /></span><h3>把资料交给嘉惠助手</h3><p>导入产品 CSV 或文字版 PDF，保留版本与来源位置。</p><label className="primary-button file-picker">{busy ? "正在解析并导入…" : "选择资料文件"}<input type="file" accept=".csv,.pdf" disabled={busy} onChange={(e) => { const file = e.target.files?.[0]; if (file) void upload(file); e.target.value = ""; }} /></label><a href="/api/v1/desktop/template" download><FileText size={14} />下载产品 CSV 模板</a></div>
      <label className="settings-field">PDF 来源版本<input value={version} onChange={(e) => setVersion(e.target.value)} placeholder="例如：2026年4月目录（有效性待确认）" maxLength={100} /></label><label className="checkbox-field"><input type="checkbox" checked={current} onChange={(e) => setCurrent(e.target.checked)} />已核实 CSV 中的资料，导入为现行产品目录</label><p className="settings-footnote">PDF 始终作为待确认的参考原文，不自动生成产品规格、食品属性或保质期。扫描件需先完成 OCR 核对。</p>
      {settings && <p className="data-location">本机资料目录 <code>{settings.data_dir}</code></p>}
    </> : <form onSubmit={(e) => { e.preventDefault(); if (busy) return; setBusy(true); setError(""); setNotice(""); void updateDesktopModel(key, remember).then(async (result) => { setNotice(result.capabilities.llm === "deepseek" ? "DeepSeek 已连接，真实生成检查通过。" : "Key 已设置，但真实生成尚未通过；请检查密钥、网络或账户额度。当前降级状态已更新。"); await refresh(); }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "连接未完成")).finally(() => { setKey(""); setBusy(false); }); }}><div className="model-provider-card"><span className="provider-monogram">D</span><div><strong>DeepSeek</strong><p>使用官方 API · deepseek-chat</p></div><span className={settings?.capabilities.llm === "deepseek" ? "provider-state verified" : "provider-state"}>{settings?.capabilities.llm === "deepseek" ? "已验证" : "待连接"}</span></div><label className="settings-field">DeepSeek API Key<input required type="password" autoComplete="off" value={key} maxLength={1024} placeholder={settings?.api_key_configured ? "已有配置；仅在更换 Key 时输入" : "输入你的 API Key"} onChange={(e) => setKey(e.target.value)} /></label>{settings?.secure_storage && <label className="checkbox-field"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />安全保存，下次启动自动复用</label>}<p className="settings-footnote">Key 仅用于连接 DeepSeek，不在界面回显。只有实际生成请求成功后，才会显示“已验证”。</p><button className="primary-button" disabled={busy}>{busy ? "正在检查真实生成…" : "保存并验证连接"}</button></form>}
      {error && <p className="login-error" role="alert">{error}</p>}{notice && <p className="settings-notice" role="status"><CheckCircle2 size={17} />{notice}</p>}
    </div></section></div>;
}
