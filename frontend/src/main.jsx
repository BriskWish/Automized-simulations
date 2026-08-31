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
      if (kind === "proposal" && typeof data.proposalId === "string" && data.proposalId !== proposalId) {
        onProposalWorkspaceChanged(data.proposalId);
      }
      const launchedRunId = data.run_id || data.runId;
      if (typeof launchedRunId === "string" && (kind === "proposal" || launchedRunId !== runId)) {
        onPipelineStarted(launchedRunId);
      }
      return { content: [{ type: "text", text: data.text || "暂时没有生成新的回复。" }] };
    },
  };
}

function AssistantMessage() {
  const eventKind = useAuiState((state) => state.message.metadata?.custom?.runActivityKind);
  const eventActive = useAuiState((state) => state.message.metadata?.custom?.runActivityActive === true);
  const messageText = useAuiState((state) => (state.message.content || [])
    .filter((part) => part?.type === "text")
    .map((part) => part.text || "")
    .join("\n"));
  const runningStep = eventKind === "status" && eventActive;
  const completedStep = eventKind === "step_completed" || eventKind === "completion_artifacts" || (eventKind === "status" && !eventActive && /已完成|全流程完成/.test(messageText));
  return <MessagePrimitive.Root className={`message assistant-message ${runningStep ? "running-step" : ""} ${completedStep ? "completed-step" : ""}`} data-role="assistant">
    <div className="message-mark">{runningStep ? <span className="step-spinner" aria-label="当前步骤进行中" /> : completedStep ? <CircleCheck size={16} aria-label="步骤已完成" /> : <Sparkles size={15} />}</div>
    <div className="message-copy"><MessagePrimitive.Parts /></div>
  </MessagePrimitive.Root>;
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
    <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => applyCommand("/resume")}><code>/resume</code><span>从安全步骤继续</span></button>
    <button type="button" onMouseDown={(event) => event.preventDefault()} onClick={() => applyCommand("/switch")}><code>/switch</code><span>切换工程对话</span></button>
  </div>;
}

function StructureUploadButton({ proposalId, onResult, onFailure }) {
  const fileInput = useRef(null);
  const [uploading, setUploading] = useState(false);
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
      onResult(payload);
    } catch (error) {
      onFailure(error instanceof Error ? error.message : "上传失败");
    } finally {
      setUploading(false);
    }
  };
  return <><input ref={fileInput} className="visually-hidden" type="file" accept=".gjf,.inp" onChange={upload} /><button type="button" className="upload-button" disabled={uploading} onClick={() => fileInput.current?.click()} title="上传结构"><Upload className={uploading ? "spin" : ""} size={15} /><span>{uploading ? "上传中" : "上传结构"}</span></button></>;
}

