import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, resolve, sep } from "node:path";
import { chromium } from "playwright";

const root = resolve("dist");
const officialAccountPath = "/app-assets/qrcode_for_gh_7df1329939c6_258.jpg";
const officialAccountQr = resolve("../assets/qrcode_for_gh_7df1329939c6_258.jpg");
const types = { ".css": "text/css", ".js": "application/javascript", ".html": "text/html" };
const runId = "md__202608260002";
const snapshot = {
  run_id: runId,
  state: "running",
  phase: "分子拓扑参数化",
  progress: "3/10",
  done_steps: [1, 2, 3],
  status_event_id: `${runId}:status:running:4:none:none`,
  live_summary: "正在执行第 4 步。",
};
const proposalPlan = {
  version: 1,
  structure: [{ name: "Li", count: 100, optimization_level: "b3lyp/6-311+g(d,p)", solvent: "Acetone", solvent_source: "builtin", scrf_override: "覆盖原始 SCRF（含多行设置）" }],
  environment: { charge_scale: "0.80", initial_density: "0.70 g/cm3", total_molecules: 100 },
  md_steps: [
    { label: "EM", meta: "能量最小化" },
    { label: "EQ · 升温（heat）", meta: "NPT · 2 ns · 298K→500K" },
    { label: "EQ · 高温恒温（hold_high）", meta: "NPT · 1 ns · 500K→500K" },
    { label: "EQ · 降至过渡温度（cool_transition）", meta: "NPT · 2 ns · 500K→400K" },
    { label: "EQ · 过渡恒温（hold_transition）", meta: "NPT · 1 ns · 400K→400K" },
    { label: "EQ · 降至目标温度（cool_target）", meta: "NPT · 2 ns · 400K→298K" },
    { label: "EQ · 目标恒温（hold_target）", meta: "NPT · 2 ns · 298K→298K" },
    { label: "PROD", meta: "NPT · 10 ns · 298K→298K" },
  ],
};
const proposalSummary = `[[WILLY_PLAN_DATA:${JSON.stringify(proposalPlan)}]]\n**模拟方案确认**\n\n下一步将开始结构优化。\n\n确认无误后回复“运行”即可开始。\n若参数有误请提出，我会更新方案。`;
const server = createServer(async (request, response) => {
  const pathname = new URL(request.url, "http://127.0.0.1").pathname;
  const candidate = resolve(root, `.${pathname}`);
  const file = pathname === officialAccountPath
    ? officialAccountQr
    : candidate.startsWith(`${root}${sep}`) ? candidate : resolve(root, "index.html");
  try {
    response.writeHead(200, { "content-type": types[extname(file)] || "application/octet-stream" });
    response.end(await readFile(file));
  } catch {
    response.writeHead(200, { "content-type": "text/html" });
    response.end(await readFile(resolve(root, "index.html")));
  }
});

