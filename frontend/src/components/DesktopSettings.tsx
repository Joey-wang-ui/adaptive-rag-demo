import { useEffect, useRef, useState } from "react";
import { CheckCircle2, Copy, FileText, FolderOpen, KeyRound, UploadCloud, X } from "lucide-react";
import { ApiRequestError, downloadDesktopTemplate, getDesktopDocuments, getDesktopSettings, importDesktopFile, repairDesktopCatalog, updateDesktopModel } from "../lib/api";
import type { DesktopSettings as Settings, ImportResult, KnowledgeDocument } from "../types";

export function importSummary(pdf: boolean, result: ImportResult, current: boolean): string {
  if (!pdf) return `已导入 ${result.imported ?? 0} 条${current ? "现行产品资料，现在可以查询。" : "产品草稿；草稿尚不参与普通产品查询，复核后再勾选“现行产品目录”重新导入。"}`;
  const pages = result.imported_pages ?? result.pages ?? 0;
  const skipped = result.skipped_pages?.length ?? 0;
  if (pages === 0) return "未导入可检索的文字页，请查看导入诊断；扫描件需先完成 OCR 核对。";
  return `${result.catalog_imported ? `已接入参考产品目录 ${result.catalog_imported} 条、原文 ${pages} 页，已可查询并保存，下次打开可继续使用。` : `知识库已接入 ${pages} 页，可以直接提问；已保存，下次打开可继续使用。`}参考资料现行有效性待确认。${skipped ? `另有 ${skipped} 页未导入，请查看下方诊断。` : ""}`;
}

type Diagnostic = { filename: string; kind: "pdf" | "products"; bytes: number; status: string; result?: ImportResult; error?: string; http_status?: number; trace_id?: string | null };

