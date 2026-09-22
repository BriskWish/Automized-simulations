import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useAui,
  useAuiState,
  useLocalRuntime,
} from "@assistant-ui/react";
import {
  Activity,
  Atom,
  BookOpen,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CircleHelp,
  CircleCheck,
  ClipboardList,
  FileCog,
  FolderKanban,
  Gauge,
  Info,
  LayoutDashboard,
  Menu,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Play,
  Plus,
  RefreshCw,
  Send,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  ScrollText,
  Trash2,
  Upload,
  Workflow,
  X,
} from "lucide-react";
import {
  ABOUT_WILLY,
  BEGINNER_GUIDE_SECTIONS,
  LEGACY_CONFIGURATION,
} from "./legacy-content";
import "./styles.css";

const NAV_ITEMS = [
  { id: "tasks", label: "本地任务", icon: FolderKanban },
  { id: "config", label: "配置", icon: Settings2 },
  { id: "guide", label: "新手指南", icon: BookOpen },
  { id: "about", label: "关于", icon: Info },
];

const ASSISTANT_TABS = [
  { id: "proposal", label: "方案助理", icon: Sparkles },
  { id: "run", label: "运行助理", icon: Activity },
  { id: "visual", label: "可视化", icon: Atom },
  { id: "logs", label: "日志", icon: ScrollText },
];

const PROPOSAL_PLACEHOLDER = "输入模拟体系、项目简介、分子库查询";
const RUN_PLACEHOLDER = "输入进度查询、/指令等";
const SPHERE_SCALE_OPTIONS = [
  { value: "0.25", label: "小" },
  { value: "0.35", label: "标准" },
  { value: "0.45", label: "大" },
  { value: "0.55", label: "特大" },
];
const STICK_RADIUS_OPTIONS = [
  { value: "0.14", label: "细" },
  { value: "0.22", label: "标准" },
  { value: "0.30", label: "粗" },
  { value: "0.38", label: "特粗" },
];
const BACKGROUND_OPTIONS = [
  { value: "beige", label: "米色" },
  { value: "white", label: "白色" },
  { value: "silver", label: "银色" },
];
const PANEL_WIDTH_LIMITS = {
  drawer: { min: 240, max: 460, initial: 300 },
  inspector: { min: 240, max: 440, initial: 280 },
};
const MIN_ASSISTANT_WIDTH = 470;

function clampPanelWidth(value, { min, max }) {
  return Math.min(max, Math.max(min, value));
}

function readResponseError(response) {
  return response.json().then((data) => data.detail || data.message || "请求失败").catch(() => "请求失败");
}

function assistantMessageText(message) {
  if (!message || typeof message !== "object") return "";
  if (typeof message.content === "string") return message.content;
  if (!Array.isArray(message.content)) return "";
  return message.content
    .filter((part) => part && part.type === "text" && typeof part.text === "string")
    .map((part) => part.text)
    .join("\n");
}

function createChatAdapter(kind, runId, proposalId, onPipelineStarted, onProposalWorkspaceChanged) {
  return {
    async run({ messages, abortSignal, unstable_threadId }) {
      if (kind === "run" && !runId) {
        return { content: [{ type: "text", text: "当前没有可读取的运行。请先在方案助理中确认并启动流水线。" }] };
      }
      if (kind === "proposal" && !proposalId) {
        return { content: [{ type: "text", text: "方案工作区正在准备，请稍后重试。" }] };
      }
      const endpoint = kind === "proposal"
        ? "/api/proposal/chat"
        : `/api/runs/${encodeURIComponent(runId)}/chat`;
      const response = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(kind === "proposal"
          ? { messages, proposalId, threadId: unstable_threadId }
          : { message: assistantMessageText([...messages].reverse().find((item) => item.role === "user")) }),
        signal: abortSignal,
      });
      if (!response.ok) throw new Error(await readResponseError(response));
      const data = await response.json();
      if (abortSignal.aborted) return { content: [] };
      const launchedRunId = data.run_id || data.runId || data.snapshot?.run_id;
      if (typeof launchedRunId === "string" && (kind === "proposal" || launchedRunId !== runId)) {
        onPipelineStarted(launchedRunId);
      } else if (kind === "proposal" && typeof data.proposalId === "string" && data.proposalId !== proposalId) {
        onProposalWorkspaceChanged(data.proposalId);
      }
      return { content: [{ type: "text", text: data.text || "暂时没有生成新的回复。" }] };
    },
  };
}

function parseEqSegments(text) {
  const source = typeof text === "string" ? text : "";
  if (!/(?:EQ|平衡).*(?:六段|6\s*段)|(?:六段|6\s*段).*(?:EQ|平衡)/i.test(source)) return [];
  return source.split(/\r?\n/).map((line) => {
    const numbered = line.match(/^\s*(\d+)[.)、]\s*(.+)$/);
    if (!numbered) return null;
    const fields = numbered[2].split(/[|｜]/).map((field) => field.trim()).filter(Boolean);
    if (fields.length < 4 || !/(?:ns|ps|fs|step)/i.test(fields[2]) || !/(?:K|°C|℃)\s*(?:->|→|至)\s*/i.test(fields[3])) return null;
    return {
      number: Number(numbered[1]),
      stage: fields[0],
      ensemble: fields[1],
      duration: fields[2],
      temperature: fields[3].replace(/\s*(?:->|至)\s*/g, "→"),
    };
  }).filter(Boolean).slice(0, 6);
}

function parseProposalPlan(text) {
  const source = typeof text === "string" ? text : "";
  const marker = source.match(/^\[\[WILLY_PLAN_DATA:(.+)\]\]$/m);
  if (!marker) return null;
  try {
    const payload = JSON.parse(marker[1]);
    if (!payload || payload.version !== 1 || !Array.isArray(payload.structure) || !payload.environment || !Array.isArray(payload.md_steps)) return null;
    const structure = payload.structure.map((item) => {
      const name = typeof item?.name === "string" ? item.name.trim() : "";
      const count = item?.count;
      const optimizationLevel = typeof item?.optimization_level === "string" ? item.optimization_level.trim() : "";
      const solvent = typeof item?.solvent === "string" ? item.solvent.trim() : "gas";
      const solventSource = typeof item?.solvent_source === "string" ? item.solvent_source.trim() : "unknown";
      const scrfOverride = typeof item?.scrf_override === "string" ? item.scrf_override.trim() : "";
      const solventNote = typeof item?.solvent_note === "string" ? item.solvent_note.trim() : "";
      return name && Number.isInteger(count) && count > 0 && optimizationLevel ? { name, count, optimizationLevel, solvent, solventSource, scrfOverride, solventNote } : null;
    }).filter(Boolean);
    const chargeScale = typeof payload.environment.charge_scale === "string" ? payload.environment.charge_scale.trim() : "";
    const initialDensity = typeof payload.environment.initial_density === "string" ? payload.environment.initial_density.trim() : "";
    const totalMolecules = payload.environment.total_molecules;
    const mdSteps = payload.md_steps.map((item) => {
      const label = typeof item?.label === "string" ? item.label.trim() : "";
      const meta = typeof item?.meta === "string" ? item.meta.trim() : "";
      return label && meta ? { label, meta } : null;
    }).filter(Boolean);
    if (!structure.length || !chargeScale || !initialDensity || !Number.isInteger(totalMolecules) || totalMolecules < 1 || mdSteps.length !== 8) return null;
    return { structure, environment: { chargeScale, initialDensity, totalMolecules }, mdSteps };
  } catch {
    return null;
  }
}

function EqProtocolOverview({ text }) {
  const segments = parseEqSegments(text);
  if (segments.length !== 6) return null;
  return <section className="eq-protocol" aria-label="EQ 六段过程">
    <div className="eq-protocol-heading"><Workflow size={15} /><strong>EQ 六段过程</strong><span>按执行顺序</span></div>
    <ol className="eq-segment-list">
      {segments.map((segment) => <li className="eq-segment" key={`${segment.number}-${segment.stage}`}>
        <span className="eq-segment-number">{segment.number}</span>
        <div className="eq-segment-main"><strong>{segment.stage}</strong><span className="eq-segment-meta">{segment.ensemble} · {segment.duration} · {segment.temperature}</span></div>
      </li>)}
    </ol>
  </section>;
}

function PlanCard({ number, title, detail, rows }) {
  return <section className="proposal-plan-card" aria-label={title}>
    <div className="proposal-plan-heading"><span className="proposal-plan-index">{number}</span><Workflow size={15} /><strong>{title}</strong><span>{detail}</span></div>
    <ol className="proposal-plan-list">
      {rows.map((row, index) => <li className="proposal-plan-row" key={`${number}-${row.label}-${index}`}>
        <span className="proposal-plan-row-number">{index + 1}</span>
        <div className="proposal-plan-row-main"><strong>{row.label}</strong><span>{row.meta}</span></div>
      </li>)}
    </ol>
  </section>;
}

function ProposalPlanOverview({ plan }) {
  const environmentRows = [
    { label: "电荷缩放因子", meta: plan.environment.chargeScale },
    { label: "盒子初始密度", meta: plan.environment.initialDensity },
    { label: "合计分子数", meta: `${plan.environment.totalMolecules} 个` },
  ];
  return <div className="proposal-plan" aria-label="模拟方案">
    <PlanCard number="01" title="结构生成过程" detail="优化阶段" rows={plan.structure.map((item) => ({ label: item.name, meta: `${item.count} 个 · ${item.optimizationLevel} · SMD: ${item.solvent}（${item.solventSource}） · ${item.scrfOverride}${item.solventNote ? ` · ${item.solventNote}` : ""}` }))} />
    <PlanCard number="02" title="环境生成" detail="建盒阶段" rows={environmentRows} />
    <PlanCard number="03" title="MD 模拟流程" detail="EM → EQ → PROD" rows={plan.mdSteps} />
  </div>;
}

function ProposalConfirmation() {
  return <div className="proposal-confirmation">
    <strong>模拟方案确认</strong>
    <span>下一步将开始结构优化。</span>
    <span>确认无误后回复“运行”即可开始。</span>
    <span>若参数有误请提出，我会更新方案。</span>
  </div>;
}

function AssistantMessage({ showEq = false }) {
  const eventKind = useAuiState((state) => state.message.metadata?.custom?.runActivityKind);
  const eventActive = useAuiState((state) => state.message.metadata?.custom?.runActivityActive === true);
  const messageText = useAuiState((state) => (state.message.content || [])
    .filter((part) => part?.type === "text")
    .map((part) => part.text || "")
    .join("\n"));
  const plan = parseProposalPlan(messageText);
  const runningStep = eventKind === "status" && eventActive;
  const completedStep = eventKind === "step_completed" || eventKind === "completion_artifacts" || (eventKind === "status" && !eventActive && /已完成|全流程完成/.test(messageText));
  return <MessagePrimitive.Root className={`message assistant-message ${runningStep ? "running-step" : ""} ${completedStep ? "completed-step" : ""} ${plan ? "proposal-plan-message" : ""}`} data-role="assistant">
    <div className="message-mark">{runningStep ? <span className="step-spinner" aria-label="当前步骤进行中" /> : completedStep ? <CircleCheck size={16} aria-label="步骤已完成" /> : <Sparkles size={15} />}</div>
    <div className="message-copy">{plan ? <><ProposalPlanOverview plan={plan} /><ProposalConfirmation /></> : <>{showEq && <EqProtocolOverview text={messageText} />}<MessagePrimitive.Parts /></>}</div>
  </MessagePrimitive.Root>;
}

