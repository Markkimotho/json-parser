"""Gate tests for engines/core.py: token capture, AST, execution trace,
duplicate recording, observers, error positions and the strictness fixes."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engines.core import (  # noqa: E402
    DuplicateKey,
    JSONParseError,
    Lexer,
    Parser,
    ScalarEvent,
    TokenType,
    ensure_recursion_headroom,
    parse,
)
from engines.query import query_json  # noqa: E402

FIXTURES = Path(__file__).resolve().parent
SOURCE = '{\n  "name": "Ada",\n  "tags": ["x", 2, true, null],\n  "nested": {"pi": 3.14}\n}'


def valid_fixtures():
    for path in sorted(FIXTURES.rglob("*.json")):
        text = path.read_text()
        try:
            yield path, text, parse(text)
        except JSONParseError:
            continue


def walk(node):
    yield node
    for child in node.get("children", []):
        yield from walk(child)


class TokenCaptureTests(unittest.TestCase):
    def test_tokens_record_type_value_raw_and_positions(self):
        lexer = Lexer(SOURCE, capture_tokens=True)
        Parser(lexer).parse()
        tokens = lexer.tokens
        self.assertEqual(tokens[0], {"index": 0, "type": "LBRACE", "value": "{", "raw": "{",
                                     "start_index": 0, "end_index": 1, "line": 1, "column": 1})
        name = tokens[1]
        self.assertEqual((name["type"], name["value"], name["raw"], name["line"], name["column"]),
                         ("STRING", "name", '"name"', 2, 3))
        for token in tokens:
            self.assertEqual(SOURCE[token["start_index"]:token["end_index"]], token["raw"])
        self.assertEqual([t["index"] for t in tokens], list(range(len(tokens))))
        self.assertEqual(tokens[-1]["type"], TokenType.EOF)
        self.assertEqual((tokens[-1]["line"], tokens[-1]["column"]), (5, 2))

    def test_number_tokens_keep_raw_text_and_keywords_their_values(self):
        lexer = Lexer('[-1.5e3, true, false, null]', capture_tokens=True)
        Parser(lexer).parse()
        values = [(t["type"], t["value"]) for t in lexer.tokens[1:8:2]]
        self.assertEqual(values, [("NUMBER", "-1.5e3"), ("TRUE", True), ("FALSE", False), ("NULL", None)])

    def test_capture_is_off_by_default_and_counts_still_tracked(self):
        lexer = Lexer("[1, 2]")
        Parser(lexer).parse()
        self.assertEqual(lexer.tokens, [])
        self.assertEqual(lexer.token_count, 6)

    def test_capture_limit_truncates(self):
        lexer = Lexer("[1, 2, 3, 4]", capture_tokens=True, capture_limit=3)
        Parser(lexer).parse()
        self.assertEqual(len(lexer.tokens), 3)
        self.assertTrue(lexer.tokens_truncated)

    def test_tokenize_lexes_everything(self):
        tokens = Lexer('{"a": [1]}').tokenize()
        self.assertEqual([t["type"] for t in tokens],
                         ["LBRACE", "STRING", "COLON", "LBRACKET", "NUMBER", "RBRACKET", "RBRACE", "EOF"])

    def test_crlf_and_tabs_track_lines(self):
        lexer = Lexer('{\r\n\t"a":\r\n  1}', capture_tokens=True)
        Parser(lexer).parse()
        number = next(t for t in lexer.tokens if t["type"] == "NUMBER")
        self.assertEqual((number["line"], number["column"]), (3, 3))


class ASTTests(unittest.TestCase):
    def parse_ast(self, text, **options):
        parser = Parser(Lexer(text), capture_ast=True, **options)
        value = parser.parse()
        return value, parser

    def test_structure_kinds_paths_and_positions(self):
        _, parser = self.parse_ast(SOURCE)
        root = parser.ast
        self.assertEqual((root["kind"], root["path"], root["parent"], root["depth"]), ("object", "$", None, 0))
        self.assertEqual((root["start"], root["end"]), (0, len(SOURCE)))
        prop = root["children"][1]
        self.assertEqual((prop["kind"], prop["key"], prop["path"], prop["line"], prop["column"]),
                         ("property", "tags", "$.tags", 3, 3))
        key, array = prop["children"]
        self.assertEqual((key["kind"], key["value"], key["raw"]), ("key", "tags", '"tags"'))
        self.assertEqual((array["kind"], array["path"], array["depth"]), ("array", "$.tags", 1))
        self.assertEqual([(c["kind"], c["index"], c["path"]) for c in array["children"]],
                         [("string", 0, "$.tags[0]"), ("number", 1, "$.tags[1]"),
                          ("boolean", 2, "$.tags[2]"), ("null", 3, "$.tags[3]")])
        number = array["children"][1]
        self.assertEqual((number["value"], number["raw"], number["depth"]), (2, "2", 2))
        self.assertEqual(parser.node_count, len(list(walk(root))))

    def test_parent_links_ids_and_spans_are_consistent_on_every_fixture(self):
        for path, text, _ in valid_fixtures():
            with self.subTest(fixture=path.name):
                _, parser = self.parse_ast(text)
                nodes = list(walk(parser.ast))
                ids = [n["id"] for n in nodes]
                self.assertEqual(sorted(ids), list(range(len(nodes))))
                by_id = {n["id"]: n for n in nodes}
                for node in nodes:
                    for child in node.get("children", []):
                        self.assertEqual(child["parent"], node["id"])
                    if "raw" in node:
                        self.assertEqual(text[node["start"]:node["end"]], node["raw"])
                    if node["kind"] in ("object", "array"):
                        self.assertIn(text[node["start"]], "{[")
                        self.assertIn(text[node["end"] - 1], "}]")
                    if node["parent"] is not None:
                        parent = by_id[node["parent"]]
                        self.assertGreaterEqual(node["start"], parent["start"])
                        self.assertLessEqual(node["end"], parent["end"])

    def test_every_value_node_path_resolves_through_jsonpath(self):
        """Paths are shared across modules: an AST path pasted into the JSONPath
        terminal must return exactly that node's value."""
        for path, text, value in valid_fixtures():
            with self.subTest(fixture=path.name):
                _, parser = self.parse_ast(text)
                for node in walk(parser.ast):
                    if node["kind"] in ("property", "key"):
                        continue
                    matches = query_json(value, node["path"])
                    self.assertEqual(len(matches), 1, node["path"])
                    self.assertEqual(matches[0].path, node["path"])
                    if "value" in node:
                        self.assertEqual(matches[0].value, node["value"])

    def test_control_characters_in_keys_use_unicode_escapes(self):
        from engines.paths import path_child
        self.assertEqual(path_child("$", "a\x01b\n"), "$['a\\u0001b\\n']")
        self.assertEqual(query_json({"a\x01b\n": 1}, path_child("$", "a\x01b\n"))[0].value, 1)

    def test_special_keys_use_bracket_paths(self):
        _, parser = self.parse_ast('{"a b": {"it\'s": [1]}}')
        inner = parser.ast["children"][0]["children"][1]["children"][0]
        self.assertEqual(inner["path"], "$['a b']['it\\'s']")

    def test_scalar_root(self):
        _, parser = self.parse_ast('"hi"')
        self.assertEqual((parser.ast["kind"], parser.ast["path"], parser.ast["value"]), ("string", "$", "hi"))

    def test_node_budget_truncates_cleanly(self):
        value, parser = self.parse_ast("[[1, 2], [3, 4], {\"a\": 5}]", max_ast_nodes=4)
        self.assertEqual(value, [[1, 2], [3, 4], {"a": 5}])
        self.assertIsNone(parser.ast)
        self.assertTrue(parser.ast_truncated)

    def test_duplicate_properties_are_marked_in_record_mode(self):
        _, parser = self.parse_ast('{"a": 1, "a": 2}', duplicate_keys="record")
        self.assertEqual([p["duplicate"] for p in parser.ast["children"]], [False, True])


