// JSON Developer Engine dashboard controller. Talks to the Flask API; all
// rendering uses DOM nodes and textContent (never innerHTML with data).
import {
  buildCallIndex,
  debounce,
  diffLines,
  firstStepForToken,
  formatBytes,
  indexAst,
  nodePreview,
  offsetMapper,
  offsetToLineColumn,
  lineColumnToOffset,
  sourceWindow,
  stackAt,
  suggestQueries,
  utf8Length,
} from "./lib.js";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

/** Create an element: h("span", {class: "x", onclick}, "text", child). */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : String(child));
  }
  return el;
}

const LIVE_LIMIT = 200 * 1024;
const MAX_FILE = 10 * 1024 * 1024;
const TOKEN_CHIP_LIMIT = 4000;
const CHILD_CHUNK = 200;

const SAMPLES = {
  api: {
    name: "api-response.json",
    text: JSON.stringify({
      status: "ok",
      requestId: "req_8f2c",
      users: [
        { id: 1, name: "Ada Lovelace", email: "ada@example.com", roles: ["admin", "author"], metadata: { active: true, lastLogin: "2026-10-16T09:12:00Z" } },
        { id: 2, name: "Linus Torvalds", email: null, roles: ["author"], metadata: { active: false, lastLogin: null } },
        { id: 3, name: "Grace Hopper", email: "grace@example.com", roles: [], metadata: { active: true, lastLogin: "2026-10-17T07:45:00Z" }, team: { name: "Compilers", size: 12 } },
      ],
      pagination: { page: 1, perPage: 3, total: 128, next: "/users?page=2" },
    }, null, 2),
  },
  broken: {
    name: "broken.jsonc",
    text: `// Config exported from a hand-edited file
{
  name: 'json-engine',
  version: "3.0.0",
  /* feature flags */
  features: {
    healing: True,
    telemetry: None,
    retries: 3
    timeout_ms: 1500,
  },
  'tags': ['parser', 'compiler', 'llm',],
  "owner": "Mark",
  "owner": "Mark K."
}`,
  },
  // Written as text: a JS object literal cannot hold a duplicate key, turns
  // "__proto__" into the prototype, and would round the big numbers.
  risky: {
    name: "risky-payload.json",
    text: `{
  "user": "mallory",
  "role": "viewer",
  "role": "admin",
  "__proto__": { "isAdmin": true },
  "constructor": { "prototype": { "polluted": true } },
  "orderId": 9007199254740993,
  "ratio": 0.1000000000000000055511151231257827,
  "label": "\\ud800 unpaired surrogate",
  "deep": ${"[".repeat(28)}1${"]".repeat(28)}
}`,
  },
  schema: {
    name: "order.schema.json",
    text: JSON.stringify({
      $schema: "https://json-schema.org/draft/2020-12/schema",
      title: "Order",
      description: "A customer order",
      type: "object",
      required: ["id", "status", "items"],
      properties: {
        id: { type: "string", description: "Order identifier" },
        status: { enum: ["open", "paid", "shipped", null] },
        items: { type: "array", items: { $ref: "#/$defs/LineItem" } },
        customer: { $ref: "#/$defs/Customer" },
        notes: { type: ["string", "null"] },
        metadata: { type: "object", additionalProperties: { type: "string" } },
      },
      $defs: {
        LineItem: { type: "object", required: ["sku", "qty"], properties: { sku: { type: "string" }, qty: { type: "integer" }, price: { type: "number" } } },
        Customer: { type: "object", required: ["name"], properties: { name: { type: "string" }, vip: { type: "boolean" } } },
      },
    }, null, 2),
  },
  bookstore: {
    name: "bookstore.json",
    text: JSON.stringify({
      store: {
        book: [
          { category: "reference", author: "Nigel Rees", title: "Sayings of the Century", price: 8.95 },
          { category: "fiction", author: "Evelyn Waugh", title: "Sword of Honour", price: 12.99 },
          { category: "fiction", author: "Herman Melville", title: "Moby Dick", isbn: "0-553-21311-3", price: 8.99 },
          { category: "fiction", author: "J. R. R. Tolkien", title: "The Lord of the Rings", isbn: "0-395-19395-8", price: 22.99 },
        ],
        bicycle: { color: "red", price: 399 },
      },
    }, null, 2),
  },
};

const state = {
  seq: 0,
  compiledText: "", // editor text that was compiled
  visualText: "", // text the tokens/trace/AST describe (healed text when healing ran)
  doc: null, // valid JSON text the other engines analyse
  auditText: null, // text the audit findings point into
  parse: null,
  heal: null,
  astIndex: new Map(),
  mapVisual: (i) => i,
  trace: [],
  callIndex: null,
  step: 0,
  timer: null,
  types: null,
  lang: "typescript",
  compress: null,
  cmpMode: "compact",
  history: [],
  historyPos: 0,
};