function ProposalAssistantMessage() {
  return <AssistantMessage showEq />;
}

function UserMessage() {
  return <MessagePrimitive.Root className="message user-message" data-role="user">
    <div className="message-copy"><MessagePrimitive.Parts /></div>
    <div className="avatar message-user-avatar" aria-label="用户 ZL">ZL</div>
  </MessagePrimitive.Root>;
}

function ThreadWelcome({ kind }) {
  const isEmpty = useAuiState((state) => state.thread.messages.length === 0);
  if (!isEmpty) return null;
  const run = kind === "run";
  return <div className={`thread-welcome ${run ? "run-welcome" : "proposal-welcome"}`}>
    <div className="welcome-mark">{run ? <Activity size={23} /> : <Atom size={24} />}</div>
    <div>
      <span className="eyebrow">WILLY · {run ? "RUN AGENT" : "PROPOSAL AGENT"}</span>
      <h3>{run ? "当前工程的运行状态会在这里更新。" : "今天要建立怎样的模拟体系？"}</h3>
      <p>{run ? "每个完成步骤会作为新的运行消息出现。" : "从分子名称、数量或项目目标开始。"}</p>
    </div>
  </div>;
}

function ProposalThinkingIndicator() {
  const isRunning = useAuiState((state) => state.thread.isRunning);
  if (!isRunning) return null;
  return <div className="proposal-thinking" role="status" aria-live="polite"><span className="step-spinner" aria-hidden="true" />正在思考...</div>;
}

function SlashMenu({ inputId, onClose }) {
  const applyCommand = (command) => {
    const input = document.getElementById(inputId);
    if (input instanceof HTMLTextAreaElement) {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
      setter?.call(input, `${command} `);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    }
    onClose();
  };
  return <div className="slash-menu" role="listbox" aria-label="运行控制命令">
    <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => applyCommand("/fork")}><code>/fork</code><span>以修改参数创建分支</span></button>
    <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => applyCommand("/inputs")}><code>/inputs</code><span>查询或声明新的工程输入</span></button>
    <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => applyCommand("/resume")}><code>/resume</code><span>从安全步骤继续</span></button>
    <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => applyCommand("/switch")}><code>/switch</code><span>切换工程对话</span></button>
  </div>;
}

function MoleculeRecord({ record }) {
  const formats = Array.isArray(record.input_suffixes) ? record.input_suffixes.map((suffix) => String(suffix).replace(/^\./, "").toUpperCase()).join(" · ") : "原始输入";
  return <div className="molecule-record"><strong>{record.name}</strong><span>{formats}</span></div>;
}

function MoleculeCatalogDialog({ proposalId, onResult, onFailure, onClose }) {
  const fileInput = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [catalog, setCatalog] = useState(null);
  const [loading, setLoading] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const mounted = useRef(false);
  const catalogRequest = useRef(null);
  const contentRef = useRef(null);
  const loadCatalog = useCallback(async () => {
    catalogRequest.current?.abort();
    const controller = new AbortController();
    catalogRequest.current = controller;
    const isCurrent = () => mounted.current && catalogRequest.current === controller && !controller.signal.aborted;
    setLoading(true);
    setCatalogError("");
    try {
      const response = await fetch("/api/molecules", { signal: controller.signal });
      if (!response.ok) throw new Error(await readResponseError(response));
      const payload = await response.json();
      if (!isCurrent()) return;
      const isRecord = (record) => record && typeof record.name === "string" && record.name.trim()
        && Array.isArray(record.input_suffixes) && record.input_suffixes.every((suffix) => typeof suffix === "string" && suffix.startsWith("."));
      if (!Array.isArray(payload?.molecules) || payload.molecules.some((record) => !isRecord(record))) throw new Error("分子库结果格式无效，请重试。");
      setCatalog(payload.molecules);
    } catch (error) {
      if (isCurrent()) setCatalogError(error instanceof Error ? error.message : "分子库暂时无法读取。");
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    loadCatalog();
    return () => {
      mounted.current = false;
      catalogRequest.current?.abort();
    };
  }, [loadCatalog]);
  useEffect(() => {
    const previousFocus = document.activeElement;
    const dialog = contentRef.current?.closest('[role="dialog"]');
    contentRef.current?.querySelector("button")?.focus();
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!uploading) onClose();
      }
      if (event.key !== "Tab" || !dialog) return;
      const controls = [...dialog.querySelectorAll('button:not(:disabled), input:not(:disabled), [tabindex="0"]')];
      const first = controls[0];
      const last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [onClose, uploading]);
  const upload = async (event) => {
    const file = event.currentTarget.files?.[0];
    event.currentTarget.value = "";
    if (!file) return;
    setUploading(true);
    try {
      const contentBase64 = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(",", 2)[1] || "");
        reader.onerror = () => reject(new Error("无法读取上传文件"));
        reader.readAsDataURL(file);
      });
      const response = await fetch("/api/proposal/upload", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proposalId, filename: file.name, content_base64: contentBase64 }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "上传失败");
      onClose();
      onResult(payload);
    } catch (error) {
      onFailure(error instanceof Error ? error.message : "上传失败");
    } finally {
      setUploading(false);
    }
  };
  return <Dialog label="上传分子结构 / 分子库" className="molecule-dialog" closeDisabled={uploading} onClose={() => { if (!uploading) onClose(); }}>
    <div ref={contentRef}>
      <span className="eyebrow">MOLECULE · LIBRARY</span><h2>上传分子结构 / 分子库</h2>
      <p className="molecule-boundary">此处展示当前可用于方案的原始量子输入。上传后会先核验文件格式与内容，再加入分子库。</p>
      <section className="molecule-results" aria-label="当前分子库" aria-busy={loading}>
        {catalogError && <p className="molecule-error" role="alert">{catalogError}</p>}
        {loading && <p role="status">正在读取分子库…</p>}
        {catalog && <><p role="status">分子库 · {catalog.length} 项</p>
          {catalog.length ? <ul className="molecule-record-list">{catalog.map((record) => <li key={record.name}><MoleculeRecord record={record} /></li>)}</ul> : <p>当前分子库为空，可上传原始量子输入。</p>}</>}
      </section>
      <input ref={fileInput} className="visually-hidden" type="file" accept=".gjf,.inp" onChange={upload} />
      <button type="button" className="molecule-upload-button" disabled={uploading} onClick={() => fileInput.current?.click()}><Upload className={uploading ? "spin" : ""} size={15} /><span>{uploading ? "上传中…" : "选择并上传分子结构"}</span></button>
    </div>
  </Dialog>;
}

function solventRegistrationPayload({ name, epsilon, epsinf }) {
  const cleanName = name.trim();
  if (cleanName.length > 96 || /[\r\n\t]/.test(cleanName)) throw new Error("名称不能包含控制字符且长度不得超过 96。");
  if (cleanName.toLowerCase() === "gas") throw new Error("gas 是保留值，不能登记为自定义溶剂。");
  const values = { epsilon: epsilon.trim(), epsinf: epsinf.trim() };
  for (const [field, value] of Object.entries(values)) {
    if (!/^\+?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value) || !Number.isFinite(Number(value)) || Number(value) < 1) {
      throw new Error(`${field} 必须是大于等于 1 的有限数值。`);
    }
  }
  if (Number(values.epsilon) < Number(values.epsinf)) throw new Error("必须满足 epsilon >= epsinf >= 1。");
  return { name: cleanName || null, ...values };
}

function SolventRecord({ record }) {
  const displayValue = (value) => typeof value === "string" || typeof value === "number" ? String(value) : "未提供";
  return <div className="solvent-record">
    <div><strong>{record.name}</strong><span className="solvent-source">{{ builtin: "Gaussian 内置", manual: "自定义" }[record.source] || "来源未标注"}</span></div>
    <dl><div><dt>epsilon</dt><dd>{displayValue(record.epsilon)}</dd></div><div><dt>epsinf</dt><dd>{displayValue(record.epsinf)}</dd></div></dl>
  </div>;
}

