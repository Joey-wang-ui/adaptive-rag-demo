import { useEffect, useState } from "react";
import { getSkillTemplates, previewSkillTemplates, restoreSkillTemplates, saveSkillTemplates } from "../lib/api";
import type { SkillColumn, SkillPreview, SkillSnapshot, SkillTemplates } from "../types";

const NAMES: Record<keyof SkillTemplates, string> = { product_lookup: "产品查询", knowledge_qa: "业务知识问答", general_chat: "通用对话", web_search: "联网搜索" };
const COLUMNS: Record<SkillColumn, string> = { name: "产品名称", code: "商品编号", brand: "品牌", spec: "规格", storage: "储存条件", shelf_life: "保质期", origin: "产地" };
export function SkillTemplatesEditor({ onDirty, onBusy }: { onDirty: (dirty: boolean) => void; onBusy: (busy: boolean) => void }) {
  const [saved, setSaved] = useState<SkillSnapshot | null>(null);
  const [draft, setDraft] = useState<SkillTemplates | null>(null);
  const [skill, setSkill] = useState<keyof SkillTemplates>("product_lookup");
  const [history, setHistory] = useState("");
  const [question, setQuestion] = useState("帮我查下蛋液有哪些");
  const [allowWeb, setAllowWeb] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [preview, setPreview] = useState<SkillPreview | null>(null);
  const dirty = !!draft && !!saved && JSON.stringify(draft) !== JSON.stringify(saved.templates);
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  useEffect(() => { onBusy(busy); return () => onBusy(false); }, [busy, onBusy]);
  function accept(value: SkillSnapshot) { setSaved(value); setDraft(value.templates); setHistory(""); setPreview(null); }
  useEffect(() => { let live = true; void getSkillTemplates().then((s) => { if (live) accept(s); }).catch((e: unknown) => { if (live) setError(e instanceof Error ? e.message : "读取失败"); }); return () => { live = false; }; }, []);
  async function action(work: () => Promise<void>) { if (busy) return; setBusy(true); setError(""); setNotice(""); try { await work(); } catch (e) { setError(e instanceof Error ? e.message : "操作未完成"); } finally { setBusy(false); } }
  if (!saved || !draft) return <p role="status">{error || "正在读取技能模板…"}</p>;
  function product<K extends keyof SkillTemplates["product_lookup"]>(key: K, value: SkillTemplates["product_lookup"][K]) { setDraft((d) => d ? { ...d, product_lookup: { ...d.product_lookup, [key]: value } } : d); setPreview(null); }
  return <div className="skill-editor"><p className="settings-footnote">保存后，下次提问立即读取新版本，无需重启。模板只调整表达和展示；权限、来源校验和业务写操作限制由服务端固定控制。请勿在模板中填写产品事实或密钥。</p>
    {saved.load_error && <p className="login-error" role="alert">{saved.load_error}</p>}
    <label className="settings-field">技能类型<select value={skill} disabled={busy} onChange={(e) => { setSkill(e.target.value as keyof SkillTemplates); setPreview(null); setAllowWeb(false); }}>{Object.entries(NAMES).map(([key, name]) => <option value={key} key={key}>{name}</option>)}</select></label>
    {skill === "product_lookup" ? <><label className="settings-field">列表开头文案<input value={draft.product_lookup.list_intro} maxLength={200} disabled={busy} onChange={(e) => product("list_intro", e.target.value)} /><small>仅支持 {"{count}"}，由程序填入实际匹配记录数。</small></label>
      <label className="settings-field">多商品或多规格的澄清提示<textarea rows={3} maxLength={300} disabled={busy} value={draft.product_lookup.clarification} onChange={(e) => product("clarification", e.target.value)} /></label>
      <label className="settings-field">列表最多显示条数<input type="number" min={1} max={20} disabled={busy} value={draft.product_lookup.max_results} onChange={(e) => product("max_results", Number(e.target.value))} /></label>
      <fieldset className="skill-columns"><legend>列表列项（按勾选顺序显示）</legend>{(Object.keys(COLUMNS) as SkillColumn[]).map((key) => <label className="checkbox-field" key={key}><input type="checkbox" disabled={busy || key === "name"} checked={draft.product_lookup.columns.includes(key)} onChange={(e) => product("columns", e.target.checked ? [...draft.product_lookup.columns, key] : draft.product_lookup.columns.filter((c) => c !== key))} />{COLUMNS[key]}</label>)}</fieldset>
      <label className="settings-field">商品详情布局<select value={draft.product_lookup.details_layout} disabled={busy} onChange={(e) => product("details_layout", e.target.value as "full" | "compact")}><option value="full">完整字段（缺失项明确标出）</option><option value="compact">简洁字段（只列已录入值）</option></select></label></> : <label className="settings-field">回答表达要求<textarea rows={8} value={draft[skill].instructions} disabled={busy} maxLength={4000} onChange={(e) => { setDraft({ ...draft, [skill]: { ...draft[skill], instructions: e.target.value } }); setPreview(null); }} /><small>{skill === "knowledge_qa" ? "调整要点和步骤顺序；业务事实仍须逐字依据校验，不会因模板改写而补造缺失字段。" : "调整语气、长短、步骤和格式；固定边界会附加在表达要求之后。"}</small></label>}
    {skill === "knowledge_qa" && <label className="settings-field">依据展示格式<select disabled={busy} value={draft.knowledge_qa.layout} onChange={(e) => { setDraft({ ...draft, knowledge_qa: { ...draft.knowledge_qa, layout: e.target.value as SkillTemplates["knowledge_qa"]["layout"] } }); setPreview(null); }}><option value="bullets">要点列表</option><option value="numbered">编号步骤</option><option value="paragraph">分段原文</option><option value="table">原文与来源表格</option></select></label>}
    <div className="skill-actions"><button className="primary-button" type="button" disabled={busy || (!dirty && !saved.load_error)} onClick={() => void action(async () => { accept(await saveSkillTemplates(draft, saved.revision)); setNotice("模板已保存，下次提问生效。"); })}>保存并生效</button><button type="button" disabled={busy} onClick={() => { if (!dirty || window.confirm("将放弃未保存的修改并重新加载，继续吗？")) void action(async () => accept(await getSkillTemplates())); }}>重新加载</button><button type="button" disabled={busy} onClick={() => { setDraft({ ...draft, [skill]: structuredClone(saved.defaults[skill]) }); setPreview(null); setNotice("已载入默认草稿，点击保存后才生效。"); }}>载入此技能默认草稿</button></div>
    <p className="settings-footnote">当前版本 {saved.revision.slice(0, 10)} · {saved.updated_at ? new Date(saved.updated_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "内置默认"} · {saved.updated_by}{dirty ? " · 有未保存修改" : ""}</p>
    <section className="skill-preview"><h3>预览与测试（不保存草稿）</h3><label className="settings-field">测试问题<textarea rows={2} maxLength={4000} value={question} disabled={busy} onChange={(e) => { setQuestion(e.target.value); setPreview(null); }} /></label>
      {skill === "web_search" && <label className="checkbox-field"><input type="checkbox" checked={allowWeb} disabled={busy} onChange={(e) => setAllowWeb(e.target.checked)} />允许本次测试联网，问题将发送给搜索服务</label>}
      <p className="settings-footnote">规则预览不调用模型；测试问答走实际路由、按当前账号权限检索，可能调用收费API，不写入正式会话或知识库。测试问题应与所选技能相符。</p>
      <div className="skill-actions"><button type="button" disabled={busy} onClick={() => void action(async () => setPreview(await previewSkillTemplates(draft, skill, "rules", question || "你好", false)))}>预览生效规则</button><button type="button" disabled={busy || !question.trim() || (skill === "web_search" && !allowWeb)} onClick={() => void action(async () => setPreview(await previewSkillTemplates(draft, skill, "answer", question, skill === "web_search" && allowWeb)))}>{busy ? "处理中…" : "测试问答"}</button></div>
      {preview && <div className="skill-preview-result" role="status"><strong>{preview.mode === "rules" ? "生效规则预览（未保存）" : "测试回答（未保存）"}</strong><pre>{preview.mode === "rules" ? typeof preview.rules === "string" ? preview.rules : JSON.stringify(preview.rules, null, 2) : preview.answer}</pre>{preview.citations?.map((c) => <small key={c.chunk_id}>{c.title} · {c.section}</small>)}</div>}
    </section>
    <section className="skill-preview"><h3>历史版本恢复</h3><select aria-label="历史模板版本" value={history} disabled={busy} onChange={(e) => setHistory(e.target.value)}><option value="">选择保存前的历史版本</option>{saved.history.map((h) => <option key={h.revision} value={h.revision} disabled={!h.restorable}>{h.updated_at ? new Date(h.updated_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hour12: false }) : "内置默认 / 原文件"} · {h.updated_by} · {h.revision.slice(0, 8)}{!h.restorable ? "（不可恢复）" : ""}</option>)}</select><button type="button" disabled={busy || !history} onClick={() => { if (window.confirm("恢复所选版本并立即生效？当前已保存版本会先备份，未保存草稿将放弃。")) void action(async () => { accept(await restoreSkillTemplates(history, saved.revision)); setNotice("历史版本已恢复，下次提问生效。"); }); }}>恢复所选版本</button></section>
    {error && <p className="login-error" role="alert">{error}</p>}{notice && <p className="settings-notice" role="status">{notice}</p>}
  </div>;
}