// ------------------------------------------------------------------ helpers

async function api(route, body) {
  let response;
  try {
    response = await fetch(route, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (error) {
    return { ok: false, status: 0, body: { error: `Network error: ${error.message}` } };
  }
  let data;
  try {
    data = await response.json();
  } catch {
    data = { error: `HTTP ${response.status}` };
  }
  return { ok: response.ok, status: response.status, body: data };
}

function store(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {}
}
function recall(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

let flashTimer;
function flash(message) {
  const meta = $("#parse-meta");
  meta.textContent = message;
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => (meta.textContent = state.parse?.stats ? parseMeta() : ""), 4500);
}

async function copy(text, what) {
  try {
    await navigator.clipboard.writeText(text);
    flash(`${what} copied`);
  } catch {
    flash("Clipboard unavailable in this browser");
  }
}

function setBadge(status) {
  const badge = $("#status-badge");
  badge.dataset.status = status;
  badge.textContent = status;
}

// ------------------------------------------------------------------- editor

const editor = $("#editor");
const gutter = $("#gutter");
let gutterLines = 0;

function updateGutter() {
  const lines = editor.value.split("\n").length;
  if (lines !== gutterLines) {
    gutterLines = lines;
    gutter.textContent = Array.from({ length: lines }, (_, i) => i + 1).join("\n");
  }
  gutter.scrollTop = editor.scrollTop;
}

const updateFooter = debounce(() => {
  $("#doc-bytes").textContent = formatBytes(utf8Length(editor.value));
}, 150);

function updateCursor() {
  const { line, column } = offsetToLineColumn(editor.value, editor.selectionStart);
  $("#cursor-pos").textContent = `Ln ${line}, Col ${column}`;
}

function selectEditorRange(start, end) {
  editor.focus({ preventScroll: true });
  editor.setSelectionRange(start, end);
  const line = editor.value.slice(0, start).split("\n").length;
  const lineHeight = parseFloat(getComputedStyle(editor).lineHeight) || 20;
  editor.scrollTop = Math.max(0, (line - 4) * lineHeight);
  updateGutter();
  updateCursor();
}

/** Select a range given in code points of the visual (tokens/AST) text. */
function selectVisual(start, end) {
  if (editor.value !== state.visualText) {
    flash(
      state.heal && state.visualText === state.heal.healed
        ? "Positions refer to the healed JSON. Apply it to the editor to navigate."
        : "The editor changed since the last compile. Recompile to navigate.",
    );
    return;
  }
  selectEditorRange(state.mapVisual(start), state.mapVisual(end));
}

/** Select line/column positions reported against the audited text. */
function selectDocPosition(line, column, length = 1) {
  if (editor.value !== state.auditText) {
    flash("Positions refer to the healed JSON. Apply it to the editor to navigate.");
    return;
  }
  const start = lineColumnToOffset(editor.value, line, column);
  selectEditorRange(start, start + length);
}

function selectOriginalPosition(index, length) {
  if (editor.value !== state.compiledText) {
    flash("The editor changed since the last compile. Recompile to navigate.");
    return;
  }
  const map = offsetMapper(editor.value);
  selectEditorRange(map(index), map(index + Math.max(length, 1)));
}

function setDocument(text, name) {
  editor.value = text;
  if (name) $("#doc-name").textContent = name;
  editor.scrollTop = 0;
  updateGutter();
  updateCursor();
  updateFooter();
  compile();
}

// ------------------------------------------------------------------ compile

function parseMeta() {
  const stats = state.parse?.stats;
  if (!stats) return "";
  return `${stats.tokens.toLocaleString()} tokens · depth ${stats.max_depth} · ${stats.parse_ms} ms`;
}

async function compile() {
  const text = editor.value;
  const seq = ++state.seq;
  stopPlayback();
  if (!text.trim()) {
    resetViews("Paste JSON, drop a file, or pick a sample.");
    return;
  }
  setBadge("WORKING");
  const lenient = $("#opt-lenient").checked;
  // The auditor reads the original text: it records duplicate keys that the
  // strict parser rejects and that healing would silently remove.
  const originalAudit = api("/audit-json", { jsonData: text });
  const { ok, body } = await api("/parse-json", { jsonData: text, lenient });
  if (seq !== state.seq) return;
  state.compiledText = text;
  state.parse = body;
  state.heal = body.healing?.repairs?.length || body.healing?.healed_text
    ? { original: text, healed: body.healing.healed_text, repairs: body.healing.repairs, applied: body.healing.applied,
        originalError: body.healing.original_error, error: ok ? null : body }
    : null;
  state.visualText = body.healing?.applied ? body.healing.healed_text : text;
  state.doc = ok ? state.visualText : null;
  state.mapVisual = offsetMapper(state.visualText);
  $("#healed-chip").hidden = !body.healing?.applied;

  renderError(ok ? null : body);
  renderAst(body);
  renderTrace(body);
  renderFuzzy();
  $("#parse-meta").textContent = ok ? parseMeta() : "";

  if (!ok) {
    setBadge("INVALID");
    const reason = lenient ? "Healing could not repair this document." : "Fix the syntax error or enable Lenient mode.";
    const audit = await originalAudit;
    if (seq !== state.seq) return;
    if (audit.ok) {
      state.auditText = text;
      renderAudit(audit.body, { note: "Audited the editor text. The strict parser rejects it, but the auditor can still read it.", keepBadge: true });
    } else {
      renderAuditMessage(reason);
    }
    $("#poly-code").textContent = reason;
    $("#poly-meta").textContent = "";
    $("#cmp-output").textContent = reason;
    $("#cmp-stats").replaceChildren();
    return;
  }
  const doc = state.doc;
  const [first, types, compress] = await Promise.all([originalAudit, requestTypes(doc), api("/compress-json", { jsonData: doc })]);
  if (seq !== state.seq) return;
  let audit = first;
  let note = "";
  state.auditText = text;
  if (!first.ok && doc !== text) {
    audit = await api("/audit-json", { jsonData: doc });
    if (seq !== state.seq) return;
    state.auditText = doc;
    note = "Audited the healed document: the editor text has syntax errors the auditor cannot read.";
  }
  renderAudit(audit.body, { note });
  renderTypes(types);
  state.compress = compress.ok ? compress.body.modes : null;
  renderCompress(compress.ok ? null : compress.body.error);
  renderExamples();
}

function resetViews(message) {
  state.parse = null;
  state.doc = null;
  state.auditText = null;
  state.heal = null;
  setBadge("READY");
  $("#healed-chip").hidden = true;
  renderError(null);
  $("#ast-tree").replaceChildren();
  $("#ast-meta").textContent = message;
  renderTrace({ trace: [], tokens: [] });
  renderFuzzy();
  renderAuditMessage(message);
  $("#poly-code").textContent = message;
  $("#cmp-output").textContent = message;
  $("#cmp-stats").replaceChildren();
}

function renderError(body) {
  const banner = $("#error-banner");
  if (!body) {
    banner.hidden = true;
    banner.replaceChildren();
    return;
  }
  const parts = [h("strong", {}, "Syntax error"), h("span", {}, body.error)];
  if (body.line) {
    parts.push(h("button", { class: "btn btn-small", type: "button", onclick: () => selectOriginalPosition(
      body.healing ? (body.healing.original_error?.index ?? 0) : body.index ?? 0, 1) },
    `Ln ${body.healing?.original_error?.line ?? body.line}, Col ${body.healing?.original_error?.column ?? body.column}`));
  }
  if (!$("#opt-lenient").checked) {
    parts.push(h("button", { class: "btn btn-small", type: "button", onclick: healNow }, "Heal it"));
  }
  banner.replaceChildren(...parts);
  banner.hidden = false;
}

// --------------------------------------------------------------------- AST

function renderAst(body) {
  const tree = $("#ast-tree");
  tree.replaceChildren();
  state.astIndex = indexAst(body.ast);
  if (!body.ast) {
    $("#ast-meta").textContent = !body.valid
      ? "No tree: the document did not parse."
      : body.visual?.ast_truncated
        ? `Tree omitted: more than ${body.visual.limits.ast_nodes.toLocaleString()} nodes.`
        : body.visual?.reason || "No tree available.";
    return;
  }
  const source = body.healing?.applied ? "healed document" : "editor";
  $("#ast-meta").textContent = `${body.stats.nodes.toLocaleString()} nodes · max depth ${body.stats.max_depth} · positions from the ${source}`;
  tree.append(renderNode(body.ast, { open: true }));
}

function nodeHeader(node, label, owner) {
  const parts = [h("span", { class: `kind k-${node.kind}` }, node.kind)];
  if (label) parts.push(h("span", { class: "key" }, label));
  parts.push(h("span", { class: "preview" }, nodePreview(node)));
  if (owner?.duplicate) parts.push(h("span", { class: "dup", title: "Duplicate key" }, "duplicate"));
  parts.push(h("span", { class: "pos" }, `L${owner?.line ?? node.line}:${owner?.column ?? node.column}`));
  return parts;
}

function renderNode(node, { open = false, label = null, owner = null } = {}) {
  if (node.kind === "property") {
    const value = node.children?.[1];
    if (!value) return h("div", { class: "leaf" }, h("span", { class: "key" }, node.key));
    return renderNode(value, { label: JSON.stringify(node.key), owner: node });
  }
  if (node.index !== undefined && label === null) label = `[${node.index}]`;
  const range = owner ?? node;
  const select = (event) => {
    event.preventDefault();
    $$(".tree .selected").forEach((el) => el.classList.remove("selected"));
    event.currentTarget.classList.add("selected");
    selectVisual(range.start, node.end);
  };
  if (node.kind !== "object" && node.kind !== "array") {
    return h("div", { class: "leaf", role: "treeitem", title: node.path, onclick: select }, nodeHeader(node, label, owner));
  }
  const summary = h("summary", { role: "treeitem", title: node.path }, nodeHeader(node, label, owner));
  summary.addEventListener("dblclick", select);
  summary.addEventListener("click", (event) => {
    if (event.altKey || event.metaKey) select(event);
  });
  const details = h("details", {}, summary);
  details._node = node;
  details.addEventListener("toggle", () => details.open && ensureChildren(details));
  if (open) {
    details.open = true;
    ensureChildren(details);
  }
  return details;
}

function ensureChildren(details, from = 0) {
  if (details._rendered && from === 0) return;
  details._rendered = true;
  const children = details._node.children;
  const end = Math.min(children.length, from + CHILD_CHUNK);
  for (let i = from; i < end; i++) details.append(renderNode(children[i]));
  if (end < children.length) {
    const more = h("button", { class: "btn btn-small more", type: "button" }, `Show ${Math.min(CHILD_CHUNK, children.length - end)} more of ${children.length - end}`);
    more.addEventListener("click", () => {
      more.remove();
      ensureChildren(details, end);
    });
    details.append(more);
  }
}

function expandAll() {
  const queue = $$("#ast-tree details");
  let opened = 0;
  while (queue.length && opened < 3000) {
    const details = queue.shift();
    details.open = true;
    ensureChildren(details);
    opened++;
    queue.push(...details.querySelectorAll(":scope > details"));
  }
  if (queue.length) flash("Expanded the first 3,000 containers");
}

// ------------------------------------------------------------------- tracer

function renderTrace(body) {
  state.trace = body.trace ?? [];
  state.callIndex = buildCallIndex(state.trace);
  state.step = 0;
  const tokens = body.tokens ?? [];
  const stream = $("#token-stream");
  stream.replaceChildren(
    ...tokens.slice(0, TOKEN_CHIP_LIMIT).map((token) =>
      h("span", {
        class: `tok t-${token.type}`, role: "listitem", dataset: { index: token.index },
        title: `#${token.index} ${token.type} · L${token.line}:${token.column} · [${token.start_index}, ${token.end_index})`,
        onclick: () => jumpToToken(token),
      },
      h("span", { class: "ti" }, token.index),
      h("span", { class: "tv" }, token.type === "EOF" ? "EOF" : token.raw)),
    ),
  );
  const notes = [];
  if (tokens.length) notes.push(`${tokens.length.toLocaleString()} tokens`);
  if (tokens.length > TOKEN_CHIP_LIMIT) notes.push(`showing first ${TOKEN_CHIP_LIMIT.toLocaleString()}`);
  if (body.visual?.tokens_truncated) notes.push("capture truncated");
  if (body.visual?.trace_truncated) notes.push(`trace capped at ${body.visual.limits.trace.toLocaleString()} steps`);
  if (body.visual && !body.visual.available) notes.push(body.visual.reason);
  $("#token-meta").textContent = notes.join(" · ");
  const scrub = $("#trace-scrub");
  scrub.max = Math.max(0, state.trace.length - 1);
  renderStep();
}

function jumpToToken(token) {
  const step = firstStepForToken(state.trace, token.index);
  if (step >= 0) {
    state.step = step;
    renderStep();
  }
  selectVisual(token.start_index, token.end_index);
}

let currentChip = null;
function renderStep() {
  const trace = state.trace;
  const total = trace.length;
  $("#trace-counter").textContent = total ? `${state.step + 1} / ${total}` : "0 / 0";
  $("#trace-scrub").value = state.step;
  $("#trace-back").disabled = state.step <= 0;
  $("#trace-step").disabled = !total || state.step >= total - 1;
  const eventBox = $("#trace-event");
  const stack = $("#trace-stack");
  const source = $("#trace-source");
  if (!total) {
    eventBox.textContent = "No trace yet. Compile a document under 512 KB.";
    stack.replaceChildren();
    source.textContent = "";
    $("#trace-pos").textContent = "";
    return;
  }
  const event = trace[state.step];
  eventBox.replaceChildren(
    h("div", {}, h("span", { class: `action a-${event.action}` }, event.action), h("strong", {}, event.method)),
    event.detail ? h("div", { class: "muted" }, event.detail) : null,
    h("div", { class: "muted" }, `token #${event.token_index} ${event.token_type}${event.token_value !== null && event.token_type !== "EOF" ? ` ${JSON.stringify(event.token_value)}` : ""}`),
    h("div", { class: "muted" }, `depth ${event.depth} · index ${event.start_index}`),
  );
  stack.replaceChildren(...stackAt(trace, state.callIndex, state.step).map((method) => h("li", {}, method)));
  const start = state.mapVisual(event.start_index);
  const end = state.mapVisual(event.end_index);
  const win = sourceWindow(state.visualText, start, end, 5);
  const lines = `${win.before}\u0000${win.focus}\u0001${win.after}`.split("\n");
  const width = String(win.firstLine + lines.length).length;
  const out = [];
  lines.forEach((line, i) => {
    out.push(h("span", { class: "ln" }, `${String(win.firstLine + i).padStart(width)}  `));
    const open = line.indexOf("\u0000");
    const close = line.indexOf("\u0001");
    if (open < 0 && close < 0) out.push(line);
    else {
      const a = open < 0 ? 0 : open;
      const b = close < 0 ? line.length : close;
      out.push(line.slice(0, a).replace("\u0001", ""));
      out.push(h("mark", {}, line.slice(open < 0 ? 0 : a + 1, b) || " "));
      out.push(line.slice(close < 0 ? line.length : b + 1));
    }
    if (i < lines.length - 1) out.push("\n");
  });
  source.replaceChildren(...out);
  $("#trace-pos").textContent = `L${event.line}:${event.column}`;
  currentChip?.classList.remove("current");
  currentChip = $(`#token-stream [data-index="${event.token_index}"]`);
  if (currentChip) {
    currentChip.classList.add("current");
    if (!$("#panel-trace").hidden) currentChip.scrollIntoView({ block: "nearest", inline: "nearest" });
  }
}

function stepTo(step) {
  state.step = Math.max(0, Math.min(step, state.trace.length - 1));
  renderStep();
}

function stopPlayback() {
  clearInterval(state.timer);
  state.timer = null;
  $("#trace-play").textContent = "▶ Play";
}

function togglePlayback() {
  if (state.timer) return stopPlayback();
  if (!state.trace.length) return;
  if (state.step >= state.trace.length - 1) state.step = 0;
  $("#trace-play").textContent = "⏸ Pause";
  const tick = () => {
    if (state.step >= state.trace.length - 1) return stopPlayback();
    stepTo(state.step + 1);
  };
  state.timer = setInterval(tick, 1000 / Number($("#trace-speed").value));
}

// -------------------------------------------------------------- fuzzy logs

function renderFuzzy() {
  const heal = state.heal;
  const log = $("#fuzzy-log");
  const count = $("#fuzzy-count");
  $("#fuzzy-apply").disabled = !(heal?.healed && heal.healed !== editor.value && !heal.error);
  if (!heal) {
    $("#fuzzy-summary").textContent = "The strict parser accepted the document. No repairs were needed.";
    log.replaceChildren(h("tr", { class: "empty-row" }, h("td", { colspan: 4 }, "No repairs.")));
    $("#fuzzy-diff").replaceChildren();
    count.hidden = true;
    return;
  }
  const n = heal.repairs.length;
  count.hidden = !n;
  count.textContent = n;
  const err = heal.originalError;
  $("#fuzzy-summary").textContent = heal.error
    ? `Healing made ${n} repair(s) but the result is still invalid: ${heal.error.error}`
    : `Lenient mode repaired ${n} issue(s).${err ? ` Strict parser said: ${err.error} (Ln ${err.line}, Col ${err.column}).` : ""}`;
  log.replaceChildren(
    ...(n ? heal.repairs.map((repair) =>
      h("tr", { class: "clickable", title: "Select in editor", onclick: () => selectOriginalPosition(repair.index, repair.before.length) },
        h("td", {}, h("span", { class: "repair-kind" }, repair.kind)),
        h("td", {}, repair.message),
        h("td", { class: "mono" }, `${repair.line}:${repair.column}`),
        h("td", { class: "change" }, repair.before ? h("del", {}, repair.before) : null, " ", repair.after ? h("ins", {}, repair.after) : null))) :
      [h("tr", { class: "empty-row" }, h("td", { colspan: 4 }, "No repairs."))]),
  );
  const diff = heal.healed ? diffLines(heal.original, heal.healed) : null;
  $("#fuzzy-diff").replaceChildren(
    ...(diff ? diff.map((op) => h("span", { class: op.type }, op.text || " ")) : [h("span", { class: "same" }, "Diff skipped for very large documents.")]),
  );
}

async function healNow() {
  const text = editor.value;
  if (!text.trim()) return;
  const { body } = await api("/heal-json", { jsonData: text });
  state.heal = { original: text, healed: body.healed, repairs: body.repairs ?? [], applied: false,
                 originalError: null, error: body.valid ? null : body.error };
  if (body.strict) flash("Already valid JSON. Nothing to heal.");
  renderFuzzy();
  activateTab("fuzzy");
}

// ---------------------------------------------------------------- polyglot

function requestTypes(doc) {
  return api("/generate-types", { jsonData: doc, rootName: $("#poly-root").value || "RootObject", mode: $("#poly-mode").value });
}

function renderTypes(response) {
  if (!response.ok) {
    state.types = null;
    $("#poly-code").textContent = response.body.error;
    $("#poly-meta").textContent = "";
    return;
  }
  state.types = response.body;
  const names = response.body.names;
  $("#poly-meta").textContent = `Read as ${response.body.mode === "schema" ? "JSON Schema" : "sample document"} · ${names.length} type(s)${names.length ? `: ${names.slice(0, 6).join(", ")}${names.length > 6 ? "…" : ""}` : ""}`;
  $("#poly-code").textContent = response.body.types[state.lang];
}

const regenerateTypes = debounce(async () => {
  if (!state.doc) return;
  renderTypes(await requestTypes(state.doc));
}, 300);

// ------------------------------------------------------------------- audit

function renderAuditMessage(message) {
  const badge = $("#audit-badge");
  badge.dataset.status = state.parse && !state.parse.valid ? "INVALID" : "READY";
  badge.textContent = badge.dataset.status;
  $("#audit-summary").textContent = message;
  $("#audit-stats").textContent = "";
  $("#audit-checks").replaceChildren();
  $("#audit-count").hidden = true;
}

function renderAudit(body, { note = "", keepBadge = false } = {}) {
  const report = body.audit;
  if (!report) return renderAuditMessage(body.error ?? "Audit failed");
  const badge = $("#audit-badge");
  badge.dataset.status = report.status;
  badge.textContent = report.status;
  if (!keepBadge) setBadge(report.status);
  const { critical, warning } = report.counts;
  $("#audit-summary").textContent = critical || warning
    ? `${critical} critical and ${warning} warning finding(s) across ${report.checks.length} checks`
    : `All ${report.checks.length} checks passed`;
  const s = report.stats;
  $("#audit-stats").textContent = `${s.keys} keys · ${s.scalars} scalars · depth ${s.max_depth} · ${s.duration_ms} ms${note ? ` · ${note}` : ""}`;
  const count = $("#audit-count");
  count.hidden = !(critical || warning);
  count.textContent = critical + warning;
  const icons = { pass: "✓", warning: "!", critical: "✕" };
  $("#audit-checks").replaceChildren(
    ...report.checks.map((check) =>
      h("li", { class: `check s-${check.status}` },
        h("div", { class: "check-head" },
          h("span", { class: "check-icon", "aria-label": check.status }, icons[check.status]),
          h("span", { class: "check-title" }, check.title),
          h("span", { class: "check-summary" }, check.summary)),
        h("div", { class: "check-desc" }, check.description),
        check.findings.length ? h("ul", { class: "findings" },
          ...check.findings.map((finding) =>
            h("li", { class: "finding", title: "Select in editor", onclick: () => selectDocPosition(finding.line, finding.column, finding.detail?.raw?.length ?? finding.detail?.key?.length + 2 ?? 1) },
              h("span", { class: `sev sev-${finding.severity}` }, finding.severity),
              h("span", {}, finding.message),
              h("span", { class: "path" }, finding.path),
              h("span", { class: "muted mono" }, `L${finding.line}:${finding.column}`)))) : null)),
  );
}

// ---------------------------------------------------------------- JSONPath

function termPrint(...nodes) {
  const out = $("#jp-output");
  out.append(...nodes);
  // Block-level values end their own line.
  if (!(nodes.at(-1) instanceof Element && nodes.at(-1).classList.contains("mval"))) out.append("\n");
  out.scrollTop = out.scrollHeight;
}

function renderExamples() {
  const queries = suggestQueries(state.parse?.ast);
  $("#jp-examples").replaceChildren(
    ...queries.map((query) => h("button", { type: "button", onclick: () => runQuery(query) }, query)),
  );
}

const HELP = `JSONPath terminal: queries run against the last compiled document.
  $                 root            $.a.b / $['a b']   members
  [0] [-1] [0,2]    indexes         [1:5:2]            slices
  * / [*]           wildcard        ..name / ..[0]     descendants
  [?@.price < 10]   filters (also ?(…), && || !, == != < <= > >=, $ root refs)
  length() count() value() match() search()           RFC 9535 functions
Commands: help · clear · ↑/↓ history. Click a result path to select it in the editor.`;

async function runQuery(query) {
  query = query.trim();
  if (!query) return;
  state.history.push(query);
  state.historyPos = state.history.length;
  $("#jp-input").value = "";
  termPrint(h("span", { class: "cmd" }, `jsonpath> ${query}`));
  if (query === "help") return termPrint(HELP);
  if (query === "clear") return $("#jp-output").replaceChildren();
  if (!state.doc) return termPrint(h("span", { class: "err" }, "No valid document. Compile one first."));
  const { ok, body } = await api("/query-json", { jsonData: state.doc, path: query, limit: 200 });
  if (!ok) {
    const caret = body.position !== undefined ? `\n${" ".repeat(body.position + 2)}^` : "";
    return termPrint(h("span", { class: "err" }, `error: ${body.error}${caret ? `\n  ${query}${caret}` : ""}`));
  }
  termPrint(h("span", { class: "meta" },
    `${body.count} match${body.count === 1 ? "" : "es"}${body.truncated ? " (truncated)" : ""} · ${body.duration_ms} ms${body.cached ? " · cached tree" : ""}`));
  body.matches.slice(0, 50).forEach((match) => {
    termPrint(
      h("span", { class: "mpath", title: "Select in editor", onclick: () => selectPath(match.path) }, match.path),
      h("span", { class: "mval" }, match.value_json),
    );
  });
  if (body.matches.length > 50) termPrint(h("span", { class: "meta" }, `… ${body.matches.length - 50} more`));
}

function selectPath(path) {
  const node = state.astIndex.get(path);
  if (!node) return flash("No source position for this path (tree unavailable).");
  selectVisual(node.start, node.end);
}

// ---------------------------------------------------------------- compress

function renderCompress(error) {
  const out = $("#cmp-output");
  const stats = $("#cmp-stats");
  const result = state.compress?.[state.cmpMode];
  if (!result) {
    out.textContent = error ?? "Compile a document to compress it.";
    stats.replaceChildren();
    return;
  }
  const s = result.stats;
  const stat = (value, label, good) => h("div", { class: `stat${good ? " good" : ""}` }, h("b", {}, value), h("span", {}, label));
  stats.replaceChildren(
    stat(`${s.token_savings_pct > 0 ? "−" : "+"}${Math.abs(s.token_savings_pct)}%`, "tokens vs source", s.token_savings_pct > 0),
    stat(`${s.original_tokens_est.toLocaleString()} → ${s.output_tokens_est.toLocaleString()}`, "≈ tokens"),
    stat(`${s.original_chars.toLocaleString()} → ${s.output_chars.toLocaleString()}`, "characters"),
  );
  const other = state.compress[state.cmpMode === "compact" ? "minified" : "compact"];
  if (other && other.stats.output_tokens_est < s.output_tokens_est) {
    stats.append(stat(state.cmpMode === "compact" ? "Minified" : "Compact", "is cheaper for this document"));
  }
  out.textContent = result.output;
}

// -------------------------------------------------------------------- tabs

function activateTab(name) {
  $$('.tabs [role="tab"]').forEach((tab) => {
    const active = tab.dataset.tab === name;
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
    $(`#panel-${tab.dataset.tab}`).hidden = !active;
  });
  store("jde-tab", name);
  if (name === "trace") currentChip?.scrollIntoView({ block: "nearest" });
  if (name === "jsonpath") $("#jp-input").focus();
}

// ----------------------------------------------------------------- wiring

function init() {
  editor.addEventListener("input", () => {
    updateGutter();
    updateFooter();
    if ($("#opt-live").checked && editor.value.length <= LIVE_LIMIT) liveCompile();
  });
  editor.addEventListener("scroll", () => (gutter.scrollTop = editor.scrollTop));
  ["keyup", "click", "select"].forEach((type) => editor.addEventListener(type, updateCursor));
  editor.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      compile();
    } else if (event.key === "Tab" && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault();
      document.execCommand?.("insertText", false, "  ") || editor.setRangeText("  ", editor.selectionStart, editor.selectionEnd, "end");
    }
  });
  const liveCompile = debounce(compile, 450);

  $("#btn-compile").addEventListener("click", compile);
  $("#btn-prettify").addEventListener("click", () => rewrite("editor_formatted"));
  $("#btn-minify").addEventListener("click", () => rewrite("editor_minified"));
  $("#opt-lenient").addEventListener("change", () => {
    store("jde-lenient", $("#opt-lenient").checked ? "1" : "0");
    compile();
  });
  $("#opt-live").addEventListener("change", () => store("jde-live", $("#opt-live").checked ? "1" : "0"));
  $("#sample-select").addEventListener("change", (event) => {
    const sample = SAMPLES[event.target.value];
    event.target.value = "";
    if (sample) setDocument(sample.text, sample.name);
  });

  const fileInput = $("#file-input");
  fileInput.addEventListener("change", () => {
    if (fileInput.files?.[0]) openFile(fileInput.files[0]);
    fileInput.value = "";
  });
  const wrap = $("#editor-wrap");
  const overlay = $("#drop-overlay");
  let depth = 0;
  wrap.addEventListener("dragenter", (event) => {
    if (![...(event.dataTransfer?.types ?? [])].includes("Files")) return;
    depth++;
    overlay.hidden = false;
  });
  wrap.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (!depth) overlay.hidden = true;
  });
  wrap.addEventListener("dragover", (event) => event.preventDefault());
  wrap.addEventListener("drop", (event) => {
    event.preventDefault();
    depth = 0;
    overlay.hidden = true;
    const file = event.dataTransfer?.files?.[0];
    if (file) openFile(file);
  });

  $$('.tabs [role="tab"]').forEach((tab, i, tabs) => {
    tab.addEventListener("click", () => activateTab(tab.dataset.tab));
    tab.addEventListener("keydown", (event) => {
      const delta = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!delta) return;
      const next = tabs[(i + delta + tabs.length) % tabs.length];
      activateTab(next.dataset.tab);
      next.focus();
    });
  });

  $("#ast-expand").addEventListener("click", expandAll);
  $("#ast-collapse").addEventListener("click", () => $$("#ast-tree details").forEach((d, i) => (d.open = i === 0)));
  $("#ast-copy").addEventListener("click", () => state.parse?.ast && copy(JSON.stringify(state.parse.ast, null, 2), "AST JSON"));

  $("#trace-reset").addEventListener("click", () => { stopPlayback(); stepTo(0); });
  $("#trace-back").addEventListener("click", () => { stopPlayback(); stepTo(state.step - 1); });
  $("#trace-step").addEventListener("click", () => { stopPlayback(); stepTo(state.step + 1); });
  $("#trace-play").addEventListener("click", togglePlayback);
  $("#trace-speed").addEventListener("input", () => {
    if (state.timer) { stopPlayback(); togglePlayback(); }
  });
  $("#trace-scrub").addEventListener("input", (event) => { stopPlayback(); stepTo(Number(event.target.value)); });
  $("#panel-trace").addEventListener("keydown", (event) => {
    if (event.target.matches("input")) return;
    if (event.key === "ArrowRight") stepTo(state.step + 1);
    else if (event.key === "ArrowLeft") stepTo(state.step - 1);
    else return;
    event.preventDefault();
  });
  $("#panel-trace").tabIndex = 0;

  $("#fuzzy-heal").addEventListener("click", healNow);
  $("#fuzzy-apply").addEventListener("click", () => {
    if (state.heal?.healed) setDocument(state.heal.healed);
  });

  $("#poly-root").addEventListener("input", regenerateTypes);
  $("#poly-mode").addEventListener("change", regenerateTypes);
  $$(".subtabs [data-lang]").forEach((button) => button.addEventListener("click", () => {
    state.lang = button.dataset.lang;
    $$(".subtabs [data-lang]").forEach((b) => b.setAttribute("aria-selected", String(b === button)));
    if (state.types) $("#poly-code").textContent = state.types.types[state.lang];
  }));
  $("#poly-copy").addEventListener("click", () => state.types && copy(state.types.types[state.lang], "Code"));

  $$(".segmented [data-mode]").forEach((button) => button.addEventListener("click", () => {
    state.cmpMode = button.dataset.mode;
    $$(".segmented [data-mode]").forEach((b) => b.setAttribute("aria-checked", String(b === button)));
    renderCompress();
  }));
  $("#cmp-copy").addEventListener("click", () => {
    const result = state.compress?.[state.cmpMode];
    if (result) copy(result.output, "Output");
  });

  const input = $("#jp-input");
  $("#jp-form").addEventListener("submit", (event) => {
    event.preventDefault();
    runQuery(input.value);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowUp" && state.historyPos > 0) {
      input.value = state.history[--state.historyPos];
      event.preventDefault();
    } else if (event.key === "ArrowDown") {
      state.historyPos = Math.min(state.history.length, state.historyPos + 1);
      input.value = state.history[state.historyPos] ?? "";
      event.preventDefault();
    }
  });
  termPrint(h("span", { class: "meta" }, "Type a JSONPath query, or `help`."));

  $("#theme-toggle").addEventListener("click", () => {
    const current = document.documentElement.dataset.theme
      ?? (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    store("jde-theme", next);
  });

  if (recall("jde-lenient") === "0") $("#opt-lenient").checked = false;
  if (recall("jde-live") === "0") $("#opt-live").checked = false;
  const tab = recall("jde-tab");
  activateTab(tab && $(`#panel-${tab}`) ? tab : "ast");
  setDocument(SAMPLES.api.text, SAMPLES.api.name);
}

async function rewrite(field) {
  const text = editor.value;
  if (!text.trim()) return;
  const { ok, body } = await api("/parse-json", { jsonData: text, lenient: $("#opt-lenient").checked, visualize: false });
  if (!ok) {
    renderError(body);
    return flash("Could not reformat: the document does not parse.");
  }
  if (body.healing?.applied) flash(`Applied ${body.healing.repairs.length} repair(s) while reformatting`);
  setDocument(body[field]);
}

function openFile(file) {
  if (file.size > MAX_FILE) return flash("Files must be 10 MB or smaller");
  file.text().then((text) => setDocument(text, file.name));
}

init();