class TraceTests(unittest.TestCase):
    def trace(self, text):
        parser = Parser(Lexer(text, capture_tokens=True), trace=True)
        parser.parse()
        return parser.execution_trace

    def test_trace_starts_and_ends_with_parse_and_balances_calls(self):
        events = self.trace(SOURCE)
        self.assertEqual((events[0]["action"], events[0]["method"]), ("call", "parse()"))
        self.assertEqual((events[-1]["action"], events[-1]["method"]), ("return", "parse()"))
        depth = 0
        for event in events:
            if event["action"] == "call":
                self.assertEqual(event["call_depth"], depth)
                depth += 1
            elif event["action"] == "return":
                depth -= 1
                self.assertEqual(event["call_depth"], depth)
        self.assertEqual(depth, 0)
        self.assertEqual([e["step"] for e in events], list(range(len(events))))

    def test_pointer_advances_one_token_at_a_time_over_every_token(self):
        events = self.trace(SOURCE)
        advances = [e["token_index"] for e in events if e["action"] == "advance"]
        self.assertEqual(advances, list(range(1, len(advances) + 1)))
        token_count = Lexer(SOURCE).tokenize()[-1]["index"] + 1
        self.assertEqual(advances[-1], token_count - 1)

    def test_methods_logged(self):
        methods = {e["method"] for e in self.trace('{"a": [1, "s", true]}')}
        for expected in ("parse_object()", "parse_array()", "parse_number()", "parse_string()",
                         "parse_literal()", "match_token(COLON)", "match_token(LBRACKET)", "next_token()"):
            self.assertIn(expected, methods)

    def test_events_carry_token_positions(self):
        event = next(e for e in self.trace('[\n 7]') if e["method"] == "parse_number()")
        self.assertEqual((event["token_type"], event["token_value"], event["line"], event["column"]),
                         ("NUMBER", "7", 2, 2))

    def test_trace_limit_and_error_event(self):
        parser = Parser(Lexer("[1, 2, 3]"), trace=True, trace_limit=5)
        parser.parse()
        self.assertEqual(len(parser.execution_trace), 5)
        self.assertTrue(parser.trace_truncated)
        failing = Parser(Lexer("[1, }"), trace=True)
        with self.assertRaises(JSONParseError):
            failing.parse()
        self.assertEqual(failing.execution_trace[-1]["action"], "error")
        self.assertEqual(failing.execution_trace[-1]["detail"], "Expected JSON value, got RBRACE")

    def test_long_string_values_are_previewed(self):
        events = self.trace('["' + "x" * 200 + '"]')
        previews = [e["token_value"] for e in events if e["token_type"] == "STRING"]
        self.assertTrue(all(len(v) <= 60 for v in previews))