async function installApiFixtures(page) {
  const fixtures = {
    solvents: [
      { name: "Water", epsilon: "78.3553", epsinf: null, source: "builtin", manual: false },
      { name: "default_1", epsilon: "10", epsinf: "1.5", source: "manual", manual: true },
    ],
    molecules: [
      { name: "EC", input_suffixes: [".gjf", ".inp"] },
      { name: "Li", input_suffixes: [".inp"] },
    ],
    mutations: [],
    registrations: [],
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const send = (json, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(json) });
    if (!["GET", "HEAD"].includes(request.method())) fixtures.mutations.push({ path: url.pathname, method: request.method() });
    if (url.pathname === "/api/solvents" && request.method() === "GET") {
      const query = (url.searchParams.get("query") || "").trim().toLowerCase();
      const match = fixtures.solvents.find((record) => record.name.toLowerCase() === query) || null;
      return send({ match, candidates: match ? [match] : fixtures.solvents.filter((record) => record.name.toLowerCase().includes(query)) });
    }
    if (url.pathname === "/api/solvents" && request.method() === "POST") {
      const body = JSON.parse(request.postData() || "{}");
      fixtures.registrations.push(body);
      let name = typeof body.name === "string" ? body.name.trim() : "";
      if (!name) {
        let index = 1;
        while (fixtures.solvents.some((record) => record.name.toLowerCase() === `default_${index}`)) index += 1;
        name = `default_${index}`;
      }
      if (fixtures.solvents.some((record) => record.name.toLowerCase() === name.toLowerCase())) {
        return send({ detail: `溶剂名已存在（大小写不敏感精确匹配）: ${name}` }, 400);
      }
      const epsilon = Number(body.epsilon);
      const epsinf = Number(body.epsinf);
      if (!Number.isFinite(epsilon) || !Number.isFinite(epsinf) || epsilon < epsinf || epsinf < 1) {
        return send({ detail: "必须满足 epsilon >= epsinf >= 1" }, 400);
      }
      const solvent = { name, epsilon: String(body.epsilon), epsinf: String(body.epsinf), source: "manual", manual: true };
      fixtures.solvents.push(solvent);
      return send({ ok: true, solvent });
    }
    if (url.pathname === "/api/molecules" && request.method() === "GET") return send({ molecules: fixtures.molecules });
    if (url.pathname === "/api/workspace") return send({ run_id: runId, snapshot });
    if (url.pathname === "/api/runs" && request.method() === "GET") {
      return send({ active_run_id: runId, runs: [
        { run_id: runId, display_name: "Li_Project", state: "running", updated_at: "2026-08-26T12:00:00+00:00" },
        { run_id: "md__202608260001", display_name: "", state: "aborted", updated_at: "2026-08-26T11:00:00+00:00" },
      ] });
    }
    if (url.pathname === `/api/runs/${runId}/chat` && request.method() === "GET") {
      return send({ run_id: runId, snapshot, messages: [
        { role: "assistant", content: "已恢复该工程的运行历史。" },
        { role: "assistant", content: "第 1 步「结构优化」已完成。", _run_assistant_event_kind: "step_completed" },
        { role: "assistant", content: snapshot.live_summary, _run_assistant_event_kind: "status", _run_assistant_event_active: true },
      ] });
    }
    if (url.pathname === `/api/runs/${runId}/updates`) {
      const after = url.searchParams.get("after");
      return send({
        run_id: runId,
        snapshot,
        cursor: snapshot.status_event_id,
        events: after === snapshot.status_event_id ? [] : [
          { event_id: `${runId}:step:1:completed`, kind: "step_completed", content: "第 1 步「结构优化」已完成。" },
          { event_id: snapshot.status_event_id, kind: "status", content: snapshot.live_summary },
        ],
      });
    }
    if (url.pathname === "/api/proposal/chat" && request.method() === "POST") {
      return send({ text: proposalSummary, proposalId: "plan__visual" });
    }
    if (url.pathname === "/api/proposals/plan__visual" && request.method() === "GET") {
      return send({
        workspace: { workspace_id: "plan__visual", kind: "plan" },
        messages: [{ role: "assistant", content: proposalSummary }],
      });
    }
    if (url.pathname === `/api/runs/${runId}/chat` && request.method() === "POST") {
      const body = JSON.parse(request.postData() || "{}");
      if (typeof body.message !== "string" || !body.message.trim()) {
        return send({ detail: "message 必须是非空文本" }, 400);
      }
      return send({ text: "运行助理响应", run_id: runId, messages: [], snapshot });
    }
    if (url.pathname === `/api/runs/${runId}/display-name` && request.method() === "PATCH") {
      const body = JSON.parse(request.postData() || "{}");
      return typeof body.display_name === "string" ? send({ run_id: runId, display_name: body.display_name, changed: true, state: "running" }) : send({ detail: "display_name missing" }, 400);
    }
    if (url.pathname === `/api/runs/${runId}/stop` && request.method() === "POST") {
      return send({ run_id: runId, message: "已请求安全停止。", snapshot: { ...snapshot, state: "stopping" } });
    }
    if (url.pathname === `/api/runs/${runId}/logs` && request.method() === "GET") {
      return send({
        run_id: runId,
        manifest: { filename: "run_manifest.json", truncated: false, content: '{\n  "schema_version": 2,\n  "run_id": "md__202608260002"\n}' },
        config: { filename: "config.json", truncated: false, content: '{\n  "ion_charge_scale": 0.80,\n  "md": {\n    "eq": { "target_temperature": 298 }\n  }\n}' },
      });
    }
    if (url.pathname === "/api/proposal/upload" && request.method() === "POST") {
      return send({ text: "已上传Li+.inp，仅保留坐标、电荷、自旋。" });
    }
    if (url.pathname === "/api/visualization") {
      return send({
        run_choices: [runId, "md__202608260001"],
        run_id: url.searchParams.get("run_id") || runId,
        artifacts: ["prod.pdb"],
        selected_artifact: url.searchParams.get("artifact") || "prod.pdb",
        sphere_scale: Number(url.searchParams.get("sphere_scale") || "0.35"),
        stick_radius: Number(url.searchParams.get("stick_radius") || "0.22"),
        background: url.searchParams.get("background") || "beige",
        html: "<main>structure preview</main>",
        legend_html: "<span>原子颜色</span>",
      });
    }
    return send({ detail: "fixture route not configured" }, 404);
  });
  return fixtures;
}

