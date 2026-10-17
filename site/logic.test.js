import test from "node:test";
import assert from "node:assert/strict";
import { parseAndFormat } from "./logic.js";
test("formats and minifies valid JSON", () => {
  assert.equal(parseAndFormat('{"a":1}', 2), '{\n  "a": 1\n}');
  assert.equal(parseAndFormat('{"a":1}', 0), '{"a":1}');
});
test("rejects malformed and empty JSON", () => {
  assert.throws(() => parseAndFormat('{bad}'));
  assert.throws(() => parseAndFormat(' '));
});