class DuplicateAndObserverTests(unittest.TestCase):
    def test_error_mode_rejects_duplicates_at_the_key(self):
        with self.assertRaises(JSONParseError) as ctx:
            parse('{"a": 1,\n "a": 2}')
        self.assertEqual((ctx.exception.message, ctx.exception.line, ctx.exception.column),
                         ("Duplicate key 'a' in object", 2, 2))

    def test_record_mode_keeps_last_value_and_first_position(self):
        parser = Parser(Lexer('{"a": 1, "b": {"c": 1, "c": 2}, "a": 3}'), duplicate_keys="record")
        self.assertEqual(parser.parse(), {"a": 3, "b": {"c": 2}})
        self.assertEqual([d.to_dict() for d in parser.duplicates], [
            DuplicateKey("c", "$.b.c", 1, 24, 23, 1, 16).to_dict(),
            DuplicateKey("a", "$.a", 1, 33, 32, 1, 2).to_dict(),
        ])

    def test_observer_sees_keys_and_scalars_with_raw_text_and_paths(self):
        events: list[ScalarEvent] = []
        Parser(Lexer('{"n": [1.50, "s"], "t": true, "z": null}'), observer=events.append).parse()
        self.assertEqual([(e.kind, e.raw, e.path) for e in events], [
            ("key", '"n"', "$.n"), ("number", "1.50", "$.n[0]"), ("string", '"s"', "$.n[1]"),
            ("key", '"t"', "$.t"), ("boolean", "true", "$.t"), ("key", '"z"', "$.z"), ("null", "null", "$.z"),
        ])

    def test_max_depth_seen_and_deepest_path(self):
        parser = Parser(Lexer('{"a": [[1]], "b": [2]}'), duplicate_keys="record")
        parser.parse()
        self.assertEqual(parser.max_depth_seen, 3)
        self.assertEqual(parser.deepest_path, "$.a[0]")
        self.assertEqual(parser.deepest, (1, 8, 7))


