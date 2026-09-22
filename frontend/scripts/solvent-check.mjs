import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { parse } from "@babel/parser";
import { transformSync } from "esbuild";

const source = readFileSync(new URL("../src/main.jsx", import.meta.url), "utf8");
const imports = parse(source, { sourceType: "module", plugins: ["jsx"] }).program.body
  .filter((node) => node.type === "ImportDeclaration");
let executable = source;
for (const declaration of [...imports].reverse()) {
  executable = executable.slice(0, declaration.start) + executable.slice(declaration.end);
}
executable += "\nglobalThis.components = { MoleculeCatalogDialog, MoleculeRecord, SolventCatalogDialog, SolventRecord, WorkbenchThread, solventRegistrationPayload, parseProposalPlan, ProposalPlanOverview, AssistantCompletionSync };";

let active;
const sameDeps = (left, right) => left && right && left.length === right.length
  && left.every((value, index) => Object.is(value, right[index]));

class Harness {
  constructor(component, props = {}) {
    this.component = component;
    this.props = props;
    this.slots = [];
    this.effects = [];
    this.disposed = false;
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

  flush() {
    for (const effect of this.effects.splice(0)) {
      effect.slot.cleanup?.();
      effect.slot.cleanup = effect.callback();
    }
  }

  unmount() {
    for (const slot of this.slots) slot?.cleanup?.();
    this.disposed = true;
  }
}

const useState = (initial) => {
  const owner = active;
  const slot = owner.slots[owner.index++] ??= { value: typeof initial === "function" ? initial() : initial };
  return [slot.value, (value) => {
    assert.equal(owner.disposed, false, "unmounted dialog must ignore late responses");
    slot.value = typeof value === "function" ? value(slot.value) : value;
  }];
};
const useRef = (initial) => active.slots[active.index++] ??= { current: initial };
const useMemo = (callback, deps) => {
  const index = active.index++;
  const old = active.slots[index];
  if (old && sameDeps(old.deps, deps)) return old.value;
  active.slots[index] = { value: callback(), deps };
  return active.slots[index].value;
};
const useEffect = (callback, deps) => {
  const slot = active.slots[active.index++] ??= {};
  if (!sameDeps(slot.deps, deps)) {
    slot.deps = deps;
    active.effects.push({ slot, callback });
  }
};
let fetcher = () => { throw new Error("Unexpected fetch"); };
const calls = [];
const listeners = new Set();
let focusRestored = 0;
const documentMock = {
  activeElement: { isConnected: true, focus: () => { focusRestored += 1; } },
  getElementById: () => ({}),
  addEventListener: (name, callback) => { if (name === "keydown") listeners.add(callback); },
  removeEventListener: (name, callback) => { if (name === "keydown") listeners.delete(callback); },
};
const forbiddenSideEffect = () => { throw new Error("Solvent registration must not submit chat, launch, or alter a plan"); };
const context = Object.fromEntries(imports.flatMap((node) => node.specifiers
  .map((specifier) => [specifier.local.name, () => null])));
Object.assign(context, {
  React, Error, AbortController, console,
  useState, useRef, useMemo, useEffect,
  useCallback: (callback, deps) => useMemo(() => callback, deps),
  useAui: () => ({ thread: { append: forbiddenSideEffect, reset: forbiddenSideEffect } }),
  useAuiState: (selector) => selector({ thread: { messages: [], isRunning: false } }),
  ThreadPrimitive: { Root: "thread-root", Viewport: "thread-viewport", Messages: "thread-messages", ViewportFooter: "thread-footer" },
  ComposerPrimitive: { Root: "composer-root", Input: "composer-input", Send: "composer-send" },
  createRoot: () => ({ render() {} }),
  document: documentMock,
  fetch: (url, options = {}) => {
    assert.ok(url === "/api/molecules" || url === "/api/solvents" || url.startsWith("/api/solvents?query="), `forbidden endpoint: ${url}`);
    assert.ok(["GET", "POST"].includes(options.method || "GET"));
    calls.push({ url, options });
    return fetcher(url, options);
  },
});
vm.runInNewContext(transformSync(executable, { loader: "jsx", format: "cjs" }).code, context);
const { MoleculeCatalogDialog, MoleculeRecord, SolventCatalogDialog, SolventRecord, WorkbenchThread, solventRegistrationPayload, parseProposalPlan, ProposalPlanOverview, AssistantCompletionSync } = context.components;
const plain = (value) => JSON.parse(JSON.stringify(value));
const nodes = (tree) => Array.isArray(tree) ? tree.flatMap(nodes)
  : tree && typeof tree === "object" ? [tree, ...nodes(tree.props?.children)] : [];
const text = (tree) => Array.isArray(tree) ? tree.map(text).join("")
  : tree && typeof tree === "object" ? text(tree.props?.children)
    : tree == null || typeof tree === "boolean" ? "" : String(tree);
const find = (fixture, predicate) => nodes(fixture.tree).find(predicate);
const input = (fixture, id) => find(fixture, (node) => node.type === "input" && node.props.id === id);
const form = (fixture, name) => find(fixture, (node) => node.type === "form" && node.props["aria-label"] === name);
const button = (fixture, label) => find(fixture, (node) => node.type === "button" && text(node) === label);
const submit = (fixture, name) => form(fixture, name).props.onSubmit({ preventDefault() {} });
const change = (fixture, id, value) => {
  input(fixture, id).props.onChange({ target: { value } });
  fixture.render();
};
const respond = (json, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => json });
const settle = async (fixture) => {
  await new Promise(setImmediate);
  await new Promise(setImmediate);
  fixture?.render();
};
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};
const builtin = { name: "Water", epsilon: "78.3553", epsinf: null, source: "builtin", manual: false };
const manual = { name: "MyMix", epsilon: "20", epsinf: "1.8", source: "manual", manual: true };
const catalog = (records = [builtin, manual], match = null) => ({ match, candidates: records });
const setup = async (handler = async () => respond(catalog())) => {
  calls.length = 0;
  fetcher = handler;
  let closes = 0;
  const fixture = new Harness(SolventCatalogDialog, { onClose: () => { closes += 1; } });
  fixture.closeCount = () => closes;
  fixture.render();
  fixture.flush();
  await settle(fixture);
  return fixture;
};
const fillRegistration = (fixture, name = "MyMix", epsilon = "20", epsinf = "1.8") => {
  change(fixture, "solvent-name", name);
  change(fixture, "solvent-epsilon", epsilon);
  change(fixture, "solvent-epsinf", epsinf);
};
const postCalls = () => calls.filter((call) => call.options.method === "POST");

