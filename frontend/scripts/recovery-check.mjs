import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { parse } from "@babel/parser";
import { transformSync } from "esbuild";

const source = readFileSync(new URL("../src/main.jsx", import.meta.url), "utf8");
const imports = parse(source, { sourceType: "module", plugins: ["jsx"] }).program.body
  .filter((node) => node.type === "ImportDeclaration");
let executable = source;
for (const declaration of [...imports].reverse()) {
  executable = executable.slice(0, declaration.start) + executable.slice(declaration.end);
}
executable += "\nglobalThis.components = { App, PendingActionConfirmation, recoveryContext };";

let active;
const sameDeps = (left, right) => left && right && left.length === right.length
  && left.every((value, index) => Object.is(value, right[index]));

class Harness {
  constructor(component, props = {}) {
    this.component = component;
    this.props = props;
    this.slots = [];
    this.effects = [];
  }

  render(props = this.props) {
    this.props = props;
    this.index = 0;
    const previous = active;
    active = this;
    try {
      this.tree = this.component(props);
      return this.tree;
    } finally {
      active = previous;
    }
  }

  flushEffects() {
    for (const effect of this.effects.splice(0)) {
      effect.slot.cleanup?.();
      effect.slot.cleanup = effect.callback();
    }
  }

  unmount() {
    for (const slot of this.slots) slot?.cleanup?.();
  }
}

const useState = (initial) => {
  const owner = active;
  const index = owner.index++;
  const slot = owner.slots[index] ??= { value: typeof initial === "function" ? initial() : initial };
  return [slot.value, (next) => { slot.value = typeof next === "function" ? next(slot.value) : next; }];
};
const useRef = (initial) => active.slots[active.index++] ??= { current: initial };
const useMemo = (callback, deps) => {
  const index = active.index++;
  const old = active.slots[index];
  if (old && sameDeps(old.deps, deps)) return old.value;
  const slot = { value: callback(), deps };
  active.slots[index] = slot;
  return slot.value;
};
const useEffect = (callback, deps) => {
  const index = active.index++;
  const slot = active.slots[index] ??= {};
  if (!sameDeps(slot.deps, deps)) {
    slot.deps = deps;
    active.effects.push({ slot, callback });
  }
};

const histories = [];
let fetcher = () => { throw new Error("Unexpected network call"); };
const context = Object.fromEntries(imports.flatMap((node) => node.specifiers
  .map((specifier) => [specifier.local.name, () => null])));
Object.assign(context, {
  React: {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
    Fragment: "fragment",
  },
  useState, useRef, useMemo, useEffect,
  useCallback: (callback, deps) => useMemo(() => callback, deps),
  useAui: () => ({ thread: { reset: (messages) => histories.push(messages) } }),
  useAuiState: (selector) => selector({ thread: { isRunning: false } }),
  createRoot: () => ({ render() {} }),
  document: { getElementById: () => ({}) },
  window: {
    innerWidth: 1440,
    setInterval: () => 0,
    clearInterval() {},
    setTimeout: () => 0,
    clearTimeout() {},
    addEventListener() {},
    removeEventListener() {},
  },
  fetch: (...args) => fetcher(...args),
  console,
});
vm.runInNewContext(transformSync(executable, { loader: "jsx", format: "cjs" }).code, context);
const { App, PendingActionConfirmation, recoveryContext } = context.components;

const allNodes = (tree) => Array.isArray(tree) ? tree.flatMap(allNodes)
  : tree && typeof tree === "object" ? [tree, ...allNodes(tree.props?.children || [])] : [];
const text = (tree) => Array.isArray(tree) ? tree.map(text).join("")
  : tree && typeof tree === "object" ? text(tree.props?.children || [])
    : tree == null || typeof tree === "boolean" ? "" : String(tree);