function WorkbenchThread({ kind, active, proposalId }) {
  const aui = useAui();
  const [slashOpen, setSlashOpen] = useState(false);
  const tailId = useAuiState((state) => state.thread.messages.at(-1)?.id || null);
  const inputId = `${kind}-composer-input`;
  const isRun = kind === "run";
  const appendUploadFailure = useCallback((text) => {
    void aui.thread.append({
      parentId: tailId,
      sourceId: null,
      runConfig: {},
      startRun: false,
      role: "user",
      content: [{ type: "text", text }],
      attachments: [],
      metadata: { custom: {} },
      createdAt: new Date(),
    }).catch(() => undefined);
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
        <ThreadPrimitive.Messages components={{ UserMessage, AssistantMessage }} />
        {!isRun && <ProposalThinkingIndicator />}
        <ThreadPrimitive.ViewportFooter className="thread-footer">
          <ComposerPrimitive.Root className="composer">
            <ComposerPrimitive.Input
              id={inputId}
              className="composer-input"
              placeholder={isRun ? RUN_PLACEHOLDER : PROPOSAL_PLACEHOLDER}
              rows={1}
              autoFocus={active}
              onInput={(event) => setSlashOpen(isRun && event.currentTarget.value.trimStart().startsWith("/"))}
            />
            {slashOpen && <SlashMenu inputId={inputId} onClose={() => setSlashOpen(false)} />}
            <div className="composer-actions">
              {!isRun && <StructureUploadButton proposalId={proposalId} onResult={applyUploadResult} onFailure={appendUploadFailure} />}
              <span className="composer-hint">Enter 发送 · Shift+Enter 换行</span>
              <ComposerPrimitive.Send asChild>
                <button type="button" className="send-button" title="发送" aria-label="发送"><Send size={17} /></button>
              </ComposerPrimitive.Send>
            </div>
          </ComposerPrimitive.Root>
        </ThreadPrimitive.ViewportFooter>
      </div>
    </ThreadPrimitive.Viewport>
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
        const messages = Array.isArray(payload.messages) ? payload.messages : [];
        aui.thread.reset(threadHistoryMessages(messages));
        if (payload.snapshot) onSnapshot(payload.snapshot);
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
        if (disposed) return;
        if (data.snapshot) onSnapshot(data.snapshot);
        const incoming = data.events || data.messages || [];
        if (incoming.length) {
          const historyResponse = await fetch(`/api/runs/${encodeURIComponent(runId)}/chat`);
          const historyPayload = historyResponse.ok ? await historyResponse.json() : null;
          if (!disposed && historyPayload?.run_id === runId) {
            aui.thread.reset(threadHistoryMessages(Array.isArray(historyPayload.messages) ? historyPayload.messages : []));
            if (historyPayload.snapshot) onSnapshot(historyPayload.snapshot);
          }
        }
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

function PendingActionConfirmation({ runId, snapshot, onSnapshot }) {
  const aui = useAui();
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const state = String(snapshot?.state || "").toLowerCase();
  const action = snapshot?.pending_action;
  const waiting = state === "awaiting_confirmation" && Boolean(runId)
    && action && typeof action.action_id === "string"
    && Number.isInteger(action.state_revision)
    && typeof action.config_fingerprint === "string";
  useEffect(() => { setMessage(""); setSubmitting(false); }, [runId, action?.action_id, action?.state_revision, state]);
  if (!waiting) return null;
  const confirm = async () => {
    setSubmitting(true);
    setMessage("");
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/pending-action/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action_id: action.action_id,
          state_revision: action.state_revision,
          config_fingerprint: action.config_fingerprint,
        }),
      });
      if (!response.ok) throw new Error(await readResponseError(response));
      const payload = await response.json();
      if (Array.isArray(payload.messages)) aui.thread.reset(threadHistoryMessages(payload.messages));
      if (payload.snapshot) onSnapshot(payload.snapshot);
      setMessage(payload.text || "已提交确认请求。");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "确认请求未送达，请刷新后重试。");
    } finally {
      setSubmitting(false);
    }
  };
  return <div className="pending-action-confirmation"><div><ShieldCheck size={15} /><span>待确认调参</span></div><button type="button" disabled={submitting} onClick={confirm}><Play size={13} />{submitting ? "正在确认" : "确认调参并重跑"}</button>{message && <span className="pending-action-confirmation-message" aria-live="polite">{message}</span>}</div>;
}

