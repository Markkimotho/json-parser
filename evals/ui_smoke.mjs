// End-to-end smoke test of the Developer Engine dashboard in headless Chrome.
//
//   node evals/ui_smoke.mjs [baseUrl] [outDir]
//
// Starts Chrome with the DevTools protocol (Node 22+ has a built-in
// WebSocket), drives every tab, asserts on the DOM, fails on any console
// error, and saves screenshots to outDir. Needs Google Chrome or Chromium
// (override the binary with CHROME=/path/to/chrome) and a running server
// (PORT=5055 python app.py).
import { spawn } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2] ?? "http://127.0.0.1:5055";
const OUT = process.argv[3] ?? join(tmpdir(), "jde-ui-smoke");
const CHROME = process.env.CHROME ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = 9333;
mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  (${detail})` : ""}`);
}

const chrome = spawn(CHROME, [
  "--headless=new", `--remote-debugging-port=${PORT}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "jde-chrome-"))}`,
  "--no-first-run", "--no-default-browser-check", "--window-size=1440,960", "about:blank",
], { stdio: "ignore" });

let ws;
try {
  let targets;
  for (let i = 0; i < 50 && !targets; i++) {
    try {
      targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json();
    } catch {
      await sleep(200);
    }
  }
  const page = targets.find((t) => t.type === "page");
  ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolve) => ws.addEventListener("open", resolve, { once: true }));
  let id = 0;
  const pending = new Map();
  const consoleErrors = [];
  ws.addEventListener("message", (message) => {
    const data = JSON.parse(message.data);
    if (data.id && pending.has(data.id)) {
      pending.get(data.id)(data);
      pending.delete(data.id);
    } else if (data.method === "Runtime.exceptionThrown") {
      consoleErrors.push(data.params.exceptionDetails.exception?.description ?? data.params.exceptionDetails.text);
    } else if (data.method === "Runtime.consoleAPICalled" && data.params.type === "error") {
      consoleErrors.push(data.params.args.map((a) => a.value ?? a.description).join(" "));
    } else if (data.method === "Log.entryAdded" && data.params.entry.level === "error"
               && !/status of 400/.test(data.params.entry.text)) {  // 400s are the API answering bad input on purpose
      consoleErrors.push(`${data.params.entry.text} ${data.params.entry.url ?? ""}`);
    }
  });
  const send = (method, params = {}) => new Promise((resolve) => {
    const callId = ++id;
    pending.set(callId, resolve);
    ws.send(JSON.stringify({ id: callId, method, params }));
  });
  const evaluate = async (expression) => {
    const response = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (response.result?.exceptionDetails) throw new Error(response.result.exceptionDetails.exception?.description);
    return response.result?.result?.value;
  };
  const waitFor = async (expression, timeout = 8000) => {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      try {
        if (await evaluate(expression)) return true;
      } catch {
        // The page may still be loading; keep polling.
      }
      await sleep(100);
    }
    return false;
  };
  const shot = async (name) => {
    const { result } = await send("Page.captureScreenshot", { format: "png" });
    writeFileSync(join(OUT, `${name}.png`), Buffer.from(result.data, "base64"));
  };
  const click = (selector) => evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`);
  const text = (selector) => evaluate(`document.querySelector(${JSON.stringify(selector)})?.textContent ?? ""`);
  const loadSample = async (name) => {
    await evaluate(`(() => { const s = document.querySelector('#sample-select'); s.value = ${JSON.stringify(name)}; s.dispatchEvent(new Event('change')); })()`);
    await sleep(150);
    await waitFor(`!['WORKING'].includes(document.querySelector('#status-badge').textContent)`);
    await sleep(250);
  };

  await send("Runtime.enable");
  await send("Log.enable");
  await send("Page.enable");
  await send("Emulation.setDeviceMetricsOverride", { width: 1440, height: 960, deviceScaleFactor: 1, mobile: false });
  await send("Page.navigate", { url: `${BASE}/engine/` });
  await waitFor(`document.readyState === "complete"`);
  check("dashboard loads and compiles the default sample",
    await waitFor(`['SECURE','WARNING','CRITICAL'].includes(document.querySelector('#status-badge').textContent)`),
    await text("#status-badge"));
  await evaluate(`localStorage.clear()`);

  // AST
  await click("#tab-ast");
  const initialRows = await evaluate(`document.querySelectorAll('#ast-tree summary, #ast-tree .leaf').length`);
  check("AST tree renders the root and its members", initialRows === 5, `${initialRows} rows`);
  await click("#ast-expand");
  const rows = await evaluate(`document.querySelectorAll('#ast-tree summary, #ast-tree .leaf').length`);
  check("Expand all reveals every value node", rows === 39, `${rows} rows`);
  await evaluate(`document.querySelector('#ast-tree .leaf').click()`);
  const selection = await evaluate(`(() => { const e = document.querySelector('#editor'); return e.value.slice(e.selectionStart, e.selectionEnd); })()`);
  check("clicking a leaf selects its source in the editor", selection.length > 0, JSON.stringify(selection));
  await shot("01-ast");

  // Tracer
  await click("#tab-trace");
  const tokens = await evaluate(`document.querySelectorAll('#token-stream .tok').length`);
  check("token stream renders", tokens > 50, `${tokens} tokens`);
  for (let i = 0; i < 12; i++) await click("#trace-step");
  check("Step advances the tracer", (await text("#trace-counter")).startsWith("13 /"), await text("#trace-counter"));
  check("call stack shows parse_object()", (await text("#trace-stack")).includes("parse_object()"));
  check("source pointer highlights the current token", (await evaluate(`document.querySelector('#trace-source mark')?.textContent.length`)) > 0);
  check("current token chip is highlighted", await evaluate(`!!document.querySelector('#token-stream .tok.current')`));
  await click("#trace-play");
  await sleep(600);
  await click("#trace-play");
  const played = Number((await text("#trace-counter")).split(" / ")[0]);
  check("Play animates through steps", played > 13, `${played}`);
  await shot("02-tracer");

  // Polyglot
  await click("#tab-polyglot");
  check("TypeScript interface generated", (await text("#poly-code")).includes("export interface RootObject"));
  await click('[data-lang="pydantic"]');
  check("Pydantic tab shows BaseModel", (await text("#poly-code")).includes("(BaseModel)"));
  await click('[data-lang="go"]');
  check("Go tab shows struct tags", (await text("#poly-code")).includes('`json:"'));
  await shot("03-polyglot");

  // Compressor
  await click("#tab-compress");
  check("compressor shows compact table output", (await text("#cmp-output")).includes("users[3]"));
  check("compressor shows token savings", (await text("#cmp-stats")).includes("%"));
  await shot("04-compress");

  // JSONPath
  await click("#tab-jsonpath");
  await evaluate(`(() => { const i = document.querySelector('#jp-input'); i.value = '$.users[?@.metadata.active == true].name'; document.querySelector('#jp-form').requestSubmit(); })()`);
  check("JSONPath query prints matches", await waitFor(`document.querySelector('#jp-output').textContent.includes('2 matches')`),
    (await text("#jp-output")).slice(-120));
  await evaluate(`(() => { const i = document.querySelector('#jp-input'); i.value = '$.users[?@.x =='; document.querySelector('#jp-form').requestSubmit(); })()`);
  check("JSONPath errors are reported with a caret", await waitFor(`document.querySelector('#jp-output .err')?.textContent.includes('^')`));
  await shot("05-jsonpath");

  // Healing
  await loadSample("broken");
  check("broken sample heals: HEALED chip visible", await evaluate(`!document.querySelector('#healed-chip').hidden`));
  await click("#tab-fuzzy");
  const repairRows = await evaluate(`document.querySelectorAll('#fuzzy-log tr.clickable').length`);
  check("Fuzzy log lists repairs", repairRows >= 8, `${repairRows} repairs`);
  check("diff shows added and removed lines", await evaluate(`!!document.querySelector('#fuzzy-diff .add') && !!document.querySelector('#fuzzy-diff .del')`));
  await evaluate(`document.querySelector('#fuzzy-log tr.clickable').click()`);
  const repairSel = await evaluate(`(() => { const e = document.querySelector('#editor'); return e.value.slice(e.selectionStart, e.selectionEnd); })()`);
  check("clicking a repair selects the original text", repairSel.length > 0, JSON.stringify(repairSel));
  await shot("06-fuzzy");
  await click("#fuzzy-apply");
  await sleep(200);
  check("Apply healed JSON makes the editor strict-valid",
    await waitFor(`document.querySelector('#healed-chip').hidden && document.querySelector('#status-badge').textContent !== 'INVALID'`));

  // Strict mode error banner
  await evaluate(`document.querySelector('#opt-lenient').click()`);
  await evaluate(`(() => { const e = document.querySelector('#editor'); e.value = '{"a": 1,}'; e.dispatchEvent(new Event('input')); })()`);
  check("strict mode reports INVALID with an error banner",
    await waitFor(`document.querySelector('#status-badge').textContent === 'INVALID' && !document.querySelector('#error-banner').hidden`),
    await text("#error-banner"));
  await evaluate(`(() => { const e = document.querySelector('#editor'); e.value = '{"role": "user", "role": "admin"}'; e.dispatchEvent(new Event('input')); })()`);
  check("strict mode still audits duplicate keys the parser rejects",
    await waitFor(`document.querySelector('#audit-badge').textContent === 'CRITICAL' && document.querySelector('#status-badge').textContent === 'INVALID'`),
    `${await text("#audit-badge")} / ${await text("#status-badge")}`);
  await evaluate(`document.querySelector('#opt-lenient').click()`);

  // Audit
  await loadSample("risky");
  await click("#tab-audit");
  check("risky sample audits CRITICAL", (await text("#audit-badge")) === "CRITICAL", await text("#audit-summary"));
  const failing = await evaluate(`document.querySelectorAll('#audit-checks .s-critical, #audit-checks .s-warning').length`);
  check("all six audit checks fire on the risky sample", failing === 6,
    `${failing} failing: ${await evaluate(`[...document.querySelectorAll('#audit-checks .check')].map(c => c.className).join(', ')`)}`);
  await shot("07-audit");

  // Schema to types
  await loadSample("schema");
  await click("#tab-polyglot");
  await click('[data-lang="typescript"]');
  check("JSON Schema is detected", (await text("#poly-meta")).includes("JSON Schema"));
  check("schema $defs become named types", (await text("#poly-code")).includes("export interface LineItem"));

  // Theme + mobile layout
  await click("#theme-toggle");
  await shot("08-theme-toggled");
  await send("Emulation.setDeviceMetricsOverride", { width: 390, height: 844, deviceScaleFactor: 2, mobile: true });
  await sleep(300);
  const overflow = await evaluate(`document.documentElement.scrollWidth - document.documentElement.clientWidth`);
  check("no horizontal page scroll at phone width", overflow <= 0, `${overflow}px`);
  await shot("09-mobile");

  check("no console errors", consoleErrors.length === 0, consoleErrors.join(" | "));
} catch (error) {
  check("smoke run completed", false, error.stack);
} finally {
  ws?.close();
  chrome.kill();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed · screenshots in ${OUT}`);
process.exit(failed.length ? 1 : 0);