const find = (harness, predicate) => allNodes(harness.tree).find(predicate);
const button = (harness, name) => find(harness, (node) => node.type === "button" && text(node) === name);
const respond = (json, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => json });
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};
const settle = async () => {
  await new Promise(setImmediate);
  await new Promise(setImmediate);
};
const snapshot = (runId = "parent", revision = 1, state = "awaiting_confirmation", pending = true) => ({
  run_id: runId,
  state,
  state_revision: revision,
  pending_action: pending ? {
    run_id: runId,
    action_id: `action-${revision}`,
    state_revision: revision,
    config_fingerprint: `fingerprint-${revision}`,
    summary: `建议 ${revision}`,
    adjustments: [{ field: "dt", before: 0.002, after: 0.001 }],
  } : null,
});
const runProps = (app) => find(app, (node) => node.type?.name === "AssistantSurface" && node.props.kind === "run").props;
const drawer = (app) => find(app, (node) => node.type?.name === "DrawerContent").props;

function setup(initial = snapshot()) {
  const app = new Harness(App);
  app.render();
  drawer(app).onSelectRun(initial.run_id);
  app.render();
  assert.equal(runProps(app).onSnapshot(initial, initial.run_id), true);
  app.render();
  const controls = new Harness(PendingActionConfirmation, runProps(app));
  controls.render();
  controls.flushEffects();
  const sync = () => {
    app.render();
    controls.render(runProps(app));
    controls.flushEffects();
  };
  return { app, controls, sync };
}

for (const state of ["awaiting", "running", "retrying", "awaiting_confirmation", "stopping", "aborted", "done", "escalated"]) {
  const fixture = setup(snapshot("parent", 1, state, false));
  assert.equal(Boolean(button(fixture.controls, "原参数续跑")), ["awaiting_confirmation", "aborted", "escalated"].includes(state), state);
}
assert.equal(recoveryContext("other", snapshot()).recoverable, false);
const invalid = snapshot();
invalid.pending_action.state_revision = 0;
assert.equal(recoveryContext("parent", invalid).action, null);
console.log("PASS 八状态可见性、无建议续跑、过期方案校验");

{
  const fixture = setup(snapshot("parent", 4, "escalated", false));
  const calls = [];
  const pending = deferred();
  fetcher = (url, options) => { calls.push({ url, options }); return pending.promise; };
  const click = button(fixture.controls, "原参数续跑").props.onClick;
  const first = click();
  const second = click();
  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(calls[0].options.body), { state_revision: 4 });
  assert.ok(calls[0].url.endsWith("/parent/resume"));
  pending.resolve(respond({ run_id: "parent", snapshot: snapshot("parent", 5, "retrying", false), messages: [] }));
  await Promise.all([first, second]);
  fixture.sync();
  assert.equal(runProps(fixture.app).runId, "parent");
  assert.equal(button(fixture.controls, "原参数续跑"), undefined);
  console.log("PASS 原参数仅提交 revision、同步防双击、同 run 续跑");
}

{
  const fixture = setup();
  const calls = [];
  const pending = deferred();
  fetcher = (url, options) => { calls.push({ url, options }); return pending.promise; };
  const oldConfirm = button(fixture.controls, "确认方案并创建分支").props.onClick;
  button(fixture.controls, "修改方案").props.onClick();
  fixture.sync();
  find(fixture.controls, (node) => node.type === "textarea").props.onChange({ target: { value: "  减小时间步长  " } });
  fixture.sync();
  find(fixture.controls, (node) => node.type === "form").props.onSubmit({ preventDefault() {} });
  await oldConfirm();
  fixture.sync();
  assert.equal(calls.length, 1);
  assert.equal(button(fixture.controls, "确认方案并创建分支").props.disabled, true);
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    action_id: "action-1", state_revision: 1, config_fingerprint: "fingerprint-1", request: "减小时间步长",
  });
  runProps(fixture.app).onSnapshot(snapshot("parent", 2), "parent");
  fixture.sync();
  await oldConfirm();
  assert.equal(calls.length, 1);
  assert.equal(button(fixture.controls, "取消修改").props.disabled, true);
  pending.resolve(respond({
    run_id: "parent", snapshot: snapshot("parent", 2), messages: [{ role: "assistant", content: "建议 2" }],
  }));
  await settle();
  fixture.sync();
  assert.equal(calls.length, 1);
  assert.match(text(fixture.controls.tree), /建议 2/);
  assert.match(text(fixture.controls.tree), /尚未启动/);
  const childResponse = snapshot("child", 0, "retrying", false);
  fetcher = async (url, options) => {
    calls.push({ url, options });
    return respond({ run_id: "child", snapshot: childResponse, messages: [] });
  };
  await button(fixture.controls, "确认方案并创建分支").props.onClick();
  fixture.app.render();
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    action_id: "action-2", state_revision: 2, config_fingerprint: "fingerprint-2",
  });
  assert.equal(runProps(fixture.app).runId, "child");
  assert.equal(runProps(fixture.app).snapshot.run_id, "child");
  console.log("PASS 修改在途禁确认、旧闭包读取最新值、修改不启动、新方案确认绑定子 run");
}