{
  assert.deepEqual(plain(solventRegistrationPayload({ name: "  ", epsilon: " 20 ", epsinf: "1.8" })), { name: null, epsilon: "20", epsinf: "1.8" });
  for (const [epsilon, epsinf] of [["1", "1"], ["2e1", "1.8"], ["20.", "1.80"], ["+20", "1"]]) {
    assert.equal(solventRegistrationPayload({ name: " 混合液 ", epsilon, epsinf }).name, "混合液");
  }
  for (const invalid of ["", " ", "NaN", "Infinity", "-1", "0.9", "1e309", "0x10", "1,8"]) {
    assert.throws(() => solventRegistrationPayload({ name: "", epsilon: invalid, epsinf: "1" }));
    assert.throws(() => solventRegistrationPayload({ name: "", epsilon: "20", epsinf: invalid }));
  }
  for (const name of [" GAS ", "x".repeat(97), "my\tmix", "my\nmix"]) {
    assert.throws(() => solventRegistrationPayload({ name, epsilon: "20", epsinf: "1.8" }));
  }
  assert.throws(() => solventRegistrationPayload({ name: "", epsilon: "1", epsinf: "2" }), /epsilon >= epsinf/);
  console.log("PASS optional name, decimal/scientific values, finite/range/order and reserved-name validation");
}

{
  const fixture = new Harness(WorkbenchThread, { kind: "proposal", active: true, proposalId: "plan__test" });
  fixture.render();
  fixture.flush();
  assert.equal(button(fixture, "上传分子结构/分子库").props.type, "button");
  assert.equal(button(fixture, "上传分子结构/分子库").props["aria-haspopup"], "dialog");
  button(fixture, "上传分子结构/分子库").props.onClick();
  fixture.render();
  assert.ok(find(fixture, (node) => node.type === MoleculeCatalogDialog));
  assert.equal(button(fixture, "登记隐式溶剂/溶剂库").props.type, "button");
  assert.equal(button(fixture, "登记隐式溶剂/溶剂库").props["aria-haspopup"], "dialog");
  button(fixture, "登记隐式溶剂/溶剂库").props.onClick();
  fixture.render();
  assert.ok(find(fixture, (node) => node.type === SolventCatalogDialog));
  const composer = find(fixture, (node) => node.type === "composer-root");
  assert.equal(nodes(composer).some((node) => node.type === SolventCatalogDialog), false);
  fixture.render({ ...fixture.props, active: false });
  fixture.flush();
  assert.equal(find(fixture, (node) => node.type === SolventCatalogDialog), undefined);
  fixture.render({ ...fixture.props, active: true, kind: "run" });
  assert.equal(button(fixture, "登记隐式溶剂/溶剂库"), undefined);
  fixture.unmount();
  console.log("PASS proposal-only entry, no nested composer forms, hidden workspace closes dialog");
}

