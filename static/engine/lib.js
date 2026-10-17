// Pure helpers for the Developer Engine dashboard. No DOM access, so every
// function here is unit-tested with `node --test static/engine/lib.test.js`.

/**
 * The Python engine reports source offsets in code points; browser strings
 * index UTF-16 code units. Returns a converter from code-point offsets to
 * UTF-16 offsets for `text` (identity when the text has no astral characters).
 */
export function offsetMapper(text) {
  if (!/[\uD800-\uDFFF]/.test(text)) return (index) => index;
  const table = [];
  let unit = 0;
  for (const ch of text) {
    table.push(unit);
    unit += ch.length;
  }
  table.push(unit);
  return (index) => table[Math.max(0, Math.min(index, table.length - 1))];
}

/**
 * Index the execution trace so the call stack at any step is O(depth):
 * `top[i]` is the innermost open call after event i (-1 for none) and
 * `parent[c]` is the call that was open when call c started.
 */
export function buildCallIndex(trace) {
  const top = new Int32Array(trace.length);
  const parent = new Int32Array(trace.length).fill(-1);
  let current = -1;
  trace.forEach((event, i) => {
    if (event.action === "call") {
      parent[i] = current;
      current = i;
    } else if (event.action === "return" && current >= 0) {
      current = parent[current];
    }
    top[i] = current;
  });
  return { top, parent };
}

/** Methods on the call stack after event `step`, outermost first. */
export function stackAt(trace, index, step) {
  if (step < 0 || step >= trace.length) return [];
  const stack = [];
  for (let call = index.top[step]; call >= 0; call = index.parent[call]) {
    stack.push(trace[call].method);
  }
  return stack.reverse();
}

/** First trace step whose token pointer is at `tokenIndex` (or -1). */
export function firstStepForToken(trace, tokenIndex) {
  return trace.findIndex((event) => event.token_index === tokenIndex);
}

/**
 * Lines of `text` around the UTF-16 range [start, end): returns the text
 * before, inside and after the range, plus the first line number shown.
 */
export function sourceWindow(text, start, end, context = 5) {
  const safeStart = Math.max(0, Math.min(start, text.length));
  const safeEnd = Math.max(safeStart, Math.min(end, text.length));
  let from = safeStart;
  for (let lines = 0; from > 0; from--) {
    if (text[from - 1] === "\n" && ++lines > context) break;
  }
  let to = safeEnd;
  for (let lines = 0; to < text.length; to++) {
    if (text[to] === "\n" && ++lines > context) break;
  }
  const firstLine = text.slice(0, from).split("\n").length;
  return {
    before: text.slice(from, safeStart),
    focus: text.slice(safeStart, safeEnd),
    after: text.slice(safeEnd, to),
    firstLine,
  };
}

/** UTF-16 offset of 1-based (line, column) in `text`; columns count code points. */
export function lineColumnToOffset(text, line, column) {
  let offset = 0;
  for (let current = 1; current < line; current++) {
    const next = text.indexOf("\n", offset);
    if (next < 0) return text.length;
    offset = next + 1;
  }
  let remaining = column - 1;
  while (remaining > 0 && offset < text.length && text[offset] !== "\n") {
    offset += text.codePointAt(offset) > 0xffff ? 2 : 1;
    remaining--;
  }
  return offset;
}

/** 1-based line and column (in code points) of a UTF-16 offset. */
export function offsetToLineColumn(text, offset) {
  const before = text.slice(0, offset);
  const line = before.split("\n").length;
  const lineStart = before.lastIndexOf("\n") + 1;
  return { line, column: [...text.slice(lineStart, offset)].length + 1 };
}

/**
 * Line diff (LCS). Returns [{type: "same"|"add"|"del", text}]. Inputs larger
 * than `limit` lines return null so the UI can skip the diff.
 */
export function diffLines(a, b, limit = 2000) {
  const left = a.split("\n");
  const right = b.split("\n");
  if (left.length > limit || right.length > limit) return null;
  const n = left.length;
  const m = right.length;
  const lcs = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = left[i] === right[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const out = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (left[i] === right[j]) {
      out.push({ type: "same", text: left[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ type: "del", text: left[i++] });
    } else {
      out.push({ type: "add", text: right[j++] });
    }
  }
  while (i < n) out.push({ type: "del", text: left[i++] });
  while (j < m) out.push({ type: "add", text: right[j++] });
  return out;
}

/** Human-readable byte size. */
export function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

/** UTF-8 byte length of a string. */
export function utf8Length(text) {
  return new TextEncoder().encode(text).length;
}

/** Short single-line preview of an AST node's value. */
export function nodePreview(node, limit = 48) {
  if (node.kind === "object") return `{${node.children.length}}`;
  if (node.kind === "array") return `[${node.children.length}]`;
  const raw = node.raw ?? JSON.stringify(node.value);
  return raw.length > limit ? `${raw.slice(0, limit - 1)}…` : raw;
}

/**
 * Flatten an AST into a Map from JSONPath to the node that owns the value
 * (property nodes win over their value child, so selections include keys).
 */
export function indexAst(ast) {
  const byPath = new Map();
  const stack = ast ? [ast] : [];
  while (stack.length) {
    const node = stack.pop();
    if (node.kind !== "key" && !byPath.has(node.path)) byPath.set(node.path, node);
    else if (node.kind === "property") byPath.set(node.path, node);
    for (let i = (node.children?.length ?? 0) - 1; i >= 0; i--) stack.push(node.children[i]);
  }
  return byPath;
}

/** Suggest JSONPath examples from an AST: arrays, a leaf and a descendant search. */
export function suggestQueries(ast, max = 5) {
  const out = new Set(["$"]);
  const stack = ast ? [ast] : [];
  let leaf = null;
  while (stack.length && out.size < max) {
    const node = stack.shift();
    if (node.kind === "array" && node.children.length) {
      out.add(`${node.path}[*]`);
      const first = node.children[0];
      if (first.kind === "object" && first.children.length) {
        const key = first.children[0].key;
        out.add(`$..${/^[A-Za-z_$][\w$]*$/.test(key) ? key : `['${key.replace(/'/g, "\\'")}']`}`);
      }
    }
    if (!leaf && node.kind !== "object" && node.kind !== "array" && node.kind !== "property" && node.kind !== "key") {
      leaf = node.path;
    }
    stack.push(...(node.children ?? []));
  }
  if (leaf && out.size < max) out.add(leaf);
  return [...out].slice(0, max);
}

/** Debounce `fn` by `wait` milliseconds. */
export function debounce(fn, wait) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}