{
  const sameAction = snapshot("parent", 2);
  sameAction.pending_action.action_id = "action-1";
  const sameRevision = snapshot();
  sameRevision.pending_action.action_id = "replacement-action";
  const cases = [
    { snapshot: snapshot(), text: "LLM 暂不可用，原方案已保留。", updated: false },
    { snapshot: snapshot(), text: "修改方案未通过校验，请补充约束。", updated: false },
    { snapshot: snapshot(), text: "", updated: false },
    { snapshot: sameAction, text: "", updated: false },
    { snapshot: sameRevision, text: "", updated: false },
    { snapshot: snapshot("parent", 2), text: "已按要求生成新方案，请先核对参数。", updated: true },
  ];
  for (const result of cases) {
    const fixture = setup();
    const draft = "  改为更保守的时间步长  ";
    const calls = [];
    fetcher = async (url, options) => {
      calls.push({ url, options });
      return respond({ run_id: "parent", snapshot: result.snapshot, text: result.text, messages: [] });
    };
    button(fixture.controls, "修改方案").props.onClick();
    fixture.sync();
    find(fixture.controls, (node) => node.type === "textarea").props.onChange({ target: { value: draft } });
    fixture.sync();
    find(fixture.controls, (node) => node.type === "form").props.onSubmit({ preventDefault() {} });
    await settle();
    fixture.sync();
    assert.equal(calls.length, 1);
    assert.ok(calls[0].url.endsWith("/parent/pending-action/revise"));
    assert.equal(JSON.parse(calls[0].options.body).request, draft.trim());
    const message = find(fixture.controls, (node) => node.props?.role === "status");
    assert.equal(text(message), result.text || "方案未更新，修改内容已保留，请调整要求后重试。");
    const editor = find(fixture.controls, (node) => node.type === "textarea");
    if (result.updated) {
      assert.equal(editor, undefined);
      assert.equal(button(fixture.controls, "确认方案并创建分支").props.disabled, false);
      button(fixture.controls, "修改方案").props.onClick();
      fixture.sync();
      assert.equal(find(fixture.controls, (node) => node.type === "textarea").props.value, "");
    } else {
      assert.equal(editor.props.value, draft);
      assert.equal(editor.props.disabled, false);
      assert.equal(button(fixture.controls, "提交修改").props.disabled, false);
      assert.equal(button(fixture.controls, "确认方案并创建分支").props.disabled, true);
    }
    assert.equal(runProps(fixture.app).runId, "parent");
    assert.equal(runProps(fixture.app).recoveryLock.isBusy("parent"), false);
  }
  console.log("PASS revise 200 优先展示服务端文本，仅新 action 且 revision 增大才清空草稿");
}

