"""Gate tests for engines/healing.py: comment stripping, every repair kind,
the audit trail, verification, and a generalization test that damages every
valid fixture and checks healing restores the original value."""

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engines import healing  # noqa: E402
from engines.core import JSONParseError, Lexer, parse  # noqa: E402
from engines.healing import HealResult, Repair, heal, heal_json, strip_comments  # noqa: E402

FIXTURES = Path(__file__).resolve().parent

# (damaged input, expected healed text, expected repair kinds in order)
CASES = [
    # Trailing commas
    ("[1, 2,]", "[1, 2]", ["trailing-comma"]),
    ('{"a": 1,}', '{"a": 1}', ["trailing-comma"]),
    ('{"a": [1,],}', '{"a": [1]}', ["trailing-comma", "trailing-comma"]),
    ("[1,\n]", "[1\n]", ["trailing-comma"]),
    # Missing commas
    ('{"a": 1 "b": 2}', '{"a": 1, "b": 2}', ["missing-comma"]),
    ("[1 2 3]", "[1, 2, 3]", ["missing-comma", "missing-comma"]),
    ('[{"a": 1} {"b": 2}]', '[{"a": 1}, {"b": 2}]', ["missing-comma"]),
    ('{"a": 1\n "b": 2}', '{"a": 1,\n "b": 2}', ["missing-comma"]),
    # Keys
    ("{key: 1}", '{"key": 1}', ["unquoted-key"]),
    ("{'key': 1}", '{"key": 1}', ["single-quoted-key"]),
    ("{$id: 1, _x: 2, content-type: 3}", '{"$id": 1, "_x": 2, "content-type": 3}', ["unquoted-key"] * 3),
    ("{1: 'one'}", '{"1": "one"}', ["unquoted-key", "single-quoted-string"]),
    # Strings
    ("['a', 'b']", '["a", "b"]', ["single-quoted-string"] * 2),
    ("{'q': 'say \"hi\"'}", '{"q": "say \\"hi\\""}', ["single-quoted-key", "single-quoted-string"]),
    ("['it\\'s']", '["it\'s"]', ["single-quoted-string"]),
    ('["it\\\'s"]', '["it\'s"]', ["invalid-escape"]),
    ('["C:\\dir\\x"]', '["C:\\\\dir\\\\x"]', ["invalid-escape"]),
    ('["tab\there"]', '["tab\\there"]', ["control-character"]),
    ('["a\tb\tc",]', '["a\\tb\\tc"]', ["control-character", "trailing-comma"]),
    ('["a\nb"]', '["a\\nb"]', ["control-character"]),
    ("[\u201csmart\u201d]", '["smart"]', ["smart-quotes"]),
    ("[\u2018single\u2019]", '["single"]', ["smart-quotes"]),
    ('["unterminated', '["unterminated"]', ["unterminated-string", "unclosed-container"]),
    ('{"a": "x\n, "b": 2}', '{"a": "x"\n, "b": 2}', ["unterminated-string"]),
    ("[hello]", '["hello"]', ["unquoted-string"]),
    ("[xyz", '["xyz"]', ["unquoted-string", "unclosed-container"]),
    ("[true, null,]", "[true, null]", ["trailing-comma"]),
    # Literals and numbers
    ("[True, False, None, NULL]", "[true, false, null, null]", ["literal-case"] * 4),
    ("[NaN, Infinity, -Infinity, +Infinity, undefined]", "[null, null, null, null, null]", ["non-json-literal"] * 5),
    ("[tr", "[true]", ["truncated-literal", "unclosed-container"]),
    ("[fal", "[false]", ["truncated-literal", "unclosed-container"]),
    ("[+1, .5, 5., 007, 0x1F, -0x10, 1.50, 1E5]", "[1, 0.5, 5, 7, 31, -16, 1.50, 1E5]", ["number-format"] * 6),
    # Comments, fences, BOM, prose
    ("// c\n[1]", "[1]", ["comment-removed"]),
    ("[1, /* two */ 2]", "[1,  2]", ["comment-removed"]),
    ("[1, # hash\n 2]", "[1,\n 2]", ["comment-removed"]),
    ("[1 /* unterminated", "[1]", ["unclosed-container", "comment-removed"]),
    ("```json\n[1]\n```", "[1]", ["code-fence-removed", "code-fence-removed"]),
    ("\ufeff[1]", "[1]", ["bom-removed"]),
    ("Sure! Here it is:\n{\"a\": 1}\nAnything else?", '{"a": 1}', ["surrounding-text", "surrounding-text"]),
    ("[1, 2]; ", "[1, 2] ", ["stray-character"]),
    # Missing pieces
    ('{"a" 1}', '{"a": 1}', ["missing-colon"]),
    ('{"a": , "b": 1}', '{"a": null, "b": 1}', ["missing-value"]),
    ('{"a"}', '{"a": null}', ["missing-value"]),
    ('{"a":', '{"a":null}', ["missing-value", "unclosed-container"]),
    ('{"a": [1, {"b": 2', '{"a": [1, {"b": 2}]}', ["unclosed-container"] * 3),
    # Brackets
    ('{"a": 1]', '{"a": 1}', ["mismatched-bracket"]),
    ("[1, 2}", "[1, 2]", ["mismatched-bracket"]),
    ('[{"a": 1]', '[{"a": 1}]', ["unclosed-container"]),
    ('[{"a": 1], {"b": 2}]', '[{"a": 1}, {"b": 2}]', ["mismatched-bracket"]),
    ('{"a": [1}, "b": 2}', '{"a": [1], "b": 2}', ["mismatched-bracket"]),
    ('{"a": [1}', '{"a": [1]}', ["unclosed-container"]),
    ("[1]]", "[1]", ["surrounding-text"]),
    ('{"a": 1}}', '{"a": 1}', ["surrounding-text"]),
    # Commas
    ("[,1]", "[1]", ["extra-comma"]),
    ("[1,,2]", "[1,2]", ["extra-comma"]),
    ("[,]", "[]", ["extra-comma"]),
    ('{,"a": 1,,"b": 2}', '{"a": 1,"b": 2}', ["extra-comma", "extra-comma"]),
    # Stray tokens
    ('{"a": 1, : "b": 2}', '{"a": 1,  "b": 2}', ["stray-token"]),
    ("[1, :2]", "[1, 2]", ["stray-token"]),
    ('{"a": 1, [2]}', '{"a": 1}', ["trailing-comma", "stray-token"]),
    ("[1, :]", "[1 ]", ["trailing-comma", "stray-token"]),
    ("] [1]", "[1]", ["surrounding-text"]),
    # Duplicates
    ('{"a": 1, "a": 2}', '{"a": 2}', ["duplicate-key"]),
    ('{\n  "a": 1,\n  "b": 2,\n  "a": 3\n}', '{\n  "b": 2,\n  "a": 3\n}', ["duplicate-key"]),
    ("{a: 1, 'a': 2, \"a\": 3}", '{"a": 3}', ["unquoted-key", "single-quoted-key", "duplicate-key", "duplicate-key"]),
    # Document shape
    ('{"a": 1}\n{"b": 2}\n', '[{"a": 1},\n{"b": 2}]\n', ["multiple-roots"]),
    ('{"a": 1}, {"b": 2}', '[{"a": 1}, {"b": 2}]', ["multiple-roots"]),
    ("1 [2]", "[1, [2]]", ["multiple-roots"]),
    (": 1", "1", ["stray-token"]),
    ("a: 1, b: [2]", '{"a": 1, "b": [2]}', ["unclosed-container", "unquoted-key", "unquoted-key", "unclosed-container"]),
    ("Output:\n{\"a\": 1}", '{"a": 1}', ["surrounding-text"]),
    ("result: [1, 2]", "[1, 2]", ["surrounding-text"]),
    ('Output: {"a": 1', '{"a": 1}', ["surrounding-text", "unclosed-container"]),
    ("data: {\"a\": 1}, more: 2", '{"data": {"a": 1}, "more": 2}', ["unclosed-container", "unquoted-key", "unquoted-key", "unclosed-container"]),
]