{
  calls.length = 0;
  fetcher = async (url) => {
    assert.equal(url, "/api/molecules");
    return respond({ molecules: [
      { name: "EC", input_suffixes: [".gjf", ".inp"] },
      { name: "Li", input_suffixes: [".inp"] },
    ] });
  };
  let closes = 0;
  const fixture = new Harness(MoleculeCatalogDialog, {
    proposalId: "plan__test", onResult: forbiddenSideEffect, onFailure: forbiddenSideEffect, onClose: () => { closes += 1; },
  });
  fixture.render();
  fixture.flush();
  await settle(fixture);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.method, undefined);
  const markup = renderToStaticMarkup(fixture.tree);
  assert.match(markup, /上传分子结构 \/ 分子库/);
  assert.match(markup, /EC/);
  assert.match(markup, /GJF · INP/);
  assert.doesNotMatch(markup, /struct\//);
  for (const listener of listeners) listener({ key: "Escape", preventDefault() {} });
  assert.equal(closes, 1);
  fixture.unmount();
  console.log("PASS molecule catalog loads auditable names and formats without paths");
}

{
  const fixture = await setup();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/solvents");
  assert.equal(calls[0].options.method, undefined);
  const markup = renderToStaticMarkup(fixture.tree);
  assert.match(markup, /Gaussian 内置/);
  assert.match(markup, /自定义/);
  assert.match(markup, /未提供/);
  assert.match(markup, /此处仅登记适用于Gaussian的隐式溶剂性质/);
  assert.match(markup, /default_编号/);
  assert.match(text(form(fixture, "登记自定义隐式溶剂")), /仅Eps\/EpsInf的介电近似，不是完整SMD参数化/);
  assert.match(markup, /aria-label="Gaussian 隐式溶剂库"/);
  change(fixture, "solvent-query", "mix + 水");
  fetcher = async () => respond(catalog([manual]));
  submit(fixture, "查询隐式溶剂");
  await settle(fixture);
  assert.equal(calls.at(-1).url, `/api/solvents?query=${encodeURIComponent("mix + 水")}`);
  assert.match(text(fixture.tree), /仅为候选，不会自动采用/);
  fetcher = async () => respond(catalog([manual], manual));
  submit(fixture, "查询隐式溶剂");
  await settle(fixture);
  assert.match(text(fixture.tree), /精确匹配/);
  assert.equal(nodes(fixture.tree).filter((node) => node.type === SolventRecord).length, 1);
  fetcher = async () => respond(catalog([]));
  submit(fixture, "查询隐式溶剂");
  await settle(fixture);
  assert.match(text(fixture.tree), /暂无匹配隐式溶剂/);
  fetcher = async () => respond({ detail: "溶剂库不可读" }, 503);
  submit(fixture, "查询隐式溶剂");
  await settle(fixture);
  assert.match(text(fixture.tree), /溶剂库不可读/);
  assert.equal(button(fixture, "查询隐式溶剂").props.disabled, false);
  fetcher = async () => respond({ candidates: [{}] });
  submit(fixture, "查询隐式溶剂");
  await settle(fixture);
  assert.match(text(fixture.tree), /格式无效/);
  for (const listener of listeners) listener({ key: "Escape", preventDefault() {} });
  assert.equal(fixture.closeCount(), 1);
  fixture.unmount();
  assert.equal(listeners.size, 0);
  assert.ok(focusRestored > 0);
  console.log("PASS catalog list/search, exact/candidate/empty results, source/value display, errors and keyboard close");
}