function SolventCatalogDialog({ onClose }) {
  const [query, setQuery] = useState("");
  const [catalog, setCatalog] = useState(null);
  const [searching, setSearching] = useState(false);
  const [queryError, setQueryError] = useState("");
  const [fields, setFields] = useState({ name: "", epsilon: "", epsinf: "" });
  const [saving, setSaving] = useState(false);
  const [registered, setRegistered] = useState(null);
  const [registrationError, setRegistrationError] = useState("");
  const mounted = useRef(false);
  const queryRequest = useRef(null);
  const registrationRequest = useRef(null);
  const contentRef = useRef(null);
  const loadCatalog = useCallback(async (value = "") => {
    queryRequest.current?.abort();
    const controller = new AbortController();
    queryRequest.current = controller;
    const isCurrent = () => mounted.current && queryRequest.current === controller && !controller.signal.aborted;
    setSearching(true);
    setCatalog(null);
    setQueryError("");
    try {
      const normalized = value.trim();
      const response = await fetch(`/api/solvents${normalized ? `?query=${encodeURIComponent(normalized)}` : ""}`, { signal: controller.signal });
      if (!response.ok) throw new Error(await readResponseError(response));
      const payload = await response.json();
      if (!isCurrent()) return;
      const isRecord = (record) => record && typeof record.name === "string" && record.name.trim();
      if (!Array.isArray(payload?.candidates) || payload.candidates.some((record) => !isRecord(record))
        || (payload.match != null && !isRecord(payload.match))) throw new Error("溶剂查询结果格式无效，请重试。");
      const records = payload.match ? [payload.match, ...payload.candidates.filter((record) => record.name !== payload.match.name)] : payload.candidates;
      setCatalog({ records, exact: Boolean(payload.match), query: normalized });
    } catch (error) {
      if (isCurrent()) setQueryError(error instanceof Error ? error.message : "溶剂库暂时无法读取。");
    } finally {
      if (isCurrent()) setSearching(false);
    }
  }, []);
  useEffect(() => {
    mounted.current = true;
    loadCatalog();
    return () => {
      mounted.current = false;
      queryRequest.current?.abort();
      registrationRequest.current?.abort();
    };
  }, [loadCatalog]);
  useEffect(() => {
    const previousFocus = document.activeElement;
    const dialog = contentRef.current?.closest('[role="dialog"]');
    contentRef.current?.querySelector("input")?.focus();
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!registrationRequest.current) onClose();
      }
      if (event.key !== "Tab" || !dialog) return;
      const controls = [...dialog.querySelectorAll('button:not(:disabled), input:not(:disabled), [tabindex="0"]')];
      const first = controls[0];
      const last = controls.at(-1);
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [onClose]);
  const register = async (event) => {
    event.preventDefault();
    if (!mounted.current || registrationRequest.current) return;
    setRegistrationError("");
    setRegistered(null);
    let body;
    try {
      body = solventRegistrationPayload(fields);
    } catch (error) {
      setRegistrationError(error.message);
      return;
    }
    const controller = new AbortController();
    registrationRequest.current = controller;
    const isCurrent = () => mounted.current && registrationRequest.current === controller && !controller.signal.aborted;
    setSaving(true);
    try {
      const response = await fetch("/api/solvents", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal,
      });
      if (!response.ok) throw new Error(await readResponseError(response));
      const payload = await response.json();
      if (!isCurrent()) return;
      if (payload?.ok !== true || typeof payload.solvent?.name !== "string" || !payload.solvent.name.trim()) throw new Error("服务端未返回有效的登记回执。");
      setRegistered(payload.solvent);
      setFields({ name: "", epsilon: "", epsinf: "" });
      setQuery(payload.solvent.name);
      void loadCatalog(payload.solvent.name);
    } catch (error) {
      if (isCurrent()) setRegistrationError(`${error instanceof Error ? error.message : "登记请求未完成。"} 请先查询隐式溶剂库核对结果，再决定是否重试；不会自动重复登记。`);
    } finally {
      if (isCurrent()) setSaving(false);
      if (registrationRequest.current === controller) registrationRequest.current = null;
    }
  };
  return <Dialog label="Gaussian 隐式溶剂库" className="solvent-dialog" closeDisabled={saving} onClose={() => { if (!registrationRequest.current) onClose(); }}>
    <div ref={contentRef}>
      <span className="eyebrow">GAUSSIAN · IMPLICIT SOLVENT</span><h2>隐式溶剂查询和登记</h2>
      <p className="solvent-boundary">此处仅登记适用于Gaussian的隐式溶剂性质，上传溶剂分子请到上传分子结构/结构库；自定义溶剂登记后，可直接在方案助理中指定使用。</p>
      <form className="solvent-query-form" aria-label="查询隐式溶剂" onSubmit={(event) => { event.preventDefault(); void loadCatalog(query); }}>
        <label htmlFor="solvent-query">隐式溶剂名称或关键词<input id="solvent-query" value={query} maxLength={128} placeholder="留空查询全部隐式溶剂" onChange={(event) => setQuery(event.target.value)} /></label>
        <button type="submit" disabled={searching}>{searching ? "查询中…" : "查询隐式溶剂"}</button>
      </form>
      <section className="solvent-results" aria-label="隐式溶剂查询结果" aria-busy={searching}>
        {queryError && <p className="solvent-error" role="alert">{queryError}</p>}
        {searching && <p role="status">正在读取隐式溶剂库…</p>}
        {catalog && <><p role="status">{catalog.exact ? "精确匹配" : catalog.query ? "未找到精确匹配，以下仅为候选，不会自动采用。" : `隐式溶剂库 · ${catalog.records.length} 项`}</p>
          {catalog.records.length ? <ul className="solvent-record-list">{catalog.records.map((record, index) => <li key={`${record.name}-${index}`}><SolventRecord record={record} /></li>)}</ul> : <p>暂无匹配隐式溶剂，可在下方登记自定义隐式溶剂。</p>}</>}
      </section>
      <form className="solvent-register-form" aria-label="登记自定义隐式溶剂" onSubmit={register}>
        <h3>登记自定义 Gaussian 隐式溶剂</h3>
        <p id="solvent-registration-help">仅Eps/EpsInf的介电近似，不是完整SMD参数化。名称可空，由服务端分配 default_编号；同名不可覆盖。必须满足 epsilon ≥ epsinf ≥ 1。</p>
        <div className="solvent-fields">
          <label className="solvent-name-field" htmlFor="solvent-name">name（可选）<input id="solvent-name" value={fields.name} maxLength={96} disabled={saving} aria-describedby="solvent-registration-help" placeholder="留空自动编号" onChange={(event) => setFields((current) => ({ ...current, name: event.target.value }))} /></label>
          <label htmlFor="solvent-epsilon">epsilon<input id="solvent-epsilon" type="number" min="1" step="any" required value={fields.epsilon} disabled={saving} aria-describedby="solvent-registration-help" onChange={(event) => setFields((current) => ({ ...current, epsilon: event.target.value }))} /></label>
          <label htmlFor="solvent-epsinf">epsinf<input id="solvent-epsinf" type="number" min="1" step="any" required value={fields.epsinf} disabled={saving} aria-describedby="solvent-registration-help" onChange={(event) => setFields((current) => ({ ...current, epsinf: event.target.value }))} /></label>
        </div>
        <button type="submit" disabled={saving}>{saving ? "登记中…" : "仅登记隐式溶剂"}</button>
        {registrationError && <p className="solvent-error" role="alert">{registrationError}</p>}
        {registered && <div className="solvent-success" role="status"><p>已登记 {registered.name}，可在方案助理中指定使用。</p><SolventRecord record={registered} /></div>}
      </form>
    </div>
  </Dialog>;
}

function WorkbenchThread({ kind, active, proposalId, controlsBusy = false }) {
  const aui = useAui();
  const [slashOpen, setSlashOpen] = useState(false);
  const [moleculesOpen, setMoleculesOpen] = useState(false);
  const [solventsOpen, setSolventsOpen] = useState(false);
  const closeMolecules = useCallback(() => setMoleculesOpen(false), []);
  const closeSolvents = useCallback(() => setSolventsOpen(false), []);
  useEffect(() => { if (!active) { setMoleculesOpen(false); setSolventsOpen(false); } }, [active]);
  const tailId = useAuiState((state) => state.thread.messages.at(-1)?.id || null);
  const inputId = `${kind}-composer-input`;
  const isRun = kind === "run";
  const appendUploadFailure = useCallback((text) => {
    aui.thread.append({
      parentId: tailId,
      sourceId: null,
      runConfig: {},
      startRun: false,
      role: "user",
      content: [{ type: "text", text }],
      attachments: [],
      metadata: { custom: {} },
      createdAt: new Date(),
    });
  }, [aui, tailId]);
  const applyUploadResult = useCallback((payload) => {
    if (Array.isArray(payload?.messages)) {
      aui.thread.reset(threadHistoryMessages(payload.messages));
      return;
    }
    appendUploadFailure(payload?.text || "结构已上传。");
  }, [appendUploadFailure, aui]);
  return <ThreadPrimitive.Root className="thread-root">
    <ThreadPrimitive.Viewport className="thread-viewport">
      <div className="thread-content">
        <ThreadWelcome kind={kind} />
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage: kind === "proposal" ? ProposalAssistantMessage : AssistantMessage }} />
        {!isRun && <ProposalThinkingIndicator />}
        <ThreadPrimitive.ViewportFooter className="thread-footer">
          <ComposerPrimitive.Root className="composer">
            <ComposerPrimitive.Input
              id={inputId}
              className="composer-input"
              placeholder={isRun ? RUN_PLACEHOLDER : PROPOSAL_PLACEHOLDER}
              rows={1}
              disabled={controlsBusy}
              autoFocus={active}
              onInput={(event) => setSlashOpen(isRun && event.currentTarget.value.trimStart().startsWith("/"))}
            />
            {slashOpen && !controlsBusy && <SlashMenu inputId={inputId} onClose={() => setSlashOpen(false)} />}
            <div className={`composer-actions ${isRun ? "" : "proposal-composer-actions"}`}>
              {!isRun && <button type="button" className="upload-button library-launcher" title="上传分子结构或查看分子库" aria-label="上传分子结构/分子库" aria-haspopup="dialog" aria-expanded={moleculesOpen} onClick={() => setMoleculesOpen(true)}><Upload size={15} /><span>上传分子结构/<wbr />分子库</span></button>}
              {!isRun && <button type="button" className="upload-button library-launcher" title="查询或登记 Gaussian 隐式溶剂" aria-label="登记隐式溶剂/溶剂库" aria-haspopup="dialog" aria-expanded={solventsOpen} onClick={() => setSolventsOpen(true)}><BookOpen size={15} /><span>登记隐式溶剂/<wbr />溶剂库</span></button>}
              <span className="composer-hint">Enter 发送 · Shift+Enter 换行</span>
              <ComposerPrimitive.Send asChild>
                <button type="button" className="send-button" title="发送" aria-label="发送" disabled={controlsBusy}><Send size={17} /></button>
              </ComposerPrimitive.Send>
            </div>
          </ComposerPrimitive.Root>
        </ThreadPrimitive.ViewportFooter>
      </div>
    </ThreadPrimitive.Viewport>
    {!isRun && active && moleculesOpen && <MoleculeCatalogDialog proposalId={proposalId} onResult={applyUploadResult} onFailure={appendUploadFailure} onClose={closeMolecules} />}
    {!isRun && active && solventsOpen && <SolventCatalogDialog onClose={closeSolvents} />}
  </ThreadPrimitive.Root>;
}