{
  const fixture = setup();
  const calls = [];
  fetcher = async (url, options) => {
    calls.push({ url, options });
    return options?.method === "POST" ? respond({ detail: "stale" }, 409) : respond(snapshot("parent", 2));
  };
  await button(fixture.controls, "确认方案并创建分支").props.onClick();
  fixture.sync();
  assert.equal(calls.length, 2);
  assert.equal(calls.filter((call) => call.options?.method === "POST").length, 1);
  assert.ok(calls[1].url.endsWith("/parent/snapshot"));
  assert.match(text(fixture.controls.tree), /未继续提交旧方案/);
  assert.equal(runProps(fixture.app).snapshot.pending_action.action_id, "action-2");
  assert.equal(runProps(fixture.app).onSnapshot(snapshot("parent", 1), "parent"), false);
  fixture.sync();
  assert.equal(runProps(fixture.app).snapshot.state_revision, 2);
  console.log("PASS 409 刷新最新 snapshot 不重试，旧轮询不能回退 revision");
}

{
  const fixture = setup();
  fetcher = async (url, options) => options?.method === "POST" ? respond({}, 409) : respond({}, 503);
  await button(fixture.controls, "确认方案并创建分支").props.onClick();
  fixture.sync();
  assert.equal(button(fixture.controls, "确认方案并创建分支").props.disabled, true);
  assert.equal(button(fixture.controls, "原参数续跑").props.disabled, true);
  assert.ok(button(fixture.controls, "刷新状态"));
  fetcher = async () => respond(snapshot("parent", 2));
  await button(fixture.controls, "刷新状态").props.onClick();
  fixture.sync();
  assert.equal(button(fixture.controls, "确认方案并创建分支").props.disabled, false);
  console.log("PASS 冲突刷新失败保持禁用，显式刷新后恢复");
}

{
  const fixture = setup();
  const pending = deferred();
  const historyCount = histories.length;
  fetcher = () => pending.promise;
  const oldProps = runProps(fixture.app);
  const confirmation = button(fixture.controls, "确认方案并创建分支").props.onClick();
  drawer(fixture.app).onSelectRun("other");
  fixture.app.render();
  runProps(fixture.app).onSnapshot(snapshot("other", 6, "aborted", false), "other");
  fixture.app.render();
  fixture.controls.unmount();
  pending.resolve(respond({ run_id: "old-child", snapshot: snapshot("old-child", 0, "retrying", false), messages: [] }));
  await confirmation;
  fixture.app.render();
  assert.equal(runProps(fixture.app).runId, "other");
  assert.equal(runProps(fixture.app).snapshot.run_id, "other");
  assert.equal(histories.length, historyCount);
  assert.equal(oldProps.onSnapshot(snapshot(), "parent", true), false);
  drawer(fixture.app).onSelectPlan("new-proposal");
  fixture.app.render();
  assert.equal(runProps(fixture.app).runId, null);
  assert.equal(runProps(fixture.app).snapshot, null);
  assert.equal(oldProps.onSnapshot(snapshot(), "parent", true), false);
  console.log("PASS 切换运行或方案工作区后，旧确认/快照/消息不污染新工程");
}

{
  const fixture = setup();
  const pending = deferred();
  let posts = 0;
  fetcher = () => { posts++; return pending.promise; };
  const first = button(fixture.controls, "确认方案并创建分支").props.onClick();
  drawer(fixture.app).onSelectRun("other");
  fixture.app.render();
  drawer(fixture.app).onSelectRun("parent");
  fixture.app.render();
  runProps(fixture.app).onSnapshot(snapshot(), "parent");
  fixture.app.render();
  fixture.controls.unmount();
  const revisited = new Harness(PendingActionConfirmation, runProps(fixture.app));
  revisited.render();
  revisited.flushEffects();
  assert.equal(button(revisited, "确认方案并创建分支").props.disabled, true);
  await button(revisited, "确认方案并创建分支").props.onClick();
  assert.equal(posts, 1);
  pending.resolve(respond({ run_id: "old-child", snapshot: snapshot("old-child", 0, "retrying", false), messages: [] }));
  await first;
  fixture.app.render();
  assert.equal(runProps(fixture.app).runId, "parent");
  assert.equal(runProps(fixture.app).recoveryLock.isBusy("parent"), false);
  console.log("PASS A→B→A 切换后仍保留同 run 请求锁，旧响应不重绑");
}