function AssistantSurface({ kind, active, runId, proposalId, snapshot, onPipelineStarted, onProposalWorkspaceChanged, onSnapshot, onStop }) {
  const adapter = useMemo(
    () => createChatAdapter(kind, runId, proposalId, onPipelineStarted, onProposalWorkspaceChanged),
    [kind, runId, proposalId, onPipelineStarted, onProposalWorkspaceChanged],
  );
  const runtime = useLocalRuntime(adapter, { unstable_enableMessageQueue: true });
  return <AssistantRuntimeProvider runtime={runtime}>
    <section className={`assistant-surface ${active ? "active" : ""}`} aria-hidden={!active}>
      {kind === "proposal" && <ProposalHistoryLoader proposalId={proposalId} />}
      {kind === "run" && <RunHistoryLoader runId={runId} onSnapshot={onSnapshot} />}
      {kind === "run" && <RunHistorySync runId={runId} active={active} onSnapshot={onSnapshot} />}
      {kind === "run" && <PendingActionConfirmation runId={runId} snapshot={snapshot} onSnapshot={onSnapshot} />}
      {kind === "run" && <RunStopControl runId={runId} snapshot={snapshot} onStop={onStop} />}
      <WorkbenchThread kind={kind} active={active} proposalId={proposalId} />
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
  const [records, setRecords] = useState(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const loadRecords = useCallback(async () => {
    if (!runId) {
      setRecords(null);
      setError("");
      return;
    }
    setRefreshing(true);
    try {
      const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/logs`);
      if (!response.ok) throw new Error(await readResponseError(response));
      setRecords(await response.json());
      setError("");
    } catch (loadError) {
      setRecords(null);
      setError(loadError instanceof Error ? loadError.message : "日志暂时无法读取");
    } finally {
      setRefreshing(false);
    }
  }, [runId]);
  useEffect(() => {
    if (active) loadRecords();
  }, [active, loadRecords]);
  const currentRun = runs.find((item) => item.run_id === runId);
  const runName = currentRun?.display_name || runId || "暂无工程";
  return <section className={`assistant-surface logs-surface ${active ? "active" : ""}`} aria-hidden={!active}>
    <div className="logs-heading"><div><span className="eyebrow">AUDIT RECORDS</span><h3>{runName}</h3></div><button type="button" className="icon-button" title="刷新日志" aria-label="刷新日志" disabled={!runId || refreshing} onClick={loadRecords}><RefreshCw className={refreshing ? "spin" : ""} size={16} /></button></div>
    {!runId ? <div className="logs-empty"><ClipboardList size={30} /><p>选择一个工程以查看其记录。</p></div> : error ? <div className="logs-empty"><p>{error}</p></div> : <div className="logs-grid">{[["manifest", "Manifest"], ["events", "Events"]].map(([key, label]) => { const record = records?.[key]; return <article className="log-column" key={key}><header><strong>{label}</strong><code>{record?.filename || "读取中"}</code></header><pre aria-label={`${label} 内容`}>{record?.content || "正在读取记录..."}</pre></article>; })}</div>}
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
      <div className="inspector-section"><div className="section-label"><ShieldCheck size={14} /> 受控边界</div><div className="check-row"><span>输入契约</span><b className="ok">已检查</b></div><div className="check-row"><span>资源预检</span><b className="ok">已登记</b></div><div className="check-row"><span>记录文件</span><b className="ok">manifest / events</b></div></div>
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

function Dialog({ label, children, onClose, className = "" }) {
  return <div className="dialog-backdrop" role="presentation">
    <section className={`dialog-card ${className}`} role="dialog" aria-modal="true" aria-label={label}>
      <button type="button" className="dialog-close" title="关闭" aria-label="关闭" onClick={onClose}><X size={17} /></button>
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
      setStatus((current) => ({ ...current, [action]: payload.message || payload.summary || payload.detail || "操作已完成。" }));
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
  const [aboutOpen, setAboutOpen] = useState(false);
  const [refreshStates, setRefreshStates] = useState({ tasks: "idle", status: "idle" });
  const refreshTimers = useRef({});
  const resizeSession = useRef(null);
  const workspaceRef = useRef(null);
  const activeLabel = useMemo(() => NAV_ITEMS.find((item) => item.id === section)?.label, [section]);

  const refreshWorkspace = useCallback(async (source = null) => {
    if (source) setRefreshStates((current) => ({ ...current, [source]: "refreshing" }));
    try {
      const [workspaceResponse, runsResponse] = await Promise.all([fetch("/api/workspace"), fetch("/api/runs")]);
      if (workspaceResponse.ok) {
        const workspace = await workspaceResponse.json();
        setSnapshot(workspace.snapshot || null);
        setSelectedRunId((current) => current || workspace.snapshot?.run_id || null);
      }
      if (runsResponse.ok) {
        const payload = await runsResponse.json();
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
      if (source) setRefreshStates((current) => ({ ...current, [source]: "failed" }));
    }
  }, []);

  const ensureProposalWorkspace = useCallback(async () => {
    try {
      const response = await fetch("/api/proposals/default", { method: "POST" });
      if (!response.ok) return;
      const payload = await response.json();
      const workspaceId = payload?.workspace?.workspace_id;
      if (typeof workspaceId === "string" && workspaceId) {
        setProposalId((current) => current || workspaceId);
        refreshWorkspace();
      }
    } catch {
      // The run assistant remains usable when the proposal workspace is unavailable.
    }
  }, [refreshWorkspace]);

  useEffect(() => {
    refreshWorkspace();
    ensureProposalWorkspace();
    const timer = window.setInterval(refreshWorkspace, 5000);
    return () => {
      window.clearInterval(timer);
      Object.values(refreshTimers.current).forEach((timeout) => window.clearTimeout(timeout));
    };
  }, [ensureProposalWorkspace, refreshWorkspace]);
  const selectNav = (id) => { setSection(id); setDrawerOpen(true); };
  // Selecting a run also binds its proposal trace. The active assistant stays
  // stable until the user switches it, or a new pipeline explicitly starts.
  const selectRun = (runId) => { setSelectedRunId(runId); setProposalId(runId); setSelectedProjectKind("run"); };
  const selectProposal = useCallback((workspaceId) => {
    setProposalId(workspaceId);
    setSelectedProjectKind("proposal");
    setAssistantMode("proposal");
  }, []);
  const createTempProposal = useCallback(async () => {
    const response = await fetch("/api/proposals", { method: "POST" });
    if (!response.ok) throw new Error(await readResponseError(response));
    const payload = await response.json();
    const workspaceId = payload?.workspace?.workspace_id;
    if (typeof workspaceId !== "string" || !workspaceId) throw new Error("新建方案工作区失败");
    setProposalId(workspaceId);
    setSelectedProjectKind("proposal");
    setAssistantMode("proposal");
    await refreshWorkspace();
  }, [refreshWorkspace]);
  const proposalWorkspaceChanged = useCallback((workspaceId) => {
    setProposalId(workspaceId);
    setSelectedProjectKind("proposal");
    refreshWorkspace();
  }, [refreshWorkspace]);
  const pipelineStarted = useCallback((runId) => { setSelectedRunId(runId); setProposalId(runId); setSelectedProjectKind("run"); setAssistantMode("run"); refreshWorkspace(); }, [refreshWorkspace]);
  const applySnapshot = useCallback((nextSnapshot) => { setSnapshot(nextSnapshot); if (nextSnapshot?.run_id) { setSelectedRunId(nextSnapshot.run_id); setProposalId(nextSnapshot.run_id); setSelectedProjectKind("run"); } }, []);
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
    if (kind === "run") {
      setSelectedRunId((current) => current === itemId ? null : current);
    }
    setProposalId((current) => current === itemId ? null : current);
    await refreshWorkspace();
    await ensureProposalWorkspace();
  }, [ensureProposalWorkspace, refreshWorkspace]);
  const stopRun = useCallback(async (runId) => {
    const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/stop`, { method: "POST" });
    if (!response.ok) throw new Error(await readResponseError(response));
    const payload = await response.json();
    if (payload.snapshot) applySnapshot(payload.snapshot);
    refreshWorkspace();
    return payload;
  }, [applySnapshot, refreshWorkspace]);

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
        <section className="assistant-area"><div className="assistant-tabs" role="tablist" aria-label="助理工作区">{ASSISTANT_TABS.map(({ id, label, icon: Icon }) => <button type="button" role="tab" aria-selected={assistantMode === id} className={`assistant-tab ${assistantMode === id ? "active" : ""}`} key={id} onClick={() => setAssistantMode(id)}><Icon size={16} /><span>{label}</span></button>)}</div><div className="assistant-stack"><AssistantSurface kind="proposal" active={assistantMode === "proposal"} runId={selectedRunId} proposalId={proposalId} snapshot={snapshot} onPipelineStarted={pipelineStarted} onProposalWorkspaceChanged={proposalWorkspaceChanged} onSnapshot={applySnapshot} onStop={stopRun} /><AssistantSurface kind="run" active={assistantMode === "run"} runId={selectedRunId} proposalId={null} snapshot={snapshot} onPipelineStarted={pipelineStarted} onProposalWorkspaceChanged={proposalWorkspaceChanged} onSnapshot={applySnapshot} onStop={stopRun} /><VisualizationSurface active={assistantMode === "visual"} runId={selectedRunId} runs={runs} /><LogsSurface active={assistantMode === "logs"} runId={selectedRunId} runs={runs} /></div></section>
        {!inspectorCollapsed && <button type="button" className="panel-resize-handle panel-resize-handle-right" title="拖动调整运行检查器宽度" aria-label="调整运行检查器宽度" onPointerDown={(event) => beginPanelResize("inspector", event)} onKeyDown={(event) => handleResizeKeyDown("inspector", event)} />}
        <StatusPanel collapsed={inspectorCollapsed} onToggle={() => setInspectorCollapsed((value) => !value)} snapshot={snapshot} onRefresh={refreshWorkspace} refreshState={refreshStates.status} />
      </div>
    </main>{aboutOpen && <WillyIntroductionDialog onClose={() => setAboutOpen(false)} />}
  </div>;
}

createRoot(document.getElementById("root")).render(<App />);
