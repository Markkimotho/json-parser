"""Gate tests for engines/compressor.py: exact round trips (fixtures, edge
cases and seeded random documents), the compact grammar, decoder errors and
the token estimator."""

import json
import random
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engines.compressor import (  # noqa: E402
    CompactDecodeError,
    compress_json,
    decode_compact,
    encode_compact,
    estimate_tokens,
    minify,
)
from engines.core import JSONParseError, parse  # noqa: E402

FIXTURES = Path(__file__).resolve().parent


def same(a, b):
    """Equality that also requires identical types (so 1 != 1.0 != True)."""
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def random_value(rng, depth=0):
    roll = rng.random()
    if depth > 4 or roll < 0.45:
        return rng.choice([
            None, True, False, 0, -7, 2**60, 3.25, -0.5, 1e-9, 1e21, "", " ", "plain", "two words", "a,b", "k:v",
            "-dash", "42", "true", "null", "quote\"d", "back\\slash", "new\nline", "tab\t", "é ü 漢字", "[x]", "{y}",
            "trailing ", "#hash", "- item",
        ])
    if roll < 0.7:
        return [random_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    if roll < 0.8:
        keys = rng.sample(["id", "name", "a b", "x-y", "", "1st", "ok"], rng.randint(1, 3))
        return [{k: random_value(rng, 5) for k in keys} for _ in range(rng.randint(1, 3))]
    keys = rng.sample(["id", "name", "a b", "x-y", "", "1st", "ok", "nested", "list", "dot.key"], rng.randint(0, 4))
    return {k: random_value(rng, depth + 1) for k in keys}


class RoundTripTests(unittest.TestCase):
    EDGE_CASES = [
        {}, [], "", "x", " pad", "true", "-1", "1a", 0, -0.0, 1e16, 3.5, None, True, [[]], [{}], {"a": {}},
        {"a": []}, [[1, 2], [3]], {"k": ["", " ", "null", "1.5"]}, {"": {"": ""}},
        {"users": [{"id": 1, "name": "Ada Lovelace", "active": True}, {"id": 2, "name": "Linus", "active": False}]},
        {"mixed": [1, {"a": 1, "b": [1, 2]}, [1, [2, {"c": None}]], "s", {}, []]},
        [{"a": {"b": 1}, "c": 2}, {"x": [{"y": 1}, {"y": 2}]}],
        {"weird key": {"k,": "v:1", 'q"': "\\", "n": "line\nbreak", "e": "é"}},
        {"rows": [{"a": "", "b": "x,y"}, {"b": None, "a": "-z"}]},
        {"a": [{"b": [{"c": [1]}]}]},
        [{"first": {"deep": {"deeper": [1, {"x": 2}]}}, "second": 2}],
    ]

    def test_edge_cases(self):
        for value in self.EDGE_CASES:
            with self.subTest(value=value):
                self.assertTrue(same(decode_compact(encode_compact(value)), value), encode_compact(value))

    def test_every_valid_fixture(self):
        for path in sorted(FIXTURES.rglob("*.json")):
            try:
                value = parse(path.read_text())
            except JSONParseError:
                continue
            with self.subTest(fixture=path.name):
                self.assertTrue(same(decode_compact(encode_compact(value)), value))

    def test_seeded_random_documents(self):
        rng = random.Random(20261017)
        for i in range(400):
            value = random_value(rng)
            with self.subTest(case=i):
                encoded = encode_compact(value)
                self.assertTrue(same(decode_compact(encoded), value), f"{value!r}\n{encoded}")


class FormatTests(unittest.TestCase):
    def test_tables_inline_arrays_and_lists(self):
        value = {"users": [{"id": 1, "name": "Ada"}, {"name": "Bo", "id": 2}], "tags": ["a", "b"],
                 "items": [1, {"k": "v", "o": {"p": 1}}, [2, 3], {}], "empty": [], "obj": {}, "nested": {"x": 1}}
        self.assertEqual(encode_compact(value), "\n".join([
            "users[2]{id,name}:", " 1,Ada", " 2,Bo",
            "tags[2]:a,b",
            "items[4]:", " - 1", " - k:v", "   o:", "    p:1", " - [2]:2,3", " - {}",
            "empty[0]:",
            "obj:{}",
            "nested:", " x:1",
        ]))

    def test_root_array_and_primitive(self):
        self.assertEqual(encode_compact([1, 2]), "[2]:1,2")
        self.assertEqual(encode_compact([{"a": 1}]), "[1]{a}:\n 1")
        self.assertEqual(encode_compact("hello world"), "hello world")
        self.assertEqual(encode_compact({}), "")

    def test_quoting_rules(self):
        quoted = ["", " x", "x ", "true", "false", "null", "-x", "1x", "a,b", "a:b", 'a"b', "a\\b", "[", "]", "{", "}", "a\nb"]
        for text in quoted:
            self.assertTrue(encode_compact([text, 0]).split(":", 1)[1].startswith('"'), text)
        self.assertEqual(encode_compact(["plain words", "eé", "x-y", "#tag"]), "[4]:plain words,eé,x-y,#tag")
        self.assertEqual(encode_compact({"a b": 1, "a.b": 2, "_x": 3, "1": 4}), '"a b":1\na.b:2\n_x:3\n"1":4')

    def test_numbers(self):
        self.assertEqual(encode_compact([1, -2, 1.5, 1e21, 2**63 - 1]), "[5]:1,-2,1.5,1e+21,9223372036854775807")
        with self.assertRaises(ValueError):
            encode_compact([float("nan")])

    def test_decoder_accepts_spaced_style(self):
        self.assertEqual(decode_compact("a: 1\nb:\n c: x\nl[2]: 1,2\no: {}"),
                         {"a": 1, "b": {"c": "x"}, "l": [1, 2], "o": {}})


class DecodeErrorTests(unittest.TestCase):
    def test_malformed_inputs(self):
        cases = {
            " a:1": "the first line must not be indented",
            "a:1\nb": "expected 'key: value'",
            "x\ny": "a primitive root must be the only line",
            "a:": "field 'a' has no value",
            "a[2]:1": "declared 2 items, found 1",
            "a[1]:\n - 1\n - 2": "declared 1 items, found 2",
            "a:1\n  b:2": "unexpected line",
            "a[2]{x}:\n 1": "expected 2 table rows",
            "a[1]{x}:\n 1,2": "row has 2 values, header has 1",
            "a[1]{1}:\n 1": "table header fields must be strings",
            "a[x]:1": "malformed array header",
            "a[1]:\n -": "empty list item",
            'a:"open': "bad quoted string",
            'a:"x" y': "unexpected text after quoted string",
            "a[2]:1;2": "declared 2 items, found 1",
            'a[2]:"x"y': "expected ',' between values",
            "a:1\na:2": "duplicate key 'a'",
            "[1]:\n - ": "missing value",
            'a:1\n"bad': "expected 'key: value'",
            "a:1\n- x": "unexpected line",
            "a[1]:\n b:1": "declared 1 items, found 0",
        }
        for text, message in cases.items():
            with self.subTest(text=text), self.assertRaises(CompactDecodeError) as ctx:
                decode_compact(text)
            self.assertIn(message, str(ctx.exception))

    def test_error_reports_line(self):
        with self.assertRaises(CompactDecodeError) as ctx:
            decode_compact("a:1\nb[3]:1,2")
        self.assertEqual(ctx.exception.line, 2)


class CompressionTests(unittest.TestCase):
    def test_minify_keeps_unicode_literal(self):
        self.assertEqual(minify({"a": "é", "b": [1, 2]}), '{"a":"é","b":[1,2]}')

    def test_stats_and_modes(self):
        value = {"users": [{"id": i, "name": f"User {i}", "active": True} for i in range(20)]}
        pretty = json.dumps(value, indent=2)
        compact = compress_json(value, "compact", original=pretty).to_dict()
        minified = compress_json(value, "minified").to_dict()
        self.assertEqual(compact["mode"], "compact")
        self.assertTrue(compact["output"].startswith("users[20]{id,name,active}:"))
        self.assertGreater(compact["stats"]["token_savings_pct"], minified["stats"]["token_savings_pct"])
        self.assertGreater(minified["stats"]["token_savings_pct"], 0)
        self.assertEqual(compact["stats"]["original_chars"], len(pretty))
        self.assertEqual(set(compact["stats"]), {"original_chars", "output_chars", "original_tokens_est",
                                                 "output_tokens_est", "token_savings_pct", "char_savings_pct"})
        with self.assertRaises(ValueError):
            compress_json(value, "zip")

    def test_empty_baseline(self):
        result = compress_json({}, "compact", original="")
        self.assertEqual((result.savings, result.to_dict()["stats"]["char_savings_pct"]), (0.0, 0.0))

    def test_token_estimate_behaviour(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("hello"), 1)
        self.assertEqual(estimate_tokens(" hello world"), 2)
        self.assertEqual(estimate_tokens("12345678"), 4)
        self.assertEqual(estimate_tokens('":"'), 1)
        self.assertEqual(estimate_tokens("こんにちは"), 3)
        self.assertGreater(estimate_tokens(json.dumps({"a": list(range(50))}, indent=2)),
                           estimate_tokens(json.dumps({"a": list(range(50))})))


if __name__ == "__main__":
    unittest.main()