async function checkSolventModal(page, fixtures, label) {
  const beforeMutations = fixtures.mutations.length;
  const beforeRegistrations = fixtures.registrations.length;
  const originalRecords = structuredClone(fixtures.solvents);
  const proposalInput = page.locator("#proposal-composer-input");
  const originalDraft = await proposalInput.inputValue();
  const draft = "暂存草稿：查询或登记隐式溶剂不应提交此消息";
  await proposalInput.fill(draft);
  const originalPlan = await page.locator(".proposal-plan").allTextContents();
  const originalMessages = await page.locator(".assistant-surface.active .message-copy").allTextContents();
  const trigger = page.getByRole("button", { name: "登记隐式溶剂/溶剂库", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "Gaussian 隐式溶剂库", exact: true });
  const results = dialog.getByRole("region", { name: "隐式溶剂查询结果", exact: true });
  const queryForm = dialog.getByRole("form", { name: "查询隐式溶剂", exact: true });
  const registrationForm = dialog.getByRole("form", { name: "登记自定义隐式溶剂", exact: true });
  await results.getByText("Water", { exact: true }).waitFor();
  await results.getByText("Gaussian 内置", { exact: true }).waitFor();
  await results.getByText("78.3553", { exact: true }).waitFor();
  await results.getByText("未提供", { exact: true }).waitFor();
  assert.ok((await registrationForm.textContent()).includes("仅Eps/EpsInf的介电近似，不是完整SMD参数化"));
  assert.equal(await dialog.evaluate((element) => element.closest("form")), null, "modal must not be nested in the chat composer form");
  await queryForm.getByLabel("隐式溶剂名称或关键词", { exact: true }).fill("water");
  await queryForm.getByRole("button", { name: "查询隐式溶剂", exact: true }).click();
  await results.getByText("精确匹配", { exact: true }).waitFor();
  assert.equal(await results.locator(".solvent-record").count(), 1);
  assert.equal(await results.locator(".solvent-record strong").textContent(), "Water");

  await registrationForm.getByLabel("name（可选）", { exact: true }).fill("");
  await registrationForm.getByLabel("epsilon", { exact: true }).fill("20.00");
  await registrationForm.getByLabel("epsinf", { exact: true }).fill("1.80");
  const responsePromise = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/solvents" && response.request().method() === "POST");
  await registrationForm.getByRole("button", { name: "仅登记隐式溶剂", exact: true }).click();
  assert.equal((await responsePromise).status(), 200);
  await registrationForm.getByText("已登记 default_2，可在方案助理中指定使用。", { exact: true }).waitFor();
  await results.getByText("default_2", { exact: true }).waitFor();
  await results.getByText("自定义", { exact: true }).waitFor();
  assert.deepEqual(fixtures.registrations[beforeRegistrations], { name: null, epsilon: "20.00", epsinf: "1.80" });
  assert.equal(await registrationForm.getByLabel("name（可选）", { exact: true }).inputValue(), "");
  assert.equal(await registrationForm.getByLabel("epsilon", { exact: true }).inputValue(), "");
  assert.equal(await registrationForm.getByLabel("epsinf", { exact: true }).inputValue(), "");

  for (const name of ["wAtEr", "DEFAULT_2"]) {
    await registrationForm.getByLabel("name（可选）", { exact: true }).fill(name);
    await registrationForm.getByLabel("epsilon", { exact: true }).fill("40");
    await registrationForm.getByLabel("epsinf", { exact: true }).fill("2");
    const conflictPromise = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/solvents" && response.request().method() === "POST");
    await registrationForm.getByRole("button", { name: "仅登记隐式溶剂", exact: true }).click();
    assert.equal((await conflictPromise).status(), 400);
    await registrationForm.getByRole("alert").filter({ hasText: name }).waitFor();
    assert.match(await registrationForm.getByRole("alert").textContent(), /溶剂名已存在.*不会自动重复登记/);
    assert.equal(await registrationForm.getByLabel("name（可选）", { exact: true }).inputValue(), name);
    assert.equal(await registrationForm.getByLabel("epsilon", { exact: true }).inputValue(), "40");
    assert.equal(await registrationForm.locator(".solvent-success").count(), 0);
  }
  assert.deepEqual(fixtures.solvents.slice(0, originalRecords.length), originalRecords, "conflicts must not overwrite existing records");
  assert.equal(fixtures.solvents.length, originalRecords.length + 1);
  assert.equal(fixtures.solvents.at(-1).epsilon, "20.00");
  const bounds = await dialog.boundingBox();
  assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= page.viewportSize().width, "solvent dialog must fit the viewport");
  await page.screenshot({ path: `/tmp/willy-app-solvents-${label}.png`, fullPage: true });
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  assert.equal(await trigger.evaluate((element) => element === document.activeElement), true, "closing must restore focus");

  await trigger.click();
  await results.getByText("default_2", { exact: true }).waitFor();
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  assert.equal(await proposalInput.inputValue(), draft);
  assert.deepEqual(await page.locator(".proposal-plan").allTextContents(), originalPlan);
  assert.deepEqual(await page.locator(".assistant-surface.active .message-copy").allTextContents(), originalMessages);
  assert.equal(await page.getByRole("tab", { name: "方案助理", exact: true }).getAttribute("aria-selected"), "true");
  assert.equal(fixtures.registrations.length - beforeRegistrations, 3, "registration must not auto-retry");
  assert.deepEqual(fixtures.mutations.slice(beforeMutations), Array.from({ length: 3 }, () => ({ path: "/api/solvents", method: "POST" })), "modal must not mutate projects or submit chat");
  await proposalInput.fill(originalDraft);
  console.log(`PASS ${label} solvent modal: builtin query, default numbering, conflicts, close/Escape, read-only project boundary`);
}