{
  const pending = deferred();
  const fixture = await setup();
  fetcher = () => pending.promise;
  change(fixture, "solvent-query", "old query");
  submit(fixture, "查询隐式溶剂");
  const oldSignal = calls.at(-1).options.signal;
  fetcher = async () => respond(catalog([manual], manual));
  change(fixture, "solvent-query", manual.name);
  submit(fixture, "查询隐式溶剂");
  await settle(fixture);
  assert.equal(oldSignal.aborted, true);
  pending.resolve(respond(catalog([builtin])));
  await settle(fixture);
  assert.match(renderToStaticMarkup(fixture.tree), /MyMix/);
  assert.doesNotMatch(renderToStaticMarkup(fixture.tree), /Water/);
  const late = deferred();
  fetcher = () => late.promise;
  submit(fixture, "查询隐式溶剂");
  fixture.unmount();
  assert.equal(calls.at(-1).options.signal.aborted, true);
  late.resolve(respond(catalog()));
  await settle();
  console.log("PASS stale searches cannot replace new results; unmount aborts and ignores late queries");
}

for (const name of ["  MyMix  ", "   "]) {
  const pending = deferred();
  const registered = { ...manual, name: name.trim() || "default_2" };
  const fixture = await setup();
  fillRegistration(fixture, name, "20.00", "1.80");
  fetcher = async (_url, options) => options.method === "POST" ? pending.promise : respond(catalog([registered], registered));
  const register = form(fixture, "登记自定义隐式溶剂").props.onSubmit;
  const first = register({ preventDefault() {} });
  await register({ preventDefault() {} });
  fixture.render();
  assert.equal(postCalls().length, 1);
  assert.deepEqual(JSON.parse(postCalls()[0].options.body), { name: name.trim() || null, epsilon: "20.00", epsinf: "1.80" });
  assert.equal(postCalls()[0].options.headers["Content-Type"], "application/json");
  assert.equal(button(fixture, "登记中…").props.disabled, true);
  assert.equal(input(fixture, "solvent-name").props.disabled, true);
  assert.equal(fixture.tree.props.closeDisabled, true);
  fixture.tree.props.onClose();
  for (const listener of listeners) listener({ key: "Escape", preventDefault() {} });
  assert.equal(fixture.closeCount(), 0);
  pending.resolve(respond({ ok: true, solvent: registered, runId: "must-not-bind", config: { mustNotApply: true } }));
  await first;
  await settle(fixture);
  assert.match(text(fixture.tree), new RegExp(`已登记 ${registered.name}`));
  assert.match(text(fixture.tree), /可在方案助理中指定使用/);
  assert.equal(input(fixture, "solvent-query").props.value, registered.name);
  assert.equal(input(fixture, "solvent-epsilon").props.value, "");
  assert.equal(calls.at(-1).url, `/api/solvents?query=${registered.name}`);
  assert.equal(fixture.tree.props.closeDisabled, false);
  assert.equal(postCalls().length, 1);
  fixture.unmount();
}
console.log("PASS named/default-number registrations, receipt name, synchronous double-submit protection, GET-only refresh");

{
  const fixture = await setup();
  fillRegistration(fixture, "", "1", "2");
  await submit(fixture, "登记自定义隐式溶剂");
  fixture.render();
  assert.equal(postCalls().length, 0);
  assert.match(text(fixture.tree), /epsilon >= epsinf/);
  fillRegistration(fixture);
  const failures = [
    async () => respond({ detail: "溶剂名已存在" }, 400),
    async () => { throw new Error("网络不可用"); },
    async () => respond({ ok: true, solvent: {} }),
    async () => respond({ ok: false, solvent: manual }),
  ];
  for (const handler of failures) {
    fetcher = handler;
    const before = calls.length;
    await submit(fixture, "登记自定义隐式溶剂");
    await settle(fixture);
    assert.equal(calls.length, before + 1);
    assert.equal(input(fixture, "solvent-name").props.value, "MyMix");
    assert.equal(input(fixture, "solvent-epsilon").props.value, "20");
    assert.equal(button(fixture, "仅登记隐式溶剂").props.disabled, false);
    assert.match(text(fixture.tree), /不会自动重复登记/);
    assert.doesNotMatch(text(fixture.tree), /已登记 MyMix/);
  }
  fetcher = async (_url, options) => options.method === "POST" ? respond({ ok: true, solvent: manual }) : respond({ detail: "刷新失败" }, 503);
  await submit(fixture, "登记自定义隐式溶剂");
  await settle(fixture);
  assert.match(text(fixture.tree), /已登记 MyMix/);
  assert.match(text(fixture.tree), /刷新失败/);
  fixture.unmount();
  console.log("PASS invalid input sends nothing; duplicate/network/malformed receipts preserve form; refresh failure preserves success");
}