export function DesktopSettings({ onClose, onUpdated }: { onClose: () => void; onUpdated: () => Promise<void> }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [tab, setTab] = useState<"data" | "model">("data");
  const [key, setKey] = useState("");
  const [remember, setRemember] = useState(false);
  const [current, setCurrent] = useState(false);
  const [version, setVersion] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [diagnostic, setDiagnostic] = useState<Diagnostic | null>(null);
  const [copied, setCopied] = useState(false);
  const closeRef = useRef<HTMLButtonElement>(null);

  async function readDocuments() {
    try { setDocuments((await getDesktopDocuments()).documents); }
    catch (reason) { if (!(reason instanceof ApiRequestError && reason.status === 404)) throw reason; }
  }
  useEffect(() => {
    closeRef.current?.focus();
    void getDesktopSettings().then(async (value) => { setSettings(value); setRemember(value.secure_storage); await readDocuments(); }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "无法读取设置"));
  }, []);
  async function refresh() { await onUpdated(); setSettings(await getDesktopSettings()); await readDocuments(); }
  async function upload(file: File) {
    if (busy) return;
    setError(""); setNotice(""); setCopied(false);
    const pdf = file.name.toLowerCase().endsWith(".pdf");
    const base: Diagnostic = { filename: file.name, kind: pdf ? "pdf" : "products", bytes: file.size, status: "读取文件" };
    setDiagnostic(base);
    if (file.size > 15 * 1024 * 1024) { setError("单份资料不能超过 15MB"); setDiagnostic({ ...base, status: "未导入", error: "文件超过15MB" }); return; }
    if (!pdf && !file.name.toLowerCase().endsWith(".csv")) { setError("请选择 UTF-8 CSV 或文字版 PDF"); setDiagnostic({ ...base, status: "未导入", error: "仅支持CSV/PDF" }); return; }
    setBusy(true);
    try {
      let content: string;
      if (pdf) {
        const bytes = new Uint8Array(await file.arrayBuffer()); let raw = "";
        for (let i = 0; i < bytes.length; i += 8192) raw += String.fromCharCode(...bytes.subarray(i, i + 8192));
        content = btoa(raw);
      } else content = await file.text();
      setDiagnostic({ ...base, status: "正在上传并解析" });
      const result = await importDesktopFile(pdf ? "pdf" : "products", file.name, content, pdf ? "draft" : current ? "current" : "draft", version.trim() || "来源版本待确认");
      const summary = importSummary(pdf, result, current);
      setDiagnostic({ ...base, status: "导入完成", result });
      if (pdf && (result.imported_pages ?? result.pages ?? 0) === 0) setError(summary);
      else setNotice(summary);
      try { await refresh(); }
      catch { setError("资料已保存，但状态刷新未完成；请重新打开设置查看，重复导入不会新增相同记录。"); }
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "导入未完成";
      setError(message);
      setDiagnostic({ ...base, status: "导入失败", error: message, ...(reason instanceof ApiRequestError ? { http_status: reason.status, trace_id: reason.traceId } : {}) });
    } finally { setBusy(false); }
  }
  async function copyDiagnostic() {
    if (!diagnostic) return;
    const text = JSON.stringify(diagnostic, null, 2);
    try {
      if (navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else {
        const field = document.createElement("textarea"); field.value = text; document.body.append(field); field.select();
        const success = document.execCommand("copy"); field.remove(); if (!success) throw new Error("copy unavailable");
      }
      setCopied(true);
    } catch { setError("自动复制不可用，请展开下方诊断后选中文本复制。"); }
  }
  const knowledgeCount = settings?.knowledge_document_count ?? documents.length;
  const knowledgeFiles = settings?.knowledge_file_count ?? new Set(documents.map((item) => item.source_file)).size;
  return <div className="dialog-backdrop settings-backdrop" onKeyDown={(e) => { if (e.key === "Escape" && !busy) onClose(); }}><section className="settings-dialog" role="dialog" aria-modal="true" aria-labelledby="settings-title"><header><div><span className="eyebrow">WORKSPACE SETTINGS</span><h2 id="settings-title">工作台设置</h2></div><button ref={closeRef} className="icon-button" aria-label="关闭设置" onClick={onClose} disabled={busy}><X size={20} /></button></header>
    <nav className="settings-tabs"><button className={tab === "data" ? "active" : ""} onClick={() => setTab("data")}><FolderOpen size={17} />资料管理</button><button className={tab === "model" ? "active" : ""} onClick={() => setTab("model")}><KeyRound size={17} />模型连接</button></nav>
    <div className="settings-body">{tab === "data" ? <><div className="data-stats"><div><span>已接入知识资料</span><strong>{settings ? knowledgeFiles : "—"}<small>份</small></strong><span>{knowledgeCount} 页/章节可查询</span></div><div><span>可查询产品目录</span><strong>{settings ? (settings.product_count ?? settings.current_products + (settings.reference_products ?? 0)) : "—"}<small>条</small></strong><span>现行 {settings?.current_products ?? 0} · 参考 {settings?.reference_products ?? 0}</span></div><div><span>产品草稿</span><strong>{settings?.draft_products ?? "—"}<small>条</small></strong></div></div>
      <p className="settings-footnote knowledge-explanation">PDF 导入后，标准表格会形成带来源的参考产品目录，原文按页进入知识库，可直接提问；参考资料保留待确认标记。CSV 草稿需复核为现行版本后参与产品查询。</p>
      <div className="upload-area"><span className="upload-symbol"><UploadCloud size={28} /></span><h3>把资料交给嘉惠助手</h3><p>选择 PDF 即可导入知识库；选择 CSV 可导入结构化产品目录。</p><label className="primary-button file-picker">{busy ? "正在解析并导入…" : "选择资料文件"}<input type="file" accept=".csv,.pdf" disabled={busy} onChange={(e) => { const file = e.target.files?.[0]; if (file) void upload(file); e.target.value = ""; }} /></label><button className="template-download" type="button" onClick={() => void downloadDesktopTemplate().catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "下载失败"))}><FileText size={14} />下载产品 CSV 模板</button></div>
      <button type="button" className="template-download" disabled={busy} onClick={() => { setBusy(true); setError(""); setNotice(""); void repairDesktopCatalog().then(async (result) => { setNotice(`已检查本机保存的原件，可查询产品目录 ${result.product_count} 条（参考 ${result.reference_products} 条）。${result.warnings.length ? "部分原件需核对，请查看资料列表及导入诊断。" : "重启后可以继续复用。"}`); await refresh(); }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "目录检查未完成")).finally(() => setBusy(false)); }}>检查并修复已导入目录</button>
      <label className="settings-field">PDF 来源版本（选填）<input value={version} onChange={(e) => setVersion(e.target.value)} placeholder="未填写时使用“来源版本待确认”，不影响导入" maxLength={100} /></label><label className="checkbox-field"><input type="checkbox" checked={current} onChange={(e) => setCurrent(e.target.checked)} />已核实 CSV 中的资料，导入为现行产品目录（不影响 PDF 导入）</label><p className="settings-footnote">PDF 保留原文、页码和来源版本；标准表格按原文提取为参考目录，未知字段留空，不补写食品属性或保质期。无法提取文字的页面会单独说明。每次更新前自动备份。</p>
      {documents.length > 0 && <details className="knowledge-documents"><summary>查看已导入的知识资料（{knowledgeFiles} 份，{knowledgeCount} 页/章节）</summary><ul>{documents.map((item) => <li key={`${item.id}:${item.version}`}><strong>{item.source_file || item.title}</strong><span>{item.location || item.title} · {item.version || "来源版本待确认"} · {item.status === "reference" ? "参考资料，现行有效性待确认" : item.status === "current" ? "现行资料" : item.status === "draft" ? "草稿" : "历史资料"}</span></li>)}</ul></details>}
      {settings && <p className="data-location">本机资料目录 <code>{settings.data_dir}</code></p>}
    </> : <form onSubmit={(e) => { e.preventDefault(); if (busy) return; setBusy(true); setError(""); setNotice(""); void updateDesktopModel(key, remember).then(async (result) => { setNotice(result.capabilities.llm === "deepseek" ? "DeepSeek 已连接，真实生成检查通过。" : "Key 已设置，但真实生成尚未通过；请检查密钥、网络或账户额度。当前降级状态已更新。"); await refresh(); }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "连接未完成")).finally(() => { setKey(""); setBusy(false); }); }}><div className="model-provider-card"><span className="provider-monogram">D</span><div><strong>DeepSeek</strong><p>使用官方 API · deepseek-chat</p></div><span className={settings?.capabilities.llm === "deepseek" ? "provider-state verified" : "provider-state"}>{settings?.capabilities.llm === "deepseek" ? "已验证" : "待连接"}</span></div><label className="settings-field">DeepSeek API Key<input required type="password" autoComplete="off" value={key} maxLength={1024} placeholder={settings?.api_key_configured ? "已有配置；仅在更换 Key 时输入" : "输入你的 API Key"} onChange={(e) => setKey(e.target.value)} /></label>{settings?.secure_storage && <label className="checkbox-field"><input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />安全保存，下次启动自动复用</label>}<p className="settings-footnote">Key 仅用于连接 DeepSeek，不在界面回显。只有实际生成请求成功后，才会显示“已验证”。</p><button className="primary-button" disabled={busy}>{busy ? "正在检查真实生成…" : "保存并验证连接"}</button></form>}
      {error && <p className="login-error" role="alert">{error}</p>}{notice && <p className="settings-notice" role="status"><CheckCircle2 size={17} />{notice}</p>}
      {tab === "data" && diagnostic && <details className="import-diagnostic" open={diagnostic.status === "导入失败"}><summary>最近导入：{diagnostic.filename} · {diagnostic.status}</summary>{diagnostic.result?.skipped_pages && diagnostic.result.skipped_pages.length > 0 && <ul>{diagnostic.result.skipped_pages.map((item) => <li key={item.page}>第 {item.page} 页：{item.reason}</li>)}</ul>}<pre>{JSON.stringify(diagnostic, null, 2)}</pre><button type="button" onClick={() => void copyDiagnostic()}><Copy size={14} />{copied ? "已复制诊断" : "复制导入诊断"}</button><small>诊断不包含文件正文、账号密码或 API Key。</small></details>}
    </div></section></div>;
}