async function checkMoleculeCatalogDialog(page, fixtures, label) {
  const beforeMutations = fixtures.mutations.length;
  const proposalInput = page.locator("#proposal-composer-input");
  const originalDraft = await proposalInput.inputValue();
  const draft = "暂存草稿：查看分子库不应提交此消息";
  await proposalInput.fill(draft);
  const originalMessages = await page.locator(".assistant-surface.active .message-copy").allTextContents();
  const trigger = page.getByRole("button", { name: "上传分子结构/分子库", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "上传分子结构 / 分子库", exact: true });
  const results = dialog.getByRole("region", { name: "当前分子库", exact: true });
  await results.getByText("分子库 · 2 项", { exact: true }).waitFor();
  await results.getByText("EC", { exact: true }).waitFor();
  await results.getByText("GJF · INP", { exact: true }).waitFor();
  await results.getByText("Li", { exact: true }).waitFor();
  assert.equal(await dialog.evaluate((element) => element.closest("form")), null, "molecule modal must not be nested in the chat composer form");
  const bounds = await dialog.boundingBox();
  assert.ok(bounds && bounds.x >= 0 && bounds.x + bounds.width <= page.viewportSize().width, "molecule dialog must fit the viewport");
  await page.screenshot({ path: `/tmp/willy-app-molecules-${label}.png`, fullPage: true });
  await dialog.getByRole("button", { name: "关闭", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  assert.equal(await trigger.evaluate((element) => element === document.activeElement), true, "closing must restore molecule trigger focus");

  await trigger.click();
  await results.getByText("EC", { exact: true }).waitFor();
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  assert.equal(await proposalInput.inputValue(), draft);
  assert.deepEqual(await page.locator(".assistant-surface.active .message-copy").allTextContents(), originalMessages);
  assert.equal(fixtures.mutations.length, beforeMutations, "opening molecule library must not mutate projects or submit chat");
  await proposalInput.fill(originalDraft);
  console.log(`PASS ${label} molecule dialog: current library, viewport fit, close/Escape, read-only project boundary`);
}

await new Promise((resolveListen) => server.listen(4175, "127.0.0.1", resolveListen));
const browser = await chromium.launch({ headless: true });
try {
  const desktop = await browser.newPage({ viewport: { width: 1440, height: 940 }, deviceScaleFactor: 1 });
  const errors = [];
  desktop.on("pageerror", (error) => errors.push(error.message));
  const desktopFixtures = await installApiFixtures(desktop);
  await desktop.goto("http://127.0.0.1:4175", { waitUntil: "networkidle" });
  await checkSolventModal(desktop, desktopFixtures, "desktop");
  await checkMoleculeCatalogDialog(desktop, desktopFixtures, "desktop");

  await desktop.getByRole("button", { name: "收起详情" }).click();
  const drawerToggle = desktop.getByRole("button", { name: "展开详情" });
  if (await drawerToggle.getAttribute("aria-expanded") !== "false") throw new Error("detail drawer did not collapse");
  await drawerToggle.click();
  await desktop.getByRole("button", { name: runId, exact: false }).first().waitFor();
  const resizePanel = async (label, deltaX) => {
    const handle = desktop.getByRole("button", { name: label });
    const box = await handle.boundingBox();
    if (!box) throw new Error(`${label} resize handle is unavailable`);
    await desktop.mouse.move(box.x + box.width / 2, box.y + Math.min(260, box.height / 2));
    await desktop.mouse.down();
    await desktop.mouse.move(box.x + box.width / 2 + deltaX, box.y + Math.min(260, box.height / 2));
    await desktop.mouse.up();
  };
  const drawerBeforeResize = await desktop.locator(".detail-drawer").boundingBox();
  await resizePanel("调整本地任务栏宽度", 60);
  const drawerAfterResize = await desktop.locator(".detail-drawer").boundingBox();
  if (!drawerBeforeResize || !drawerAfterResize || drawerAfterResize.width - drawerBeforeResize.width < 50) throw new Error("local task drawer did not resize");
  await desktop.getByRole("button", { name: "收起详情" }).click();
  await desktop.getByRole("button", { name: "展开详情" }).click();
  await desktop.waitForTimeout(220);
  const drawerAfterRestore = await desktop.locator(".detail-drawer").boundingBox();
  if (!drawerAfterRestore || Math.abs(drawerAfterRestore.width - drawerAfterResize.width) > 1) throw new Error("local task drawer width was lost after collapse");
  const inspectorBeforeResize = await desktop.locator(".status-panel").boundingBox();
  await resizePanel("调整运行检查器宽度", -60);
  const inspectorAfterResize = await desktop.locator(".status-panel").boundingBox();
  if (!inspectorBeforeResize || !inspectorAfterResize || inspectorAfterResize.width - inspectorBeforeResize.width < 50) throw new Error("run inspector did not resize");
  await desktop.getByRole("button", { name: "收起运行检查器" }).click();
  await desktop.getByRole("button", { name: "展开运行检查器" }).click();
  await desktop.waitForTimeout(220);
  const inspectorAfterRestore = await desktop.locator(".status-panel").boundingBox();
  if (!inspectorAfterRestore || Math.abs(inspectorAfterRestore.width - inspectorAfterResize.width) > 1) throw new Error("run inspector width was lost after collapse");
  await desktop.getByRole("button", { name: "配置", exact: true }).click();
  await desktop.getByText("OpenAI-compatible API Key", { exact: true }).waitFor();
  await desktop.getByRole("button", { name: "测试连接", exact: true }).click();
  await desktop.locator(".configuration-content .action-status").last().waitFor();
  const configurationFontSizes = await desktop.locator(".configuration-content").evaluate((content) => {
    const readSize = (selector) => getComputedStyle(content.querySelector(selector)).fontSize;
    return {
      url: readSize('.config-field input'),
      fieldLabel: readSize('.config-field > span'),
      help: readSize('.config-field small'),
      action: readSize('.config-actions button'),
      result: readSize('.action-status'),
      notice: readSize('.config-notice'),
    };
  });
  if (new Set(Object.values(configurationFontSizes)).size !== 1 || configurationFontSizes.url !== "12px") {
    throw new Error(`configuration typography mismatch: ${JSON.stringify(configurationFontSizes)}`);
  }
  await desktop.getByRole("button", { name: "新手指南", exact: true }).click();
  await desktop.getByText("零、Agent 的概况", { exact: true }).waitFor();
  await desktop.getByRole("button", { name: "关于", exact: true }).click();
  await desktop.getByText("Willy：AI 驱动的小分子 Gromacs 模拟工具", { exact: true }).waitFor();
  const officialAccount = desktop.getByRole("img", { name: "公众号：小w的学习笔记" });
  await officialAccount.waitFor();
  if (await officialAccount.evaluate((image) => image.naturalWidth) < 1) throw new Error("official account QR did not load");
  await desktop.locator(".official-account").click();
  await desktop.getByRole("dialog", { name: "公众号二维码" }).waitFor();
  const enlargedQr = desktop.getByRole("img", { name: "公众号：小w的学习笔记 二维码" });
  if (await enlargedQr.evaluate((image) => image.naturalWidth) < 1) throw new Error("enlarged official account QR did not load");
  await desktop.getByRole("dialog", { name: "公众号二维码" }).getByRole("button", { name: "关闭" }).click();
  await desktop.getByRole("button", { name: "Willy 简介" }).click();
  await desktop.getByRole("dialog", { name: "Willy 简介" }).waitFor();
  await desktop.getByRole("dialog", { name: "Willy 简介" }).getByRole("button", { name: "关闭" }).click();
  if (await desktop.getByText("READY", { exact: true }).count()) throw new Error("legacy READY state is still visible");
  if (await desktop.locator(".avatar").textContent() !== "ZL") throw new Error("default avatar is not ZL");
  await desktop.getByRole("button", { name: "本地任务", exact: true }).click();
  if (await desktop.getByText("LOCAL INSTANCE", { exact: true }).count()) throw new Error("legacy local-instance chip is still visible");
  await desktop.getByRole("button", { name: "刷新工程目录" }).click();
  await desktop.getByText("已刷新", { exact: true }).waitFor();
  if (await desktop.locator(".run-directory-row.selected").count() !== 1) throw new Error("selected run is not marked in the local task list");
  await desktop.getByText("Willy/md_run/目录下的已有工程。", { exact: true }).waitFor();
  await desktop.locator(".run-directory-row").first().click({ button: "right" });
  const renameInput = desktop.getByRole("textbox", { name: "工程名称" });
  await renameInput.fill("Li_eq_retry");
  await desktop.getByRole("button", { name: "重命名", exact: true }).click();

  const proposalTab = desktop.getByRole("tab", { name: "方案助理", exact: true });
  const runTab = desktop.getByRole("tab", { name: "运行助理", exact: true });
  const visualTab = desktop.getByRole("tab", { name: "可视化", exact: true });
  const logsTab = desktop.getByRole("tab", { name: "日志", exact: true });
  if (await proposalTab.getAttribute("aria-selected") !== "true") throw new Error("proposal assistant was not initially active");
  const welcomeBox = await desktop.locator(".proposal-welcome").boundingBox();
  const threadBox = await desktop.locator(".assistant-surface.active .thread-content").boundingBox();
  if (!welcomeBox || !threadBox || Math.abs(welcomeBox.x + welcomeBox.width / 2 - (threadBox.x + threadBox.width / 2)) > 2) throw new Error("proposal welcome is not horizontally centered");
  await desktop.getByRole("button", { name: "上传分子结构/分子库", exact: true }).waitFor();
  await desktop.getByRole("button", { name: "上传分子结构/分子库", exact: true }).click();
  const moleculeDialog = desktop.getByRole("dialog", { name: "上传分子结构 / 分子库", exact: true });
  await moleculeDialog.getByRole("button", { name: "选择并上传分子结构", exact: true }).click();
  await desktop.locator('input[type="file"]').setInputFiles({
    name: "Li+.inp",
    mimeType: "text/plain",
    buffer: Buffer.from("* xyz 1 1\nLi 0 0 0\n*\n"),
  });
  await desktop.getByText("已上传Li+.inp，仅保留坐标、电荷、自旋。", { exact: true }).waitFor();
  const tabsBox = await desktop.locator(".assistant-tabs").boundingBox();
  const tabBoxes = await Promise.all([proposalTab, runTab, visualTab, logsTab].map((tab) => tab.boundingBox()));
  if (!tabsBox || tabBoxes.some((box) => !box) || Math.max(...tabBoxes.map((box) => box.width)) - Math.min(...tabBoxes.map((box) => box.width)) > 1 || Math.abs(tabBoxes[0].x - tabsBox.x) > 1 || Math.abs(tabBoxes.at(-1).x + tabBoxes.at(-1).width - (tabsBox.x + tabsBox.width)) > 1) {
    throw new Error("assistant tabs are not evenly centered in their regions");
  }
  await runTab.click();
  if (await runTab.getAttribute("aria-selected") !== "true") throw new Error("run assistant tab did not activate");
  await desktop.getByText("已恢复该工程的运行历史。", { exact: true }).waitFor();
  await desktop.getByText("第 1 步「结构优化」已完成。", { exact: true }).waitFor();
  await desktop.getByLabel("当前步骤进行中").waitFor();
  if (await desktop.locator(".run-activity-feed").count()) throw new Error("legacy top activity card is still visible");
  const assistantBubbleBorder = await desktop.locator(".assistant-message .message-copy").first().evaluate((element) => getComputedStyle(element).borderTopWidth);
  if (Number.parseInt(assistantBubbleBorder, 10) < 1) throw new Error("run assistant bubbles do not have borders");
  const assistantBubbleBoxes = await desktop.locator(".assistant-message .message-copy").evaluateAll((messages) => messages.map((message) => message.getBoundingClientRect()));
  if (assistantBubbleBoxes[1].top - assistantBubbleBoxes[0].bottom < 28) throw new Error("assistant bubbles do not have the required vertical gap");
  await desktop.screenshot({ path: "/tmp/willy-app-run.png", fullPage: true });
  await desktop.getByRole("button", { name: "中止流水线", exact: true }).click();
  await desktop.getByRole("button", { name: "确认中止", exact: true }).waitFor();
  const runInput = desktop.locator("#run-composer-input");
  if (await runInput.getAttribute("placeholder") !== "输入进度查询、/指令等") throw new Error("run placeholder mismatch");
  await runInput.fill("/");
  await desktop.getByRole("listbox", { name: "运行控制命令" }).waitFor();
  await desktop.getByRole("button", { name: /以修改参数创建分支/ }).click();
  if (await runInput.inputValue() !== "/fork ") throw new Error("slash command was not applied to run assistant");
  await visualTab.click();
  if (await visualTab.getAttribute("aria-selected") !== "true") throw new Error("visualization tab did not activate");
  if (await desktop.getByText("AUTHORIZED ARTIFACTS", { exact: true }).count()) throw new Error("legacy visualization heading is still visible");
  await desktop.getByRole("heading", { name: "Li_Project", exact: true }).waitFor();
  if (await desktop.getByText("结构可视化", { exact: true }).count()) throw new Error("legacy visualization heading is still visible");
  await desktop.locator(".run-directory").nth(1).click();
  await desktop.getByRole("heading", { name: "md__202608260001", exact: true }).waitFor();
  if (await visualTab.getAttribute("aria-selected") !== "true") throw new Error("switching projects reset the active assistant tab");
  await desktop.locator(".run-directory").nth(0).click();
  await desktop.getByRole("heading", { name: "Li_Project", exact: true }).waitFor();
  if (await visualTab.getAttribute("aria-selected") !== "true") throw new Error("returning to a project reset the active assistant tab");
  await desktop.getByRole("combobox", { name: "运行目录" }).selectOption("md__202608260001");
  await desktop.getByRole("heading", { name: "md__202608260001", exact: true }).waitFor();
  const visualFields = desktop.locator(".visual-toolbar .visual-field");
  if (await visualFields.count() !== 5) throw new Error("visualization controls are incomplete");
  const visualControlLabels = await visualFields.locator(":scope > span").allTextContents();
  if (JSON.stringify(visualControlLabels) !== JSON.stringify(["运行目录", "结构文件", "球体大小", "棍宽度", "背景"])) throw new Error(`visualization control order mismatch: ${visualControlLabels}`);
  const visualFieldWidths = await visualFields.evaluateAll((fields) => fields.map((field) => field.getBoundingClientRect().width));
  if (Math.max(...visualFieldWidths) - Math.min(...visualFieldWidths) > 1) throw new Error(`visualization controls are not equally sized: ${visualFieldWidths}`);
  const visualFontSizes = await desktop.locator(".visual-toolbar").evaluate((toolbar) => ({
    label: getComputedStyle(toolbar.querySelector(".visual-field > span")).fontSize,
    select: getComputedStyle(toolbar.querySelector("select")).fontSize,
    legend: getComputedStyle(document.querySelector(".visual-legend")).justifyContent,
  }));
  if (visualFontSizes.label !== "11px" || visualFontSizes.select !== "11px" || visualFontSizes.legend !== "center") throw new Error(`visualization typography mismatch: ${JSON.stringify(visualFontSizes)}`);
  const visualSurfaceOverflow = await desktop.locator(".visual-surface").evaluate((element) => getComputedStyle(element).overflowY);
  if (visualSurfaceOverflow !== "hidden") throw new Error(`visualization surface should not add a vertical scrollbar: ${visualSurfaceOverflow}`);
  const visualLayout = await desktop.locator(".visual-surface").evaluate((surface) => {
    const frame = surface.querySelector(".structure-frame").getBoundingClientRect();
    const legend = surface.querySelector(".visual-legend").getBoundingClientRect();
    const bounds = surface.getBoundingClientRect();
    return { height: frame.height, frameBottom: frame.bottom, legendBottom: legend.bottom, surfaceBottom: bounds.bottom };
  });
  if (visualLayout.height < 650 || visualLayout.height > 675 || visualLayout.frameBottom > visualLayout.surfaceBottom + 1 || visualLayout.legendBottom > visualLayout.surfaceBottom + 1) {
    throw new Error(`visualization layout mismatch: ${JSON.stringify(visualLayout)}`);
  }
  const sphereResponse = desktop.waitForResponse((response) => {
    const requestUrl = new URL(response.url());
    return requestUrl.pathname === "/api/visualization" && requestUrl.searchParams.get("sphere_scale") === "0.45";
  });
  await desktop.getByRole("combobox", { name: "球体大小" }).selectOption("0.45");
  await sphereResponse;
  const backgroundResponse = desktop.waitForResponse((response) => {
    const requestUrl = new URL(response.url());
    return requestUrl.pathname === "/api/visualization" && requestUrl.searchParams.get("background") === "white";
  });
  await desktop.getByRole("button", { name: "白色", exact: true }).click();
  await backgroundResponse;
  if (await desktop.getByRole("button", { name: "白色", exact: true }).getAttribute("aria-pressed") !== "true") throw new Error("selected background is not emphasized");
  if (await desktop.getByRole("combobox", { name: "棍宽度" }).inputValue() !== "0.22") throw new Error("stick radius default mismatch");
  const stickResponse = desktop.waitForResponse((response) => {
    const requestUrl = new URL(response.url());
    return requestUrl.pathname === "/api/visualization" && requestUrl.searchParams.get("stick_radius") === "0.30";
  });
  await desktop.getByRole("combobox", { name: "棍宽度" }).selectOption("0.30");
  await stickResponse;
  await desktop.screenshot({ path: "/tmp/willy-app-visual.png", fullPage: true });
  await logsTab.click();
  if (await logsTab.getAttribute("aria-selected") !== "true") throw new Error("logs tab did not activate");
  await desktop.getByLabel("Manifest 内容").waitFor();
  await desktop.getByLabel("Config JSON 内容").waitFor();
  if (await desktop.getByLabel("Manifest 内容").textContent() !== '{\n  "schema_version": 2,\n  "run_id": "md__202608260002"\n}') throw new Error("manifest record was not rendered");
  if (await desktop.getByLabel("Config JSON 内容").textContent() !== '{\n  "ion_charge_scale": 0.80,\n  "md": {\n    "eq": { "target_temperature": 298 }\n  }\n}') throw new Error("config.json record was not rendered");
  if (await desktop.getByText("events.jsonl", { exact: true }).count()) throw new Error("events record is still exposed in the logs view");
  const logColumns = desktop.locator(".log-column");
  const logColumnBoxes = await logColumns.evaluateAll((columns) => columns.map((column) => column.getBoundingClientRect()));
  if (await logColumns.count() !== 2 || logColumnBoxes[0].x >= logColumnBoxes[1].x) throw new Error("logs are not arranged in two columns");
  await desktop.screenshot({ path: "/tmp/willy-app-logs.png", fullPage: true });
  await proposalTab.click();
  const proposalInput = desktop.locator("#proposal-composer-input");
  if (await proposalInput.getAttribute("placeholder") !== "输入模拟体系、项目简介、分子库查询") throw new Error("proposal placeholder mismatch");
  await proposalInput.fill("请展示 EQ 过程");
  const proposalRestored = desktop.waitForResponse((response) => new URL(response.url()).pathname === "/api/proposals/plan__visual");
  await desktop.getByRole("button", { name: "发送" }).first().click();
  await proposalRestored;
  await desktop.locator('[aria-label="模拟方案"]').waitFor();
  if (await desktop.locator(".proposal-plan-card").count() !== 3) throw new Error("proposal overview does not contain three core cards");
  if (await desktop.locator(".proposal-plan-row").count() !== 12) throw new Error("proposal plan does not contain structure, environment, EM, EQ, and PROD rows");
  await desktop.locator('[aria-label="MD 模拟流程"]').waitFor();
  await desktop.getByText("NPT · 1 ns · 500K→500K", { exact: true }).waitFor();
  await desktop.locator(".proposal-plan-row-main span").filter({ hasText: "b3lyp/6-311+g(d,p)" }).waitFor();
  assert.match(await desktop.locator(".proposal-plan-card").first().textContent(), /SMD: Acetone（builtin）.*覆盖原始 SCRF/);
  await desktop.getByText("确认无误后回复“运行”即可开始。", { exact: true }).waitFor();
  const proposalTypography = await desktop.locator(".proposal-plan").evaluate((plan) => ({
    heading: getComputedStyle(plan.querySelector(".proposal-plan-heading")).fontSize,
    index: getComputedStyle(plan.querySelector(".proposal-plan-index")).fontSize,
    rowNumber: getComputedStyle(plan.querySelector(".proposal-plan-row-number")).fontSize,
    rowLabel: getComputedStyle(plan.querySelector(".proposal-plan-row-main strong")).fontSize,
    rowMeta: getComputedStyle(plan.querySelector(".proposal-plan-row-main span")).fontSize,
    confirmation: getComputedStyle(document.querySelector(".proposal-confirmation")).fontSize,
  }));
  if (JSON.stringify(proposalTypography) !== JSON.stringify({ heading: "13px", index: "10px", rowNumber: "11px", rowLabel: "12px", rowMeta: "11px", confirmation: "13px" })) {
    throw new Error(`proposal typography mismatch: ${JSON.stringify(proposalTypography)}`);
  }
  await desktop.screenshot({ path: "/tmp/willy-app-proposal.png", fullPage: true });

  const statusSummaryMaxHeight = await desktop.locator(".status-summary").evaluate((element) => getComputedStyle(element).maxHeight);
  if (Number.parseInt(statusSummaryMaxHeight, 10) < 220) throw new Error(`current status area is too short: ${statusSummaryMaxHeight}`);
  await desktop.getByRole("button", { name: "刷新状态", exact: true }).click();
  await desktop.getByRole("button", { name: "已刷新", exact: true }).waitFor();
  await desktop.getByRole("button", { name: "收起运行检查器" }).click();
  const inspectorToggle = desktop.getByRole("button", { name: "展开运行检查器" });
  if (await inspectorToggle.getAttribute("aria-expanded") !== "false") throw new Error("run inspector did not collapse");
  await desktop.screenshot({ path: "/tmp/willy-app-desktop.png", fullPage: true });
  const desktopResult = await desktop.evaluate((currentStatusMaxHeight) => ({
    height: document.body.scrollHeight,
    activeNav: document.querySelector(".nav-item.active")?.textContent?.trim(),
    tabs: [...document.querySelectorAll("[role=tab]")].map((tab) => ({ label: tab.textContent?.trim(), selected: tab.getAttribute("aria-selected") })),
    runs: [...document.querySelectorAll(".run-directory")].map((entry) => entry.textContent?.trim()),
    currentStatusMaxHeight,
  }), statusSummaryMaxHeight);
  if (errors.length || desktopResult.activeNav !== "本地任务" || desktopResult.tabs.length !== 4 || desktopResult.runs.length !== 2 || Number.parseInt(desktopResult.currentStatusMaxHeight, 10) < 220) {
    throw new Error(`desktop validation failed: ${JSON.stringify({ errors, desktopResult })}`);
  }

  const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
  mobile.on("pageerror", (error) => errors.push(error.message));
  const mobileFixtures = await installApiFixtures(mobile);
  await mobile.goto("http://127.0.0.1:4175", { waitUntil: "networkidle" });
  await checkSolventModal(mobile, mobileFixtures, "mobile");
  await checkMoleculeCatalogDialog(mobile, mobileFixtures, "mobile");
  await mobile.screenshot({ path: "/tmp/willy-app-mobile.png", fullPage: true });
  await mobile.getByRole("button", { name: "展开详情" }).click();
  await mobile.getByText("LOCAL WORKSPACE", { exact: true }).waitFor();
  await mobile.locator(".mobile-brand").getByRole("button", { name: "展开详情", exact: true }).click();
  await mobile.getByRole("tab", { name: "方案助理", exact: true }).click();
  const mobileProposalInput = mobile.locator("#proposal-composer-input");
  await mobileProposalInput.fill("建立 Li 模拟方案");
  const mobileProposalRestored = mobile.waitForResponse((response) => new URL(response.url()).pathname === "/api/proposals/plan__visual");
  await mobile.getByRole("button", { name: "发送" }).first().click();
  await mobileProposalRestored;
  await mobile.locator('[aria-label="模拟方案"]').waitFor();
  await mobile.getByRole("button", { name: "展开详情" }).click();
  const mobileResult = await mobile.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
    sidebarVisible: getComputedStyle(document.querySelector(".sidebar")).display !== "none",
    drawerOpen: document.querySelector(".detail-drawer")?.getAttribute("aria-hidden") === "false",
    cards: document.querySelectorAll(".proposal-plan-card").length,
  }));
  if (errors.length || mobileResult.width > mobileResult.viewport || mobileResult.sidebarVisible || !mobileResult.drawerOpen || mobileResult.cards !== 3) {
    throw new Error(`mobile validation failed: ${JSON.stringify({ errors, mobileResult })}`);
  }
  console.log(JSON.stringify({ desktop: desktopResult, mobile: mobileResult }));
} finally {
  await browser.close();
  await new Promise((resolveClose) => server.close(resolveClose));
}