{
  const fixture = await setup();
  const pending = deferred();
  fillRegistration(fixture);
  fetcher = () => pending.promise;
  const request = submit(fixture, "登记自定义隐式溶剂");
  fixture.unmount();
  assert.equal(postCalls()[0].options.signal.aborted, true);
  pending.resolve(respond({ ok: true, solvent: manual }));
  await request;
  await settle();
  assert.equal(calls.length, 2);
  console.log("PASS leaving project ignores registration response without applying a config or launching a run");
}

{
  const proposal = {
    version: 1,
    structure: [
      { name: "Li", count: 10, optimization_level: "B3LYP/6-31G(d)", solvent: "Water", solvent_source: "builtin", scrf_override: "SCRF=(SMD,Solvent=Water)；覆盖原 SCRF" },
      { name: "EC", count: 20, optimization_level: "B3LYP/6-31G(d)", solvent: "MyMix", solvent_source: "manual", scrf_override: "覆盖原始 SCRF", solvent_note: "Eps=20，EpsInf=1.8；介电近似，不是完整 SMD 参数化" },
      { name: "DMC", count: 30, optimization_level: "B3LYP/6-31G(d)", solvent: "gas", solvent_source: "gas", scrf_override: "移除原 SCRF" },
    ],
    environment: { charge_scale: "0.80", initial_density: "0.70 g/cm3", total_molecules: 60 },
    md_steps: [
      { label: "EM", meta: "能量最小化" },
      ...Array.from({ length: 6 }, (_value, index) => ({ label: `EQ ${index + 1}`, meta: "NPT · 1 ns · 500K→500K" })),
      { label: "PROD", meta: "NPT · 10 ns · 298K→298K" },
    ],
  };
  const before = JSON.stringify(proposal);
  const plan = parseProposalPlan(`[[WILLY_PLAN_DATA:${before}]]`);
  const markup = renderToStaticMarkup(React.createElement(ProposalPlanOverview, { plan }));
  for (const expected of ["Li", "EC", "DMC", "SMD: Water（builtin）", "SMD: MyMix（manual）", "SMD: gas（gas）", "覆盖原 SCRF", "移除原 SCRF", "EpsInf=1.8", "MD 模拟流程", "EQ 6"]) assert.ok(markup.includes(expected), expected);
  assert.ok(markup.includes("介电近似，不是完整 SMD 参数化"));
  assert.equal(JSON.stringify(proposal), before);
  const legacy = { ...proposal, structure: [{ name: "Li", count: 60, optimization_level: "B3LYP/6-31G(d)" }] };
  assert.ok(parseProposalPlan(`[[WILLY_PLAN_DATA:${JSON.stringify(legacy)}]]`));
  console.log("PASS per-molecule SMD source/SCRF overrides, gas treatment, EQ and legacy plan cards remain intact");
}

{
  const originalState = context.useAuiState;
  let running = true;
  context.useAuiState = (selector) => selector({ thread: { messages: [], isRunning: running } });
  const pendingTransition = { current: { proposalId: "plan__next" } };
  const transitions = [];
  const fixture = new Harness(AssistantCompletionSync, {
    pendingTransition,
    onPipelineStarted: (runId) => transitions.push(runId),
    onProposalWorkspaceChanged: (proposalId) => transitions.push(proposalId),
  });
  fixture.render();
  fixture.flush();
  assert.deepEqual(transitions, []);
  running = false;
  fixture.render();
  fixture.flush();
  assert.deepEqual(transitions, ["plan__next"]);
  assert.equal(pendingTransition.current, null);
  fixture.render();
  fixture.flush();
  assert.equal(transitions.length, 1);
  running = true;
  fixture.render();
  fixture.flush();
  pendingTransition.current = { runId: "md__next" };
  running = false;
  fixture.render();
  fixture.flush();
  assert.deepEqual(transitions, ["plan__next", "md__next"]);
  fixture.unmount();
  context.useAuiState = originalState;
  console.log("PASS workspace promotion and launch notifications wait for completed replies and fire exactly once");
}

console.log("SMD frontend acceptance passed; no real backend, browser port, catalog or project writes.");