class StripCommentsTests(unittest.TestCase):
    def test_removes_all_comment_styles(self):
        self.assertEqual(strip_comments('{"a": 1 // tail\n}'), '{"a": 1 \n}')
        self.assertEqual(strip_comments('# head\n[1]'), "\n[1]")
        self.assertEqual(strip_comments("[1, /* x */ 2]"), "[1,  2]")

    def test_keeps_line_breaks_of_block_comments(self):
        self.assertEqual(strip_comments("[1, /* a\nb\nc */ 2]"), "[1, \n\n 2]")

    def test_leaves_comment_markers_inside_strings(self):
        text = '{"url": "http://x.io/*y*/", \'k\': \'// not a comment\', "h": "#tag"}'
        self.assertEqual(strip_comments(text), text)

    def test_escaped_quotes_and_unterminated_cases(self):
        self.assertEqual(strip_comments('["a\\"//", 1] // c'), '["a\\"//", 1] ')
        self.assertEqual(strip_comments("[1] /* never closed"), "[1] ")
        self.assertEqual(strip_comments('["open // string'), '["open // string')
        self.assertEqual(strip_comments("// only"), "")


class HealCasesTests(unittest.TestCase):
    def test_case_table(self):
        for damaged, expected, kinds in CASES:
            with self.subTest(damaged=damaged):
                result = heal(damaged)
                self.assertTrue(result.valid, result.error)
                self.assertFalse(result.strict)
                self.assertEqual(result.healed, expected)
                self.assertEqual([r.kind for r in result.repairs], kinds)
                self.assertEqual(result.value, parse(expected))

    def test_every_repair_kind_is_exercised(self):
        kinds = {
            getattr(healing, name) for name in dir(healing)
            if name.isupper() and not name.startswith("_") and isinstance(getattr(healing, name), str)
        }
        seen = {kind for _, _, case_kinds in CASES for kind in case_kinds}
        self.assertEqual(kinds - seen, set())

    def test_valid_json_passes_through_untouched(self):
        text = '{\n  "already": ["valid", 1]\n}'
        result = heal(text)
        self.assertEqual((result.healed, result.repairs, result.valid, result.strict), (text, [], True, True))
        self.assertEqual(heal_json(text), (text, []))

    def test_heal_json_returns_the_documented_tuple(self):
        healed, repairs = heal_json("{name: 'Ada', tags: ['x',],}")
        self.assertEqual(healed, '{"name": "Ada", "tags": ["x"]}')
        self.assertTrue(all(isinstance(r, Repair) for r in repairs))
        self.assertEqual([r.kind for r in repairs],
                         ["unquoted-key", "single-quoted-string", "unquoted-key", "single-quoted-string",
                          "trailing-comma", "trailing-comma"])

    def test_repairs_carry_positions_and_before_after(self):
        result = heal('{\n  "a": 1\n  "b": \'x\'\n}')
        comma, quote = result.repairs
        self.assertEqual((comma.kind, comma.line, comma.column, comma.before, comma.after),
                         ("missing-comma", 2, 9, "", ","))
        self.assertEqual((quote.kind, quote.line, quote.column, quote.before, quote.after),
                         ("single-quoted-string", 3, 8, "'x'", '"x"'))
        self.assertEqual(set(quote.to_dict()), {"kind", "message", "line", "column", "index", "before", "after"})
        self.assertEqual([r.index for r in result.repairs], sorted(r.index for r in result.repairs))

    def test_long_snippets_are_clipped(self):
        result = heal("['" + "x" * 100 + "']")
        self.assertEqual(len(result.repairs[0].before), 40)
        self.assertTrue(result.repairs[0].before.endswith("…"))

    def test_edge_whitespace_matches_the_original(self):
        self.assertEqual(heal("  [1,]  \n").healed, "  [1]  \n")
        self.assertEqual(heal("// x\n[1,]").healed, "[1]")

    def test_result_to_dict(self):
        body = heal("[1,]").to_dict()
        self.assertEqual(body["healed"], "[1]")
        self.assertTrue(body["changed"])
        self.assertTrue(body["valid"])
        self.assertIsNone(body["error"])


