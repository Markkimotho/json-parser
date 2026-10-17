import test from "node:test";
import assert from "node:assert/strict";
import {
  buildCallIndex,
  debounce,
  diffLines,
  firstStepForToken,
  formatBytes,
  indexAst,
  lineColumnToOffset,
  nodePreview,
  offsetMapper,
  offsetToLineColumn,
  sourceWindow,
  stackAt,
  suggestQueries,
  utf8Length,
} from "./lib.js";

const TRACE = [
  { action: "call", method: "parse()", token_index: 0 },
  { action: "call", method: "parse_object()", token_index: 0 },
  { action: "match", method: "match_token(LBRACE)", token_index: 0 },
  { action: "advance", method: "next_token()", token_index: 1 },
  { action: "call", method: "parse_array()", token_index: 1 },
  { action: "return", method: "parse_array()", token_index: 3 },
  { action: "return", method: "parse_object()", token_index: 4 },
  { action: "return", method: "parse()", token_index: 4 },
];

test("call stack reconstruction follows call/return events", () => {
  const index = buildCallIndex(TRACE);
  assert.deepEqual(stackAt(TRACE, index, 0), ["parse()"]);
  assert.deepEqual(stackAt(TRACE, index, 3), ["parse()", "parse_object()"]);
  assert.deepEqual(stackAt(TRACE, index, 4), ["parse()", "parse_object()", "parse_array()"]);
  assert.deepEqual(stackAt(TRACE, index, 5), ["parse()", "parse_object()"]);
  assert.deepEqual(stackAt(TRACE, index, 7), []);
  assert.deepEqual(stackAt(TRACE, index, 99), []);
  assert.deepEqual(stackAt(TRACE, index, -1), []);
});

test("stack survives an unbalanced trace that ends in an error", () => {
  const trace = [{ action: "call", method: "parse()" }, { action: "error", method: "parse()" }];
  const index = buildCallIndex(trace);
  assert.deepEqual(stackAt(trace, index, 1), ["parse()"]);
  assert.deepEqual(stackAt([{ action: "return" }], buildCallIndex([{ action: "return" }]), 0), []);
});

test("first step for a token", () => {
  assert.equal(firstStepForToken(TRACE, 1), 3);
  assert.equal(firstStepForToken(TRACE, 42), -1);
});

test("code point offsets map to UTF-16 offsets", () => {
  const ascii = offsetMapper('{"a": 1}');
  assert.equal(ascii(5), 5);
  const text = '["😀", "x"]';
  const map = offsetMapper(text);
  assert.equal(map(1), 1); // opening quote
  assert.equal(map(3), 4); // closing quote after a 2-unit emoji
  assert.equal(text.slice(map(6), map(9)), '"x"');
  assert.equal(map(999), text.length);
});

test("line/column conversions count code points", () => {
  const text = "ab\n😀cd\nef";
  assert.equal(lineColumnToOffset(text, 2, 2), 5);
  assert.equal(text.slice(lineColumnToOffset(text, 2, 2), lineColumnToOffset(text, 2, 3)), "c");
  assert.equal(lineColumnToOffset(text, 9, 1), text.length);
  assert.deepEqual(offsetToLineColumn(text, 5), { line: 2, column: 2 });
  assert.deepEqual(offsetToLineColumn(text, 0), { line: 1, column: 1 });
});

test("source window shows context lines around the focus", () => {
  const text = ["l1", "l2", "l3", "l4 TOKEN here", "l5", "l6", "l7"].join("\n");
  const start = text.indexOf("TOKEN");
  const win = sourceWindow(text, start, start + 5, 1);
  assert.equal(win.focus, "TOKEN");
  assert.equal(win.before, "l3\nl4 ");
  assert.equal(win.after, " here\nl5");
  assert.equal(win.firstLine, 3);
  const clamped = sourceWindow("abc", 10, 20);
  assert.equal(clamped.focus, "");
});

test("line diff marks additions and deletions", () => {
  assert.deepEqual(diffLines("a\nb\nc", "a\nB\nc"), [
    { type: "same", text: "a" },
    { type: "del", text: "b" },
    { type: "add", text: "B" },
    { type: "same", text: "c" },
  ]);
  assert.deepEqual(diffLines("x", "x\ny"), [{ type: "same", text: "x" }, { type: "add", text: "y" }]);
  assert.deepEqual(diffLines("x\ny", "y"), [{ type: "del", text: "x" }, { type: "same", text: "y" }]);
  assert.equal(diffLines("a\nb", "c", 1), null);
});

test("formatting helpers", () => {
  assert.equal(formatBytes(12), "12 B");
  assert.equal(formatBytes(2048), "2.0 KB");
  assert.equal(formatBytes(3 * 1024 * 1024), "3.00 MB");
  assert.equal(utf8Length("é"), 2);
  assert.equal(nodePreview({ kind: "object", children: [1, 2] }), "{2}");
  assert.equal(nodePreview({ kind: "array", children: [] }), "[0]");
  assert.equal(nodePreview({ kind: "number", raw: "1.50", value: 1.5 }), "1.50");
  assert.equal(nodePreview({ kind: "string", raw: `"${"x".repeat(60)}"` }).length, 48);
  assert.equal(nodePreview({ kind: "boolean", value: true }), "true");
});

const AST = {
  kind: "object", path: "$", children: [
    { kind: "property", key: "users", path: "$.users", children: [
      { kind: "key", path: "$.users", value: "users" },
      { kind: "array", path: "$.users", children: [
        { kind: "object", path: "$.users[0]", children: [
          { kind: "property", key: "name", path: "$.users[0].name", children: [
            { kind: "key", path: "$.users[0].name" },
            { kind: "string", path: "$.users[0].name", value: "Ada" },
          ] },
        ] },
      ] },
    ] },
  ],
};

test("AST index prefers property nodes so selections include keys", () => {
  const byPath = indexAst(AST);
  assert.equal(byPath.get("$").kind, "object");
  assert.equal(byPath.get("$.users").kind, "property");
  assert.equal(byPath.get("$.users[0]").kind, "object");
  assert.equal(byPath.get("$.users[0].name").kind, "property");
  assert.equal(indexAst(null).size, 0);
});

test("query suggestions come from the document", () => {
  assert.deepEqual(suggestQueries(AST), ["$", "$.users[*]", "$..name", "$.users[0].name"]);
  assert.deepEqual(suggestQueries(null), ["$"]);
  const odd = { kind: "array", path: "$", children: [{ kind: "object", path: "$[0]", children: [
    { kind: "property", key: "a b", path: "$[0]['a b']", children: [] }] }] };
  assert.ok(suggestQueries(odd).includes("$..['a b']"));
});

test("debounce runs once with the last arguments", async () => {
  const calls = [];
  const fn = debounce((x) => calls.push(x), 5);
  fn(1);
  fn(2);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(calls, [2]);
});
