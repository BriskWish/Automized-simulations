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
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const send = (json, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(json) });
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
        { role: "assistant", content: snapshot.live_summary, _run_assistant_event_kind: "status" },
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
      return send({ text: "方案助理响应", run_id: runId });
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
        events: { filename: "events.jsonl", truncated: false, content: '{"kind":"status_updated","state":"running"}' },
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
}

await new Promise((resolveListen) => server.listen(4175, "127.0.0.1", resolveListen));
const browser = await chromium.launch({ headless: true });
try {
  const desktop = await browser.newPage({ viewport: { width: 1440, height: 940 }, deviceScaleFactor: 1 });
  const errors = [];
  desktop.on("pageerror", (error) => errors.push(error.message));
  await installApiFixtures(desktop);
  await desktop.goto("http://127.0.0.1:4175", { waitUntil: "networkidle" });

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
  await desktop.getByRole("button", { name: "上传结构", exact: true }).waitFor();
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
  await desktop.getByLabel("Events 内容").waitFor();
  if (await desktop.getByLabel("Manifest 内容").textContent() !== '{\n  "schema_version": 2,\n  "run_id": "md__202608260002"\n}') throw new Error("manifest record was not rendered");
  if (await desktop.getByLabel("Events 内容").textContent() !== '{"kind":"status_updated","state":"running"}') throw new Error("events record was not rendered");
  const logColumns = desktop.locator(".log-column");
  const logColumnBoxes = await logColumns.evaluateAll((columns) => columns.map((column) => column.getBoundingClientRect()));
  if (await logColumns.count() !== 2 || logColumnBoxes[0].x >= logColumnBoxes[1].x) throw new Error("logs are not arranged in two columns");
  await desktop.screenshot({ path: "/tmp/willy-app-logs.png", fullPage: true });
  await proposalTab.click();
  const proposalInput = desktop.locator("#proposal-composer-input");
  if (await proposalInput.getAttribute("placeholder") !== "输入模拟体系、项目简介、分子库查询") throw new Error("proposal placeholder mismatch");

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
  await installApiFixtures(mobile);
  await mobile.goto("http://127.0.0.1:4175", { waitUntil: "networkidle" });
  await mobile.screenshot({ path: "/tmp/willy-app-mobile.png", fullPage: true });
  await mobile.getByRole("button", { name: "展开详情" }).click();
  await mobile.getByText("LOCAL WORKSPACE", { exact: true }).waitFor();
  const mobileResult = await mobile.evaluate(() => ({
    width: document.documentElement.scrollWidth,
    viewport: window.innerWidth,
    sidebarVisible: getComputedStyle(document.querySelector(".sidebar")).display !== "none",
    drawerOpen: document.querySelector(".detail-drawer")?.getAttribute("aria-hidden") === "false",
  }));
  if (mobileResult.width > mobileResult.viewport || mobileResult.sidebarVisible || !mobileResult.drawerOpen) {
    throw new Error(`mobile validation failed: ${JSON.stringify(mobileResult)}`);
  }
  console.log(JSON.stringify({ desktop: desktopResult, mobile: mobileResult }));
} finally {
  await browser.close();
  await new Promise((resolveClose) => server.close(resolveClose));
}