{
  const fixture = setup();
  const calls = [];
  const pending = deferred();
  fetcher = (url) => {
    calls.push(url);
    return url === "/api/runs" ? Promise.resolve(respond({ runs: [] })) : pending.promise;
  };
  const refresh = drawer(fixture.app).onRefresh();
  assert.equal(calls[0], "/api/runs/parent/snapshot");
  drawer(fixture.app).onSelectRun("other");
  fixture.app.render();
  runProps(fixture.app).onSnapshot(snapshot("other", 9, "aborted", false), "other");
  fixture.app.render();
  pending.resolve(respond(snapshot("parent", 50)));
  await refresh;
  fixture.app.render();
  assert.equal(runProps(fixture.app).snapshot.run_id, "other");
  console.log("PASS 工作区刷新读取选中 run，跨工程迟到响应被丢弃");
}

{
  const multiple = snapshot();
  multiple.pending_action.selection_required = true;
  const fixture = setup(multiple);
  assert.equal(button(fixture.controls, "确认方案并创建分支").props.disabled, true);
  assert.equal(button(fixture.controls, "原参数续跑").props.disabled, false);
  assert.match(text(fixture.controls.tree), /先选择方案/);
  fetcher = () => { throw new Error("Selection-required confirmation must not submit"); };
  await button(fixture.controls, "确认方案并创建分支").props.onClick();
  const previous = runProps(fixture.app);
  assert.equal(previous.onSnapshot(snapshot("unrelated"), "unrelated"), false);
  assert.equal(previous.onSnapshot(snapshot("child"), "mismatch", true), false);
  const noSnapshotRunId = snapshot("child", 0, "retrying", false);
  delete noSnapshotRunId.run_id;
  assert.equal(previous.onSnapshot(noSnapshotRunId, "child", true), true);
  fixture.app.render();
  assert.equal(runProps(fixture.app).runId, "child");
  console.log("PASS selection_required 禁确认，响应 run_id 回退和不一致校验");
}

{
  const failed = snapshot("parent", 7, "escalated", false);
  failed.fault = { fault_id: "0123456789abcdef01234567", step: 8, phase: "execution", resolution: "pending" };
  failed.fault_review = { manual_completion_allowed: true, reason: "manual_review_required" };
  const fixture = setup(failed);
  const calls = [];
  fetcher = (url, options) => {
    calls.push(url);
    assert.equal(url, "/api/runs/parent/fault/complete");
    assert.deepEqual(JSON.parse(options.body), { state_revision: 7, fault_id: failed.fault.fault_id });
    const resolved = snapshot("parent", 8, "aborted", false);
    resolved.fault = { ...failed.fault, resolution: "resolved" };
    return Promise.resolve(respond({ run_id: "parent", snapshot: resolved, messages: [] }));
  };
  await button(fixture.controls, "已完成人工处理，复查").props.onClick();
  fixture.sync();
  assert.equal(calls.length, 1);
  assert.equal(runProps(fixture.app).runId, "parent");
  assert.equal(runProps(fixture.app).snapshot.state, "aborted");
  assert.equal(button(fixture.controls, "已完成人工处理，复查"), undefined);
  assert.equal(button(fixture.controls, "原参数续跑").props.disabled, false);
  const blocked = setup({ ...failed, fault_review: { manual_completion_allowed: false, reason: "process_exit_unconfirmed" } });
  assert.equal(button(blocked.controls, "原参数续跑").props.disabled, true);
  assert.equal(button(blocked.controls, "已完成人工处理，复查"), undefined);
  await button(blocked.controls, "原参数续跑").props.onClick();
  assert.equal(calls.length, 1);
  console.log("PASS 人工完成绑定故障及版本，仅回到 aborted；残留进程禁续跑和声明");
}

console.log("All 11 frontend recovery fixture groups passed (mock hooks + VM + mock fetch; no browser or scientific process).");