class StrictnessTests(unittest.TestCase):
    def assert_error(self, text, message, line=None, column=None):
        with self.assertRaises(JSONParseError) as ctx:
            parse(text)
        self.assertEqual(ctx.exception.message, message)
        if line is not None:
            self.assertEqual((ctx.exception.line, ctx.exception.column), (line, column))
        return ctx.exception

    def test_non_ascii_digits_are_rejected(self):
        self.assert_error("[١]", "Unexpected character '١'")
        self.assert_error("[1²]", "Unexpected character '²'")  # superscript two is not a digit

    def test_unicode_escape_requires_four_hex_digits(self):
        self.assert_error('"\\u 041"', "Invalid unicode escape sequence")
        self.assert_error('"\\u+041"', "Invalid unicode escape sequence")
        self.assert_error('"\\u00"', "Invalid unicode escape")

    def test_surrogate_pairs_combine_and_lone_surrogates_survive(self):
        self.assert_error('"\\ud83d\\uZZZZ"', "Invalid unicode escape sequence")
        self.assertEqual(parse('"\\ud83d\\ude00"'), "\U0001F600")
        self.assertEqual(parse('"\\ud83d"'), "\ud83d")
        self.assertEqual(parse('"\\ud83d\\u0041"'), "\ud83dA")

    def test_errors_point_at_the_offending_token(self):
        err = self.assert_error('{\n  "key": invalid\n}', "Unexpected character 'i'", 2, 10)
        self.assertEqual(err.index, 11)
        self.assert_error('[1,\n 2,\n]', "Trailing comma in array", 3, 1)
        self.assert_error('{"a": 1} 2', "Unexpected token after JSON value", 1, 10)
        self.assert_error("[1e999]", "Number out of range: 1e999", 1, 2)
        self.assert_error("[9223372036854775808]", "Number out of range: 9223372036854775808", 1, 2)

    def test_lexer_errors(self):
        self.assert_error('"abc', "Unterminated string")
        self.assert_error('"a\\', "Unterminated string escape")
        self.assert_error('"a\\x"', "Invalid escape sequence: \\x")
        self.assert_error('"a\tb"', "Unescaped control character in string")
        self.assert_error("-", "Invalid number format")
        self.assert_error("1.", "Invalid number: digit expected after decimal point")
        self.assert_error("1e", "Invalid number: digit expected in exponent")
        self.assert_error("01", "Leading zeros not allowed in numbers")

    def test_long_strings_use_the_fast_path_correctly(self):
        text = '"' + "abc" * 1000 + '\\n' + "é" * 50 + '"'
        self.assertEqual(parse(text), "abc" * 1000 + "\n" + "é" * 50)
        lexer = Lexer('["' + "x" * 500 + '", 1]', capture_tokens=True)
        Parser(lexer).parse()
        self.assertEqual(lexer.tokens[3]["column"], 506)  # [ + 502-char string + ", "

    def test_documented_depth_limit_works_without_recursion_error(self):
        self.assertEqual(parse("[" * 1000 + "]" * 1000), parse("[" * 1000 + "]" * 1000))
        self.assert_error("[" * 1001 + "]" * 1001, "Maximum nesting depth (1000) exceeded")

    def test_recursion_headroom_only_grows(self):
        before = sys.getrecursionlimit()
        ensure_recursion_headroom(1)
        self.assertEqual(sys.getrecursionlimit(), before)

    def test_method_aliases_and_direct_calls(self):
        self.assertIs(Parser.value, Parser.parse_value)
        self.assertIs(Parser.object, Parser.parse_object)
        parser = Parser(Lexer('"s"'))
        with self.assertRaises(JSONParseError):
            parser.parse_number()
        with self.assertRaises(JSONParseError):
            Parser(Lexer("1")).parse_string()
        with self.assertRaises(JSONParseError):
            Parser(Lexer("1")).parse_literal()
        with self.assertRaises(JSONParseError):
            Parser(Lexer("1")).match_token(TokenType.COLON, "Expected ':'")

    def test_error_to_dict(self):
        with self.assertRaises(JSONParseError) as ctx:
            parse("[,]")
        self.assertEqual(ctx.exception.to_dict(),
                         {"error": "Expected JSON value, got COMMA", "line": 1, "column": 2, "index": 1})


if __name__ == "__main__":
    unittest.main()