function RunHistoryLoader({ runId, onSnapshot }) {
  const aui = useAui();
  useEffect(() => {
    let cancelled = false;
    if (!runId) {
      aui.thread.reset([]);
      return undefined;
    }
    fetch(`/api/runs/${encodeURIComponent(runId)}/chat`)
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => {
        if (cancelled || payload?.run_id !== runId) return;
        if (payload.snapshot && !onSnapshot(payload.snapshot, payload.run_id)) return;
        const messages = Array.isArray(payload.messages) ? payload.messages : [];
        aui.thread.reset(threadHistoryMessages(messages));
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [aui, onSnapshot, runId]);
  return null;
}

function ProposalHistoryLoader({ proposalId }) {
  const aui = useAui();
  useEffect(() => {
    let cancelled = false;
    if (!proposalId) {
      aui.thread.reset([]);
      return undefined;
    }
    fetch(`/api/proposals/${encodeURIComponent(proposalId)}`)
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => {
        if (cancelled || payload?.workspace?.workspace_id !== proposalId) return;
        aui.thread.reset(threadHistoryMessages(Array.isArray(payload.messages) ? payload.messages : []));
      })
      .catch(() => undefined);
    return () => { cancelled = true; };
  }, [aui, proposalId]);
  return null;
}

function threadHistoryMessages(messages) {
  return messages.map((message) => ({
    role: message.role,
    content: [{ type: "text", text: message.content }],
    metadata: { custom: { runActivityKind: message._run_assistant_event_kind || "", runActivityActive: message._run_assistant_event_active === true } },
  }));
}

function RunHistorySync({ runId, active, onSnapshot }) {
  const aui = useAui();
  const cursor = useRef("");
  useEffect(() => {
    cursor.current = "";
  }, [runId]);
  useEffect(() => {
    if (!active || !runId) return undefined;
    let disposed = false;
    const poll = async () => {
      try {
        const suffix = cursor.current ? `?after=${encodeURIComponent(cursor.current)}` : "";
        const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/updates${suffix}`);
        if (!response.ok) return;
        const data = await response.json();
        if (disposed || (data.run_id && data.run_id !== runId)) return;
        if (data.snapshot && !onSnapshot(data.snapshot, data.run_id)) return;
        const incoming = data.events || data.messages || [];
        if (incoming.length) {
          const historyResponse = await fetch(`/api/runs/${encodeURIComponent(runId)}/chat`);
          const historyPayload = historyResponse.ok ? await historyResponse.json() : null;
          if (!disposed && historyPayload?.run_id === runId) {
            if (historyPayload.snapshot && !onSnapshot(historyPayload.snapshot, historyPayload.run_id)) return;
            aui.thread.reset(threadHistoryMessages(Array.isArray(historyPayload.messages) ? historyPayload.messages : []));
          }
        }
        if (disposed) return;
        cursor.current = data.cursor || data.status_event_id || data.snapshot?.status_event_id || cursor.current;
      } catch { /* polling is advisory and must never block chat */ }
    };
    poll();
    const interval = window.setInterval(poll, 3000);
    return () => { disposed = true; window.clearInterval(interval); };
  }, [active, aui, runId, onSnapshot]);
  return null;
}

function RunStopControl({ runId, snapshot, onStop }) {
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState("");
  const state = String(snapshot?.state || "").toLowerCase();
  const running = ["running", "started", "executing", "retrying"].includes(state);
  useEffect(() => { setConfirming(false); setMessage(""); }, [runId, state]);
  if (!running || !runId) return null;
  const requestStop = async () => {
    if (!confirming) {
      setConfirming(true);
      return;
    }
    try {
      const result = await onStop(runId);
      setMessage(result.message || "已请求中止当前流水线。");
      setConfirming(false);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "中止请求未送达，请重试。");
      setConfirming(false);
    }
  };
  return <div className="run-stop-control"><button type="button" className={`stop-button ${confirming ? "confirming" : ""}`} onClick={requestStop}><Square size={13} />{confirming ? "确认中止" : "中止流水线"}</button>{message && <span aria-live="polite">{message}</span>}</div>;
}

function recoveryContext(runId, snapshot) {
  const state = String(snapshot?.state || snapshot?.status?.state || "").toLowerCase();
  const recoverable = Boolean(runId) && snapshot?.run_id === runId
    && ["aborted", "awaiting_confirmation", "escalated"].includes(state);
  const revision = snapshot?.state_revision ?? snapshot?.status?.state_revision;
  const action = snapshot?.pending_action;
  const validAction = recoverable && action
    && (!action.run_id || action.run_id === runId)
    && typeof action.action_id === "string" && action.action_id.trim()
    && Number.isInteger(action.state_revision) && action.state_revision >= 0
    && action.state_revision === revision
    && typeof action.config_fingerprint === "string" && action.config_fingerprint.trim();
  return { recoverable, revision, action: validAction ? action : null };
}

function PendingActionConfirmation({ runId, snapshot, onSnapshot, isCurrentProject, recoveryLock }) {
  const aui = useAui();
  const chatBusy = useAuiState((state) => state.thread.isRunning);
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState("");
  const [editing, setEditing] = useState(false);
  const [request, setRequest] = useState("");
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const inFlight = useRef(null);
  const mounted = useRef(true);
  const latest = useRef(null);
  latest.current = { runId, snapshot, onSnapshot, isCurrentProject, chatBusy, editing, request, needsRefresh };
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const { recoverable, revision, action } = recoveryContext(runId, snapshot);
  const busy = Boolean(submitting) || chatBusy || recoveryLock.isBusy(runId);
  const submit = async (operation) => {
    const current = latest.current;
    if (!mounted.current || !current || inFlight.current || current.chatBusy || !current.isCurrentProject(current.snapshot)) return;
    const context = recoveryContext(current.runId, current.snapshot);
    if (operation !== "refresh" && (!context.recoverable || current.needsRefresh)) return;
    if (operation === "resume" && (!Number.isInteger(context.revision) || context.revision < 0)) return;
    if (operation === "resume" && current.snapshot?.fault_review?.reason === "process_exit_unconfirmed") return;
    if (operation === "resolve" && (!current.snapshot?.fault_review?.manual_completion_allowed
      || !current.snapshot?.fault?.fault_id || !Number.isInteger(context.revision))) return;
    if (["confirm", "revise"].includes(operation) && !context.action) return;
    if (operation === "confirm" && (current.editing || context.action.selection_required)) return;
    if (operation === "revise" && !current.request.trim()) return;
    const token = recoveryLock.acquire(current.runId);
    if (!token) return;
    inFlight.current = token;
    const stillCurrent = () => mounted.current && inFlight.current === token && current.isCurrentProject();
    const refreshSnapshot = async () => {
      const response = await fetch(`/api/runs/${encodeURIComponent(current.runId)}/snapshot`);
      if (!response.ok) throw new Error(await readResponseError(response));
      const freshSnapshot = await response.json();
      if (!stillCurrent()) return false;
      if (!current.onSnapshot(freshSnapshot, current.runId)) throw new Error("状态已变化，请重新刷新。");
      setNeedsRefresh(false);
      return true;
    };
    setSubmitting(operation);
    setMessage("");
    try {
      if (operation === "refresh") {
        if (await refreshSnapshot()) setMessage("已刷新当前状态，请核对后操作。");
        return;
      }
      const endpoint = operation === "resume" ? "resume" : operation === "resolve" ? "fault/complete" : `pending-action/${operation}`;
      const body = operation === "resolve" ? { state_revision: context.revision, fault_id: current.snapshot.fault.fault_id }
        : operation === "resume" ? { state_revision: context.revision } : {
        action_id: context.action.action_id,
        state_revision: context.action.state_revision,
        config_fingerprint: context.action.config_fingerprint,
        ...(operation === "revise" ? { request: current.request.trim() } : {}),
      };
      const response = await fetch(`/api/runs/${encodeURIComponent(current.runId)}/${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!stillCurrent()) return;
      if (response.status === 409) {
        setNeedsRefresh(true);
        if (await refreshSnapshot()) setMessage("状态或方案已更新，请核对最新内容后重新操作；未继续提交旧方案。");
        return;
      }
      if (!response.ok) throw new Error(await readResponseError(response));
      const payload = await response.json();
      if (!stillCurrent()) return;
      if (!payload.snapshot || !current.onSnapshot(payload.snapshot, payload.run_id, operation === "confirm")) {
        throw new Error("返回的工程或状态已变化，请刷新后核对。");
      }
      if (Array.isArray(payload.messages)) aui.thread.reset(threadHistoryMessages(payload.messages));
      setNeedsRefresh(false);
      if (operation === "revise") {
        const revisedAction = recoveryContext(current.runId, payload.snapshot).action;
        if (!revisedAction || revisedAction.action_id === context.action.action_id
          || revisedAction.state_revision <= context.action.state_revision) {
          setMessage(payload.text || "方案未更新，修改内容已保留，请调整要求后重试。");
          return;
        }
      }
      setEditing(false);
      setRequest("");
      setMessage(payload.text || (operation === "revise"
        ? "方案已更新，尚未启动。请核对最新建议，再确认创建分支。"
        : operation === "resume" ? "已提交原参数续跑请求。" : "已提交确认请求。"));
    } catch (error) {
      if (!stillCurrent()) return;
      const failure = error instanceof Error ? error.message : "请求未完成，请刷新后核对。";
      setNeedsRefresh(true);
      try {
        if (operation !== "refresh" && await refreshSnapshot()) {
          setMessage(`${failure} 已刷新当前状态，未自动重试。`);
          return;
        }
      } catch {}
      if (stillCurrent()) setMessage(`${failure} 请先刷新状态，避免提交旧方案。`);
    } finally {
      if (stillCurrent()) setSubmitting("");
      if (inFlight.current === token) inFlight.current = null;
      recoveryLock.release(current.runId, token);
    }
  };
  if (!recoverable && !message && !submitting) return null;
  return <div className="pending-action-confirmation run-recovery" aria-label="运行恢复" aria-busy={busy}>
    {recoverable && <>
      {snapshot?.fault?.resolution === "pending" && <section className="recovery-proposal" aria-label="故障复查">
        <strong>故障位置：第 {snapshot.fault.step} 步 · {{ initialization: "初始化", preflight: "预检", execution: "执行", acceptance: "验收", recovery: "恢复", launch: "启动交接", stopping: "安全停止", cleanup: "进程回收" }[snapshot.fault.phase]}</strong>
        <p className="recovery-hint">{{ process_exit_unconfirmed: "尚未确认进程退出，请先完成人工处理。", prerequisites_unmet: "故障条件尚未解除，请修复后刷新复查。", state_changed: "状态已变化，请刷新复查。", manual_review_required: "此类故障无法仅靠文件判断；完成处理后请明确声明。" }[snapshot?.fault_review?.reason] || "刷新时会重新检查；复查不会启动计算。"}</p>
        {snapshot?.fault_review?.manual_completion_allowed && <button type="button" disabled={busy || needsRefresh} onClick={() => submit("resolve")}>{submitting === "resolve" ? "正在复查" : "已完成人工处理，复查"}</button>}
      </section>}
      <div className="recovery-actions"><button type="button" className="resume-original" disabled={busy || needsRefresh || snapshot?.fault_review?.reason === "process_exit_unconfirmed" || !Number.isInteger(revision) || revision < 0} onClick={() => submit("resume")}><Play size={13} />{submitting === "resume" ? "正在续跑" : "原参数续跑"}</button><span className="recovery-hint">沿用当前工程和原配置，不应用建议。</span></div>
      {action && <section className="recovery-proposal" aria-label="最新待确认方案">
        <div className="recovery-proposal-heading"><ShieldCheck size={15} /><strong>最新建议 · 待确认</strong></div>
        <p className="recovery-summary">{action.summary || "请核对方案后确认，或提出修改要求。"}</p>
        {Array.isArray(action.adjustments) && action.adjustments.length > 0 && <ul className="recovery-adjustments">{action.adjustments.map((adjustment, index) => <li key={`${adjustment.name || adjustment.field}-${index}`}><code>{adjustment.name || adjustment.field}</code>：{JSON.stringify(adjustment.before) ?? "未设置"} → {JSON.stringify(adjustment.after ?? adjustment.value) ?? "未设置"}</li>)}</ul>}
        {action.compatibility_notice && <p className="recovery-hint">{action.compatibility_notice}</p>}
        {action.selection_required && <p className="recovery-hint">请先选择方案再确认。当前可通过“修改方案”说明希望采用的方案；明确选择前不可创建分支。</p>}
        <div className="recovery-actions"><button type="button" disabled={busy || needsRefresh || editing || Boolean(action.selection_required)} onClick={() => submit("confirm")}><Play size={13} />{submitting === "confirm" ? "正在确认" : "确认方案并创建分支"}</button>{!editing && <button type="button" className="revise-action" disabled={busy || needsRefresh} onClick={() => setEditing(true)}>修改方案</button>}</div>
        {editing && <form className="recovery-revise-form" onSubmit={(event) => { event.preventDefault(); submit("revise"); }}>
          <label htmlFor={`recovery-request-${runId}`}>修改方案<textarea id={`recovery-request-${runId}`} rows={2} value={request} disabled={busy || needsRefresh} onChange={(event) => setRequest(event.target.value)} placeholder="描述希望调整的参数或约束" /></label>
          <div className="recovery-actions"><button type="submit" disabled={busy || needsRefresh || !request.trim()}>{submitting === "revise" ? "正在更新方案" : "提交修改"}</button><button type="button" className="revise-action" disabled={busy} onClick={() => { setEditing(false); setRequest(""); }}>取消修改</button><span className="recovery-hint">仅更新待确认方案，不启动计算。</span></div>
        </form>}
      </section>}
    </>}
    {needsRefresh && <button type="button" disabled={busy} onClick={() => submit("refresh")}><RefreshCw size={13} />刷新状态</button>}
    {message && <span className="pending-action-confirmation-message" role="status" aria-live="polite">{message}</span>}
  </div>;
}

function AssistantCompletionSync({ pendingTransition, onPipelineStarted, onProposalWorkspaceChanged }) {
  const isRunning = useAuiState((state) => state.thread.isRunning);
  useEffect(() => {
    if (isRunning || !pendingTransition.current) return;
    const transition = pendingTransition.current;
    pendingTransition.current = null;
    if (transition.runId) onPipelineStarted(transition.runId);
    else onProposalWorkspaceChanged(transition.proposalId);
  }, [isRunning, pendingTransition, onPipelineStarted, onProposalWorkspaceChanged]);
  return null;
}

function AssistantSurface({ kind, active, runId, proposalId, snapshot, onPipelineStarted, onProposalWorkspaceChanged, onSnapshot, onStop, isCurrentProject, recoveryLock }) {
  const pendingTransition = useRef(null);
  const adapter = useMemo(
    () => createChatAdapter(kind, runId, proposalId,
      (nextRunId) => { pendingTransition.current = { runId: nextRunId }; },
      (nextProposalId) => { pendingTransition.current = { proposalId: nextProposalId }; }),
    [kind, runId, proposalId],
  );
  const runtime = useLocalRuntime(adapter, { unstable_enableMessageQueue: true });
  return <AssistantRuntimeProvider runtime={runtime}>
    <AssistantCompletionSync pendingTransition={pendingTransition} onPipelineStarted={onPipelineStarted} onProposalWorkspaceChanged={onProposalWorkspaceChanged} />
    <section className={`assistant-surface ${active ? "active" : ""}`} aria-hidden={!active}>
      {kind === "proposal" && <ProposalHistoryLoader proposalId={proposalId} />}
      {kind === "run" && <RunHistoryLoader runId={runId} onSnapshot={onSnapshot} />}
      {kind === "run" && <RunHistorySync runId={runId} active={active} onSnapshot={onSnapshot} />}
      {kind === "run" && <PendingActionConfirmation runId={runId} snapshot={snapshot} onSnapshot={onSnapshot} isCurrentProject={isCurrentProject} recoveryLock={recoveryLock} />}
      {kind === "run" && <RunStopControl runId={runId} snapshot={snapshot} onStop={onStop} />}
      <WorkbenchThread kind={kind} active={active} proposalId={proposalId} controlsBusy={kind === "run" && recoveryLock.isBusy(runId)} />
    </section>
  </AssistantRuntimeProvider>;
}

function VisualizationSurface({ active, runId, runs }) {
  const [visualization, setVisualization] = useState(null);
  const [visualRunId, setVisualRunId] = useState("");
  const [selected, setSelected] = useState("");
  const [sphereScale, setSphereScale] = useState("0.35");
  const [stickRadius, setStickRadius] = useState("0.22");
  const [background, setBackground] = useState("beige");
  useEffect(() => {
    setVisualRunId(runId || "");
    setSelected("");
  }, [runId]);
  useEffect(() => {
    if (!active) return;
    const parameters = new URLSearchParams();
    if (visualRunId || runId) parameters.set("run_id", visualRunId || runId);
    if (selected) parameters.set("artifact", selected);
    parameters.set("sphere_scale", sphereScale);
    parameters.set("stick_radius", stickRadius);
    parameters.set("background", background);
    const query = parameters.size ? `?${parameters}` : "";
    fetch(`/api/visualization${query}`)
      .then((response) => response.ok ? response.json() : null)
      .then((payload) => {
        setVisualization(payload);
        if (!visualRunId && payload?.run_id) setVisualRunId(payload.run_id);
        setSelected(payload?.selected_artifact || payload?.artifacts?.[0] || "");
        if (payload?.sphere_scale != null) setSphereScale(String(payload.sphere_scale));
        if (payload?.stick_radius != null) setStickRadius(String(payload.stick_radius));
        if (typeof payload?.background === "string") setBackground(payload.background);
      })
      .catch(() => setVisualization(null));
  }, [active, background, runId, selected, sphereScale, stickRadius, visualRunId]);
  const visibleRunId = visualization?.run_id || visualRunId || runId || "";
  const visibleRun = runs.find((item) => item.run_id === visibleRunId);
  const visibleRunName = visibleRun?.display_name || visibleRunId || "暂无工程";
  return <section className={`assistant-surface visual-surface ${active ? "active" : ""}`} aria-hidden={!active}>
    <div className="visual-heading"><h3>{visibleRunName}</h3></div>
    <div className="visual-toolbar">
      <label className="visual-field"><span>运行目录</span><select aria-label="运行目录" value={visualRunId} onChange={(event) => { setVisualRunId(event.target.value); setSelected(""); }} disabled={!visualization?.run_choices?.length}><option value="">选择工程</option>{visualization?.run_choices?.map((choice) => <option key={choice} value={choice}>{choice}</option>)}</select></label>
      <label className="visual-field"><span>结构文件</span><select aria-label="结构文件" value={selected} onChange={(event) => setSelected(event.target.value)} disabled={!visualization?.artifacts?.length}>{!visualization?.artifacts?.length && <option value="">暂无可视化结构</option>}{visualization?.artifacts?.map((artifact) => <option key={artifact} value={artifact}>{artifact}</option>)}</select></label>
      <label className="visual-field"><span>球体大小</span><select aria-label="球体大小" value={sphereScale} onChange={(event) => setSphereScale(event.target.value)}>{SPHERE_SCALE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      <label className="visual-field"><span>棍宽度</span><select aria-label="棍宽度" value={stickRadius} onChange={(event) => setStickRadius(event.target.value)}>{STICK_RADIUS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      <div className="visual-field background-field"><span>背景</span><div className="background-picker" role="group" aria-label="背景">{BACKGROUND_OPTIONS.map((option) => <button key={option.value} type="button" className={`background-choice ${option.value} ${background === option.value ? "active" : ""}`} aria-pressed={background === option.value} onClick={() => setBackground(option.value)}>{option.label}</button>)}</div></div>
    </div>
    {visualization?.html ? <iframe className="structure-frame" title={visualization.selected_artifact || "结构预览"} sandbox="allow-scripts allow-same-origin" scrolling="no" srcDoc={visualization.html} /> : <div className="visual-empty"><Atom size={31} /><p>当前工程暂无可读取的 PDB 或 MOL2 结构。</p></div>}
    {visualization?.legend_html && <div className="visual-legend" dangerouslySetInnerHTML={{ __html: visualization.legend_html }} />}
  </section>;
}

function LogsSurface({ active, runId, runs }) {
  const [view, setView] = useState({ context: null, records: null, error: "", refreshing: false });
  const currentContext = useRef(null);
  if (currentContext.current?.runId !== runId || currentContext.current?.active !== active) {
    currentContext.current = { runId, active, request: 0 };
  }
  const context = currentContext.current;
  const loadRecords = useCallback(async () => {
    if (!runId || !active || currentContext.current !== context) return;
    const request = ++context.request;
    const isCurrent = () => currentContext.current === context && context.request === request;
    setView((previous) => ({
      context, records: previous.context === context ? previous.records : null, error: "", refreshing: true,
    }));
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/logs`);
      if (!response.ok) throw new Error(await readResponseError(response));
      const records = await response.json();
      if (!isCurrent()) return;
      if (records.run_id !== runId) throw new Error("日志响应与当前工程不一致");
      setView({ context, records, error: "", refreshing: false });
    } catch (loadError) {
      if (isCurrent()) setView({
        context, records: null, refreshing: false,
        error: loadError instanceof Error ? loadError.message : "日志暂时无法读取",
      });
    }
  }, [active, runId, context]);
  useEffect(() => {
    if (active) loadRecords();
    return () => { context.request += 1; };
  }, [active, loadRecords, context]);
  const { records, error, refreshing } = view.context === context
    ? view : { records: null, error: "", refreshing: Boolean(runId && active) };
  const currentRun = runs.find((item) => item.run_id === runId);
  const runName = currentRun?.display_name || runId || "暂无工程";
  return <section className={`assistant-surface logs-surface ${active ? "active" : ""}`} aria-hidden={!active}>
    <div className="logs-heading"><div><span className="eyebrow">AUDIT RECORDS</span><h3>{runName}</h3></div><button type="button" className="icon-button" title="刷新日志" aria-label="刷新日志" disabled={!runId || refreshing} onClick={loadRecords}><RefreshCw className={refreshing ? "spin" : ""} size={16} /></button></div>
    {!runId ? <div className="logs-empty"><ClipboardList size={30} /><p>选择一个工程以查看其记录。</p></div> : error ? <div className="logs-empty"><p>{error}</p></div> : <div className="logs-grid">{[["manifest", "Manifest"], ["config", "Config JSON"]].map(([key, label]) => { const record = records?.[key]; return <article className="log-column" key={key}><header><strong>{label}</strong><code>{record?.filename || (key === "config" ? "config.json" : "读取中")}</code></header><pre aria-label={`${label} 内容`}>{record?.content || "正在读取记录..."}</pre></article>; })}</div>}
  </section>;
}

function statusData(snapshot) {
  const status = snapshot?.status || {};
  const state = snapshot?.state || status.state || "await";
  return {
    runId: snapshot?.run_id || "暂无运行",
    state,
    phase: snapshot?.phase || status.phase || status.stage || "—",
    progress: snapshot?.progress || status.progress || "—",
    summary: snapshot?.live_summary || snapshot?.summary || "等待连接到当前工程。",
  };
}

function StatusPanel({ collapsed, onToggle, snapshot, onRefresh, refreshState }) {
  const data = statusData(snapshot);
  const preparing = String(data.state).toLowerCase() === "preparing";
  const running = ["running", "started", "executing", "retrying"].includes(String(data.state).toLowerCase());
  const active = preparing || running;
  const refreshLabel = refreshState === "refreshing" ? "正在刷新" : refreshState === "done" ? "已刷新" : refreshState === "failed" ? "刷新失败" : "刷新状态";
  return <aside className={`status-panel ${collapsed ? "collapsed" : ""}`}>
    <div className="panel-heading"><span className="panel-heading-text">运行检查器</span><button type="button" className="icon-button" title={collapsed ? "展开运行检查器" : "收起运行检查器"} aria-label={collapsed ? "展开运行检查器" : "收起运行检查器"} aria-expanded={!collapsed} onClick={onToggle}>{collapsed ? <PanelRightOpen size={16} /> : <PanelRightClose size={16} />}</button></div>
    {!collapsed && <>
      <div className={`status-banner ${active ? "is-running" : ""}`}><span className="status-dot" /><div><strong>{preparing ? "准备中" : running ? "运行中" : "等待操作"}</strong><small>{data.runId}</small></div></div>
      <div className="metric-grid"><div><span>阶段</span><b>{data.phase}</b></div><div><span>进度</span><b>{data.progress}</b></div></div>
      <div className="inspector-section status-summary-section"><div className="section-label"><Activity size={14} /> 当前状态</div><p className="status-summary">{data.summary.replace(/^#+\s*/, "")}</p></div>
      <div className="inspector-section"><div className="section-label"><ShieldCheck size={14} /> 受控边界</div><div className="check-row"><span>输入契约</span><b className="ok">已检查</b></div><div className="check-row"><span>资源预检</span><b className="ok">已登记</b></div><div className="check-row"><span>记录文件</span><b className="ok">manifest / config.json</b></div></div>
      <button type="button" className="refresh-button" disabled={refreshState === "refreshing"} onClick={() => onRefresh("status")}><RefreshCw className={refreshState === "refreshing" ? "spin" : ""} size={15} />{refreshLabel}</button>
    </>}
  </aside>;
}

function RichText({ parts }) {
  return parts.map((part, index) => {
    if (part.type === "strong") return <strong key={index}>{part.value}</strong>;
    if (part.type === "code") return <code key={index}>{part.value}</code>;
    if (part.type === "link") return <a key={index} href={part.href} target="_blank" rel="noreferrer">{part.value}</a>;
    return <React.Fragment key={index}>{part.value}</React.Fragment>;
  });
}

function Dialog({ label, children, onClose, className = "", closeDisabled = false }) {
  return <div className="dialog-backdrop" role="presentation">
    <section className={`dialog-card ${className}`} role="dialog" aria-modal="true" aria-label={label}>
      <button type="button" className="dialog-close" title="关闭" aria-label="关闭" disabled={closeDisabled} onClick={onClose}><X size={17} /></button>
      {children}
    </section>
  </div>;
}

function WillyIntroductionDialog({ onClose }) {
  return <Dialog label="Willy 简介" onClose={onClose} className="about-dialog">
    <span className="eyebrow">ABOUT</span>
    <h2>{ABOUT_WILLY.title}</h2>
    <h3>{ABOUT_WILLY.introduction.title}</h3>
    {ABOUT_WILLY.introduction.paragraphs.map((paragraph, index) => <p key={index}><RichText parts={paragraph} /></p>)}
  </Dialog>;
}

function OfficialAccountDialog({ onClose }) {
  return <Dialog label="公众号二维码" onClose={onClose} className="qr-dialog">
    <span className="eyebrow">OFFICIAL ACCOUNT</span>
    <h2>{ABOUT_WILLY.officialAccount.label}</h2>
    <img src="/app-assets/qrcode_for_gh_7df1329939c6_258.jpg" alt={`${ABOUT_WILLY.officialAccount.label} 二维码`} />
  </Dialog>;
}

function LocalTaskContent({ runs, plans, temps, selectedRunId, selectedProposalId, onSelectRun, onSelectPlan, onCreateTemp, onRefresh, onRename, onDelete, refreshState }) {
  const [editing, setEditing] = useState(null);
  const [displayName, setDisplayName] = useState("");
  const [renameError, setRenameError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deleteStage, setDeleteStage] = useState(0);
  const [deleteError, setDeleteError] = useState("");
  const openRename = (run) => { setEditing(run); setDisplayName(run.display_name || run.run_id); setRenameError(""); };
  const openDelete = (item) => { setDeleteTarget(item); setDeleteStage(0); setDeleteError(""); };
  const submitRename = async (event) => {
    event.preventDefault();
    if (!editing) return;
    try {
      await onRename(editing.run_id, displayName);
      setEditing(null);
    } catch (error) {
      setRenameError(error instanceof Error ? error.message : "重命名失败");
    }
  };
  const submitDelete = async () => {
    if (!deleteTarget) return;
    if (deleteTarget.kind === "plan" && deleteStage === 0) {
      setDeleteStage(1);
      return;
    }
    try {
      await onDelete(deleteTarget.itemId, deleteTarget.kind, deleteTarget.kind === "plan");
      setDeleteTarget(null);
    } catch (error) {
      setDeleteError(error instanceof Error ? error.message : "删除失败");
    }
  };
  const refreshLabel = refreshState === "refreshing" ? "正在刷新" : refreshState === "done" ? "已刷新" : refreshState === "failed" ? "刷新失败" : "";
  const itemRow = (item, label, selected, select) => <div className={`run-directory-row proposal-directory-row ${selected ? "selected" : ""}`} key={item.workspace_id}><button type="button" className="run-directory" onClick={() => select(item.workspace_id)}><span className={`run-state-dot ${item.state === "awaiting_confirmation" ? "await" : "idle"}`} /><span><b>{item.workspace_id}</b><small>{label}{item.updated_at ? ` · ${item.updated_at}` : ""}</small></span><ChevronRight size={15} /></button><button type="button" className="run-menu-button destructive-action" title="删除目录" aria-label={`删除 ${item.workspace_id}`} onClick={() => openDelete({ itemId: item.workspace_id, kind: item.kind })}><Trash2 size={15} /></button></div>;
  return <><section className="drawer-content"><div className="drawer-title"><div><span className="eyebrow">LOCAL WORKSPACE</span><h2>本地任务</h2></div><div className="drawer-refresh"><button type="button" className="icon-button" title="刷新工程目录" aria-label="刷新工程目录" disabled={refreshState === "refreshing"} onClick={() => onRefresh("tasks")}><RefreshCw className={refreshState === "refreshing" ? "spin" : ""} size={16} /></button>{refreshLabel && <span aria-live="polite">{refreshLabel}</span>}</div></div>
    <p className="drawer-lead">Willy/md_run/目录下的已有工程。</p>
    <div className="run-directory-list" aria-label="当前工程目录">
      <div className="proposal-workspace-heading"><span>历史工程 Previous Process</span></div>
      {runs.length ? runs.map((run) => <div className={`run-directory-row run-directory-row--managed ${selectedRunId === run.run_id ? "selected" : ""}`} key={run.run_id} onContextMenu={(event) => { event.preventDefault(); openRename(run); }}><button type="button" className="run-directory" onClick={() => onSelectRun(run.run_id)}><span className={`run-state-dot ${run.state || "await"}`} /><span><b>{run.display_name || run.run_id}</b><small>{run.display_name ? `${run.run_id} · ` : ""}{run.state || "await"}{run.updated_at ? ` · ${run.updated_at}` : ""}</small></span><ChevronRight size={15} /></button><div className="directory-actions"><button type="button" className="run-menu-button" title="重命名工程" aria-label={`${run.run_id} 重命名工程`} onClick={() => openRename(run)}><MoreHorizontal size={16} /></button><button type="button" className="run-menu-button destructive-action" title="删除工程" aria-label={`删除 ${run.run_id}`} onClick={() => openDelete({ itemId: run.run_id, kind: "run" })}><Trash2 size={15} /></button></div>{editing?.run_id === run.run_id && <form className="rename-popover" onSubmit={submitRename}><label>工程名称<input aria-label="工程名称" value={displayName} maxLength={64} pattern="[A-Za-z0-9][A-Za-z0-9_-]{0,63}" onChange={(event) => setDisplayName(event.target.value)} autoFocus /></label>{renameError && <p>{renameError}</p>}<div><button type="button" onClick={() => setEditing(null)}>取消</button><button type="submit">重命名</button></div></form>}</div>) : <div className="empty-run-list"><FolderKanban size={20} /><span>尚无历史工程</span></div>}
      <div className="proposal-workspace-heading"><span>方案 Plan</span></div>
      {plans.length ? plans.map((plan) => itemRow(plan, "待确认方案", selectedProposalId === plan.workspace_id, onSelectPlan)) : <div className="directory-empty">尚无待确认方案</div>}
      <div className="proposal-workspace-heading"><span>临时目录 Temporary List</span><button type="button" className="icon-button" title="新建临时方案" aria-label="新建临时方案" onClick={onCreateTemp}><Plus size={15} /></button></div>
      {temps.length ? temps.map((temp) => itemRow(temp, "临时方案", selectedProposalId === temp.workspace_id, onSelectPlan)) : <div className="directory-empty">尚无临时目录</div>}
    </div>
  </section>{deleteTarget && <Dialog label="删除本地任务" onClose={() => setDeleteTarget(null)} className="delete-directory-dialog"><span className="eyebrow">REMOVE LOCAL DIRECTORY</span><h2>{deleteStage === 0 ? "删除此目录？" : "再次确认删除方案"}</h2><p>{deleteTarget.kind === "plan" ? deleteStage === 0 ? "方案目录将被删除。继续后还需要再次确认。" : "该方案及其中的会话和待确认配置将永久删除，无法恢复。" : "该目录及其本地记录将被永久删除，无法恢复。"}</p>{deleteError && <p className="delete-error">{deleteError}</p>}<div className="dialog-actions"><button type="button" onClick={() => setDeleteTarget(null)}>取消</button><button type="button" className="danger-button" onClick={submitDelete}>{deleteTarget.kind === "plan" && deleteStage === 0 ? "继续" : "删除"}</button></div></Dialog>}</>;
}

function ConfigurationContent() {
  const [status, setStatus] = useState({});
  const [notice, setNotice] = useState(LEGACY_CONFIGURATION.sections[0].notice.fallback);
  const [values, setValues] = useState({ api_key: "", base_url: "", model: "" });
  useEffect(() => {
    fetch("/api/config").then((response) => response.ok ? response.json() : null).then((payload) => {
      if (!payload) return;
      if (typeof payload.notice === "string") setNotice(payload.notice);
      if (typeof payload.status === "string") setStatus((current) => ({ ...current, initial: payload.status }));
    }).catch(() => undefined);
  }, []);
  const invoke = async (action) => {
    const routes = { "test-llm-connection": "/api/config/test", "save-llm-config": "/api/config/save", "run-dependency-preflight": "/api/config/preflight" };
    const endpoint = routes[action];
    if (!endpoint) return;
    try {
      const response = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values) });
      const payload = await response.json();
      setStatus((current) => ({ ...current, [action]: payload.message || payload.summary || payload.markdown || payload.detail || "操作已完成。" }));
    } catch { setStatus((current) => ({ ...current, [action]: "暂时无法连接配置服务。" })); }
  };
  return <section className="drawer-content configuration-content"><div className="drawer-title"><div><span className="eyebrow">LOCAL SETTINGS</span><h2>{LEGACY_CONFIGURATION.title}</h2></div><FileCog size={18} /></div>
    {status.initial && <p className="action-status">{status.initial}</p>}
    {LEGACY_CONFIGURATION.sections.map((section) => <details className="detail-accordion" key={section.id} open><summary>{section.title}<ChevronDown size={15} /></summary><div className="detail-accordion-body">{section.notice && <p className="config-notice">{notice}</p>}{section.paragraphs?.map((paragraph, index) => <p key={index}><RichText parts={paragraph} /></p>)}{section.fields?.map((field) => { const key = field.id === "apiKey" ? "api_key" : field.id === "baseUrl" ? "base_url" : field.id; return <label className="config-field" key={field.id}><span>{field.label}</span><input type={field.inputType || "text"} value={values[key] || ""} placeholder={field.placeholder} onChange={(event) => setValues((current) => ({ ...current, [key]: event.target.value }))} /><small>{field.help}</small></label>; })}<div className="config-actions">{section.actions.map((action) => <button type="button" key={action.id} className={action.kind === "primary" ? "primary-action" : "secondary-action"} onClick={() => invoke(action.id)}>{action.label}</button>)}</div>{section.actions.map((action) => status[action.id] && <p key={`${action.id}-status`} className="action-status">{status[action.id]}</p>)}</div></details>)}
  </section>;
}

function GuideContent() {
  return <section className="drawer-content"><div className="drawer-title"><div><span className="eyebrow">DOCUMENTATION</span><h2>新手指南</h2></div><BookOpen size={18} /></div>{BEGINNER_GUIDE_SECTIONS.map((section) => <details className="detail-accordion guide-accordion" key={section.id}><summary>{section.title}<ChevronDown size={15} /></summary><div className="detail-accordion-body">{section.paragraphs?.map((paragraph, index) => <p key={index}><RichText parts={paragraph} /></p>)}{section.subsections?.map((subsection) => <div className="guide-subsection" key={subsection.title}><h3>{subsection.title}</h3>{subsection.paragraphs.map((paragraph, index) => <p key={index}><RichText parts={paragraph} /></p>)}</div>)}</div></details>)}</section>;
}

function AboutContent() {
  const [qrOpen, setQrOpen] = useState(false);
  return <><section className="drawer-content about-content"><div className="drawer-title"><div><span className="eyebrow">ABOUT</span><h2>{ABOUT_WILLY.title}</h2></div><Info size={18} /></div><h3 className="about-heading">{ABOUT_WILLY.introduction.title}</h3>{ABOUT_WILLY.introduction.paragraphs.map((paragraph, index) => <p key={index}><RichText parts={paragraph} /></p>)}{ABOUT_WILLY.sections.map((section) => <details className="detail-accordion" key={section.id}><summary>{section.title}<ChevronDown size={15} /></summary><div className="detail-accordion-body"><ul>{section.items.map((item) => <li key={item}>{item}</li>)}</ul></div></details>)}<button type="button" className="official-account" title="放大查看公众号二维码" onClick={() => setQrOpen(true)}><img src="/app-assets/qrcode_for_gh_7df1329939c6_258.jpg" alt={ABOUT_WILLY.officialAccount.label} /><span>{ABOUT_WILLY.officialAccount.label}</span></button><details className="detail-accordion"><summary>{ABOUT_WILLY.thirdPartyNotices.title}<ChevronDown size={15} /></summary><div className="detail-accordion-body">{ABOUT_WILLY.thirdPartyNotices.paragraphs.map((paragraph, index) => <p key={index}><RichText parts={paragraph} /></p>)}{ABOUT_WILLY.thirdPartyNotices.citationGroups.map((group) => <div className="citation-group" key={group.label}><h3>{group.label}</h3>{group.introduction && <p><RichText parts={group.introduction} /></p>}<ol>{group.citations.map((citation, index) => <li key={index}><RichText parts={citation} /></li>)}</ol></div>)}<p><RichText parts={ABOUT_WILLY.thirdPartyNotices.closing} /></p></div></details></section>{qrOpen && <OfficialAccountDialog onClose={() => setQrOpen(false)} />}</>;
}

function DrawerContent({ section, runs, plans, temps, selectedRunId, selectedProposalId, onSelectRun, onSelectPlan, onCreateTemp, onRefresh, onRename, onDelete, refreshState }) {
  if (section === "config") return <ConfigurationContent />;
  if (section === "guide") return <GuideContent />;
  if (section === "about") return <AboutContent />;
  return <LocalTaskContent runs={runs} plans={plans} temps={temps} selectedRunId={selectedRunId} selectedProposalId={selectedProposalId} onSelectRun={onSelectRun} onSelectPlan={onSelectPlan} onCreateTemp={onCreateTemp} onRefresh={onRefresh} onRename={onRename} onDelete={onDelete} refreshState={refreshState} />;
}

function App() {
  const [section, setSection] = useState("tasks");
  const [drawerOpen, setDrawerOpen] = useState(() => window.innerWidth > 850);
  const [inspectorCollapsed, setInspectorCollapsed] = useState(false);
  const [drawerWidth, setDrawerWidth] = useState(PANEL_WIDTH_LIMITS.drawer.initial);
  const [inspectorWidth, setInspectorWidth] = useState(PANEL_WIDTH_LIMITS.inspector.initial);
  const [isResizing, setIsResizing] = useState(false);
  const [assistantMode, setAssistantMode] = useState("proposal");
  const [snapshot, setSnapshot] = useState(null);
  const [runs, setRuns] = useState([]);
  const [plans, setPlans] = useState([]);
  const [temps, setTemps] = useState([]);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [proposalId, setProposalId] = useState(null);
  const [selectedProjectKind, setSelectedProjectKind] = useState("proposal");
  const [projectVersion, setProjectVersion] = useState(0);
  const [, updateRecoveryLocks] = useState(0);
  const [aboutOpen, setAboutOpen] = useState(false);
  const [refreshStates, setRefreshStates] = useState({ tasks: "idle", status: "idle" });
  const refreshTimers = useRef({});
  const resizeSession = useRef(null);
  const workspaceRef = useRef(null);
  const projectRef = useRef({ version: 0, runId: null, proposalId: null, kind: "proposal" });
  const snapshotRef = useRef(null);
  const recoveryRequests = useRef(new Map());
  const activeLabel = useMemo(() => NAV_ITEMS.find((item) => item.id === section)?.label, [section]);

  const recoveryLock = useMemo(() => ({
    isBusy: (runId) => recoveryRequests.current.has(runId),
    acquire: (runId) => {
      if (recoveryRequests.current.has(runId)) return null;
      const token = {};
      recoveryRequests.current.set(runId, token);
      updateRecoveryLocks((value) => value + 1);
      return token;
    },
    release: (runId, token) => {
      if (recoveryRequests.current.get(runId) !== token) return;
      recoveryRequests.current.delete(runId);
      updateRecoveryLocks((value) => value + 1);
    },
  }), []);

  const bindProject = useCallback((runId, workspaceId, kind) => {
    const current = projectRef.current;
    if (current.runId === runId && current.proposalId === workspaceId && current.kind === kind) return;
    const next = { version: current.version + 1, runId, proposalId: workspaceId, kind };
    projectRef.current = next;
    snapshotRef.current = null;
    setProjectVersion(next.version);
    setSelectedRunId(runId);
    setProposalId(workspaceId);
    setSelectedProjectKind(kind);
    setSnapshot(null);
    setRefreshStates({ tasks: "idle", status: "idle" });
    Object.values(refreshTimers.current).forEach((timeout) => window.clearTimeout(timeout));
  }, []);
  const storeSnapshot = useCallback((nextSnapshot, runId) => {
    if (!nextSnapshot || nextSnapshot.run_id !== runId) return false;
    const current = snapshotRef.current;
    const currentRevision = current?.state_revision ?? current?.status?.state_revision;
    const nextRevision = nextSnapshot.state_revision ?? nextSnapshot.status?.state_revision;
    if (current?.run_id === runId && Number.isInteger(currentRevision)
      && (!Number.isInteger(nextRevision) || nextRevision < currentRevision)) return false;
    snapshotRef.current = nextSnapshot;
    setSnapshot(nextSnapshot);
    return true;
  }, []);
  const isCurrentProject = useCallback((expectedSnapshot) => projectRef.current.version === projectVersion
    && (!expectedSnapshot || expectedSnapshot === snapshotRef.current), [projectVersion]);
  const applySnapshot = useCallback((nextSnapshot, responseRunId, allowBranch = false) => {
    if (!isCurrentProject() || !nextSnapshot) return false;
    const currentRunId = projectRef.current.runId;
    const nextRunId = responseRunId || nextSnapshot.run_id;
    if (!currentRunId || !nextRunId || (nextSnapshot.run_id && nextSnapshot.run_id !== nextRunId)) return false;
    if (nextRunId !== currentRunId) {
      if (!allowBranch) return false;
      bindProject(nextRunId, nextRunId, "run");
      setAssistantMode("run");
    }
    return storeSnapshot({ ...nextSnapshot, run_id: nextRunId }, nextRunId);
  }, [bindProject, isCurrentProject, storeSnapshot]);

  const refreshWorkspace = useCallback(async (source = null) => {
    const project = projectRef.current;
    if (source) setRefreshStates((current) => ({ ...current, [source]: "refreshing" }));
    try {
      const endpoint = project.runId ? `/api/runs/${encodeURIComponent(project.runId)}/snapshot` : "/api/workspace";
      const [workspaceResponse, runsResponse] = await Promise.all([fetch(endpoint), fetch("/api/runs")]);
      const workspace = workspaceResponse.ok ? await workspaceResponse.json() : null;
      const payload = runsResponse.ok ? await runsResponse.json() : null;
      if (projectRef.current !== project) return;
      if (project.runId && workspace) {
        storeSnapshot(workspace, project.runId);
      } else if (project.version === 0 && workspace?.snapshot?.run_id) {
        bindProject(workspace.snapshot.run_id, workspace.snapshot.run_id, "run");
        storeSnapshot(workspace.snapshot, workspace.snapshot.run_id);
      }
      if (payload) {
        setRuns(payload.runs || payload || []);
        setPlans(payload.plans || []);
        setTemps(payload.temps || []);
      }
      if (source) {
        setRefreshStates((current) => ({ ...current, [source]: "done" }));
        window.clearTimeout(refreshTimers.current[source]);
        refreshTimers.current[source] = window.setTimeout(() => setRefreshStates((current) => ({ ...current, [source]: "idle" })), 1800);
      }
    } catch {
      if (source && projectRef.current === project) setRefreshStates((current) => ({ ...current, [source]: "failed" }));
    }
  }, [bindProject, storeSnapshot]);

  const ensureProposalWorkspace = useCallback(async () => {
    const project = projectRef.current;
    try {
      const response = await fetch("/api/proposals/default", { method: "POST" });
      if (!response.ok) return;
      const payload = await response.json();
      const workspaceId = payload?.workspace?.workspace_id;
      if (projectRef.current === project && typeof workspaceId === "string" && workspaceId && !project.proposalId) {
        bindProject(project.runId, workspaceId, project.kind);
        refreshWorkspace();
      }
    } catch {
      // The run assistant remains usable when the proposal workspace is unavailable.
    }
  }, [bindProject, refreshWorkspace]);

  useEffect(() => {
    refreshWorkspace().then(ensureProposalWorkspace);
    const timer = window.setInterval(refreshWorkspace, 5000);
    return () => {
      window.clearInterval(timer);
      Object.values(refreshTimers.current).forEach((timeout) => window.clearTimeout(timeout));
    };
  }, [ensureProposalWorkspace, refreshWorkspace]);
  const selectNav = (id) => { setSection(id); setDrawerOpen(true); };
  // Selecting a run also binds its proposal trace. The active assistant stays
  // stable until the user switches it, or a new pipeline explicitly starts.
  const selectRun = (runId) => { bindProject(runId, runId, "run"); };
  const selectProposal = useCallback((workspaceId) => {
    bindProject(null, workspaceId, "proposal");
    setAssistantMode("proposal");
  }, [bindProject]);
  const createTempProposal = useCallback(async () => {
    const project = projectRef.current;
    const response = await fetch("/api/proposals", { method: "POST" });
    if (!response.ok) throw new Error(await readResponseError(response));
    const payload = await response.json();
    const workspaceId = payload?.workspace?.workspace_id;
    if (typeof workspaceId !== "string" || !workspaceId) throw new Error("新建方案工作区失败");
    if (projectRef.current !== project) return;
    bindProject(null, workspaceId, "proposal");
    setAssistantMode("proposal");
    await refreshWorkspace();
  }, [bindProject, refreshWorkspace]);
  const proposalWorkspaceChanged = useCallback((workspaceId) => {
    if (!isCurrentProject()) return;
    bindProject(null, workspaceId, "proposal");
    refreshWorkspace();
  }, [bindProject, isCurrentProject, refreshWorkspace]);
  const pipelineStarted = useCallback((runId) => {
    if (!isCurrentProject()) return;
    if (runId !== projectRef.current.runId) bindProject(runId, runId, "run");
    setAssistantMode("run");
    refreshWorkspace();
  }, [bindProject, isCurrentProject, refreshWorkspace]);
  const renameRun = useCallback(async (runId, displayName) => {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/display-name`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: displayName }),
    });
    if (!response.ok) throw new Error(await readResponseError(response));
    await refreshWorkspace();
  }, [refreshWorkspace]);
  const deleteLocalItem = useCallback(async (itemId, kind, confirmPlan) => {
    const response = await fetch(`/api/local-items/${encodeURIComponent(itemId)}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_plan: confirmPlan }),
    });
    if (!response.ok) throw new Error(await readResponseError(response));
    const project = projectRef.current;
    if (kind === "run" && project.runId === itemId) {
      bindProject(null, project.proposalId === itemId ? null : project.proposalId, "proposal");
    } else if (project.proposalId === itemId) {
      bindProject(project.runId, null, project.kind);
    }
    await refreshWorkspace();
    await ensureProposalWorkspace();
  }, [bindProject, ensureProposalWorkspace, refreshWorkspace]);
  const stopRun = useCallback(async (runId) => {
    if (!isCurrentProject() || projectRef.current.runId !== runId) return {};
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/stop`, { method: "POST" });
    if (!response.ok) throw new Error(await readResponseError(response));
    const payload = await response.json();
    if (!isCurrentProject()) return payload;
    if (payload.snapshot) applySnapshot(payload.snapshot, payload.run_id);
    refreshWorkspace();
    return payload;
  }, [applySnapshot, isCurrentProject, refreshWorkspace]);

  const panelWidthLimits = useCallback((panel) => {
    const limits = PANEL_WIDTH_LIMITS[panel];
    const otherWidth = panel === "drawer"
      ? (inspectorCollapsed ? 44 : inspectorWidth)
      : (drawerOpen ? drawerWidth : 0);
    const workspaceWidth = workspaceRef.current?.clientWidth || window.innerWidth;
    return {
      ...limits,
      max: Math.max(limits.min, Math.min(limits.max, workspaceWidth - 40 - MIN_ASSISTANT_WIDTH - otherWidth)),
    };
  }, [drawerOpen, drawerWidth, inspectorCollapsed, inspectorWidth]);
  const updatePanelWidth = useCallback((panel, delta) => {
    const limits = panelWidthLimits(panel);
    const setWidth = panel === "drawer" ? setDrawerWidth : setInspectorWidth;
    setWidth((current) => clampPanelWidth(current + delta, limits));
  }, [panelWidthLimits]);
  const beginPanelResize = useCallback((panel, event) => {
    if (event.button !== 0) return;
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = panel === "drawer" ? drawerWidth : inspectorWidth;
    const direction = panel === "drawer" ? 1 : -1;
    const setWidth = panel === "drawer" ? setDrawerWidth : setInspectorWidth;
    const limits = panelWidthLimits(panel);
    const finish = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", finish);
      resizeSession.current = null;
      setIsResizing(false);
    };
    const move = (moveEvent) => {
      setWidth(clampPanelWidth(startWidth + direction * (moveEvent.clientX - startX), limits));
    };
    resizeSession.current = { move, finish };
    setIsResizing(true);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", finish, { once: true });
  }, [drawerWidth, inspectorWidth, panelWidthLimits]);
  const handleResizeKeyDown = useCallback((panel, event) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    event.preventDefault();
    const direction = event.key === "ArrowRight" ? 1 : -1;
    updatePanelWidth(panel, panel === "drawer" ? direction * 20 : direction * -20);
  }, [updatePanelWidth]);
  useEffect(() => () => {
    const session = resizeSession.current;
    if (!session) return;
    window.removeEventListener("pointermove", session.move);
    window.removeEventListener("pointerup", session.finish);
  }, []);

  return <div className="app-shell">
    <aside className="sidebar"><div className="brand"><div className="brand-glyph"><Atom size={19} /></div><div><strong>WILLY</strong><small>SCIENTIFIC WORKBENCH</small></div></div><nav className="nav-list" aria-label="主导航">{NAV_ITEMS.map(({ id, label, icon: Icon }) => <button key={id} type="button" className={`nav-item ${section === id ? "active" : ""}`} onClick={() => selectNav(id)}><Icon size={17} /><span>{label}</span>{section === id && <ChevronRight size={14} className="nav-arrow" />}</button>)}</nav><div className="sidebar-bottom"><div className="system-line"><span className="pulse" /> SYSTEM ONLINE</div><small>受控执行环境 · 本机</small></div></aside>
    <main className="main-area"><header className="topbar"><div className="mobile-brand"><button type="button" className="icon-button" title="展开详情" aria-label="展开详情" onClick={() => setDrawerOpen((value) => !value)}><Menu size={18} /></button><span>{activeLabel}</span></div><div className="breadcrumb">WILLY <span>/</span> {activeLabel?.toUpperCase()}</div><div className="top-actions"><button type="button" className="icon-button" title="Willy 简介" aria-label="Willy 简介" onClick={() => setAboutOpen(true)}><CircleHelp size={17} /></button><div className="avatar">ZL</div></div></header>
      <div ref={workspaceRef} className={`workspace-layout ${drawerOpen ? "drawer-open" : "drawer-closed"} ${inspectorCollapsed ? "inspector-collapsed" : ""} ${isResizing ? "is-resizing" : ""}`} style={{ "--drawer-width": `${drawerOpen ? drawerWidth : 0}px`, "--inspector-width": `${inspectorCollapsed ? 44 : inspectorWidth}px` }}>
        <aside className="detail-drawer" aria-hidden={!drawerOpen}><DrawerContent section={section} runs={runs} plans={plans} temps={temps} selectedRunId={selectedProjectKind === "run" ? selectedRunId : null} selectedProposalId={selectedProjectKind === "proposal" ? proposalId : null} onSelectRun={selectRun} onSelectPlan={selectProposal} onCreateTemp={createTempProposal} onRefresh={refreshWorkspace} onRename={renameRun} onDelete={deleteLocalItem} refreshState={refreshStates.tasks} /></aside><button type="button" className="drawer-toggle" title={drawerOpen ? "收起详情" : "展开详情"} aria-label={drawerOpen ? "收起详情" : "展开详情"} aria-expanded={drawerOpen} onClick={() => setDrawerOpen((value) => !value)}>{drawerOpen ? <PanelLeftClose size={16} /> : <PanelLeftOpen size={16} />}</button>
        {drawerOpen && <button type="button" className="panel-resize-handle panel-resize-handle-left" title="拖动调整本地任务栏宽度" aria-label="调整本地任务栏宽度" onPointerDown={(event) => beginPanelResize("drawer", event)} onKeyDown={(event) => handleResizeKeyDown("drawer", event)} />}
        <section className="assistant-area"><div className="assistant-tabs" role="tablist" aria-label="助理工作区">{ASSISTANT_TABS.map(({ id, label, icon: Icon }) => <button type="button" role="tab" aria-selected={assistantMode === id} className={`assistant-tab ${assistantMode === id ? "active" : ""}`} key={id} onClick={() => setAssistantMode(id)}><Icon size={16} /><span>{label}</span></button>)}</div><div className="assistant-stack"><AssistantSurface key={`proposal-${projectVersion}`} kind="proposal" active={assistantMode === "proposal"} runId={selectedRunId} proposalId={proposalId} snapshot={snapshot} onPipelineStarted={pipelineStarted} onProposalWorkspaceChanged={proposalWorkspaceChanged} onSnapshot={applySnapshot} onStop={stopRun} isCurrentProject={isCurrentProject} recoveryLock={recoveryLock} /><AssistantSurface key={`run-${projectVersion}`} kind="run" active={assistantMode === "run"} runId={selectedRunId} proposalId={null} snapshot={snapshot} onPipelineStarted={pipelineStarted} onProposalWorkspaceChanged={proposalWorkspaceChanged} onSnapshot={applySnapshot} onStop={stopRun} isCurrentProject={isCurrentProject} recoveryLock={recoveryLock} /><VisualizationSurface active={assistantMode === "visual"} runId={selectedRunId} runs={runs} /><LogsSurface active={assistantMode === "logs"} runId={selectedRunId} runs={runs} /></div></section>
        {!inspectorCollapsed && <button type="button" className="panel-resize-handle panel-resize-handle-right" title="拖动调整运行检查器宽度" aria-label="调整运行检查器宽度" onPointerDown={(event) => beginPanelResize("inspector", event)} onKeyDown={(event) => handleResizeKeyDown("inspector", event)} />}
        <StatusPanel collapsed={inspectorCollapsed} onToggle={() => setInspectorCollapsed((value) => !value)} snapshot={snapshot} onRefresh={refreshWorkspace} refreshState={refreshStates.status} />
      </div>
    </main>{aboutOpen && <WillyIntroductionDialog onClose={() => setAboutOpen(false)} />}
  </div>;
}

createRoot(document.getElementById("root")).render(<App />);