class UnhealableTests(unittest.TestCase):
    def test_empty_and_whitespace_input(self):
        for text in ("", "   ", "// only a comment"):
            with self.subTest(text=text):
                result = heal(text)
                self.assertFalse(result.valid)
                self.assertIn("No JSON value found", result.error["error"])
                self.assertEqual(result.healed, text)

    def test_values_json_cannot_hold_are_reported_not_hidden(self):
        result = heal("{'x': 1e999}")
        self.assertFalse(result.valid)
        self.assertEqual(result.error["error"], "Number out of range: 1e999")
        self.assertEqual(result.healed, '{"x": 1e999}')
        self.assertEqual([r.kind for r in result.repairs], ["single-quoted-key"])

    def test_depth_limit(self):
        result = heal("[" * 50 + "1", max_depth=10)
        self.assertFalse(result.valid)
        self.assertIn("Maximum nesting depth (10) exceeded", result.error["error"])

    def test_deep_damage_within_the_limit_heals(self):
        result = heal("[" * 900 + "1")
        self.assertTrue(result.valid)
        self.assertEqual(len(result.repairs), 900)


def damage(text):
    """Rewrite valid JSON with the classic hand-written mistakes, token by
    token: unquoted identifier keys, single-quoted strings, trailing commas,
    Python literals and comments."""
    lexer = Lexer(text, capture_tokens=True)
    lexer.tokenize()
    tokens = lexer.tokens
    out = ["// damaged copy\n"]
    for i, token in enumerate(tokens):
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        prev = tokens[i - 1] if i else None
        kind, raw = token["type"], token["raw"]
        if kind == "EOF":
            break
        if kind in ("RBRACE", "RBRACKET") and prev and prev["type"] not in ("LBRACE", "LBRACKET"):
            out.append(",")
        if kind == "STRING" and nxt and nxt["type"] == "COLON" and re.fullmatch(r"[A-Za-z_$][\w$]*", token["value"]):
            out.append(token["value"])
        elif kind == "STRING" and "'" not in token["value"] and "\\" not in raw:
            out.append("'" + raw[1:-1].replace("'", "\\'") + "'")
        elif kind in ("TRUE", "FALSE", "NULL"):
            out.append({"TRUE": "True", "FALSE": "False", "NULL": "None"}[kind])
        else:
            out.append(raw)
        out.append(" /* note */ " if kind == "COMMA" else " ")
    return "".join(out)


class GeneralizationTests(unittest.TestCase):
    def test_damaged_fixtures_heal_back_to_their_original_values(self):
        count = 0
        for path in sorted(FIXTURES.rglob("*.json")):
            text = path.read_text()
            try:
                original = parse(text)
            except JSONParseError:
                continue
            with self.subTest(fixture=path.name):
                damaged = damage(text)
                result = heal(damaged)
                self.assertTrue(result.valid, (damaged[:200], result.error))
                self.assertEqual(json.dumps(result.value, sort_keys=True), json.dumps(original, sort_keys=True))
                count += 1
        self.assertGreater(count, 20)

    def test_heal_result_type(self):
        self.assertIsInstance(heal("[1,]"), HealResult)


if __name__ == "__main__":
    unittest.main()
