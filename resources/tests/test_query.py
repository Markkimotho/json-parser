"""Gate tests for engines/query.py (JSONPath interpreter), using the
canonical Goessner bookstore plus RFC 9535 semantics."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engines.query import JSONPathError, compile_selector, json_path, query_json  # noqa: E402

STORE = {
    "store": {
        "book": [
            {"category": "reference", "author": "Nigel Rees", "title": "Sayings of the Century", "price": 8.95},
            {"category": "fiction", "author": "Evelyn Waugh", "title": "Sword of Honour", "price": 12.99},
            {"category": "fiction", "author": "Herman Melville", "title": "Moby Dick", "isbn": "0-553-21311-3",
             "price": 8.99},
            {"category": "fiction", "author": "J. R. R. Tolkien", "title": "The Lord of the Rings",
             "isbn": "0-395-19395-8", "price": 22.99},
        ],
        "bicycle": {"color": "red", "price": 399},
    },
    "users": [{"name": "Ada", "metadata": {"active": True}, "tags": ["a", "b"]},
              {"name": "Linus", "metadata": {"active": False}, "tags": []}],
    "weird key": {"a'b": 1, "line\nbreak": 2},
    "flags": [True, 1, 1.0, "1", None],
}


def paths(query, data=STORE):
    return [m.path for m in query_json(data, query)]


class SelectorTests(unittest.TestCase):
    def test_spec_example(self):
        self.assertEqual(json_path(STORE, "$.users[0].metadata.active"), [True])

    def test_root_children_and_brackets(self):
        self.assertEqual(paths("$"), ["$"])
        self.assertEqual(json_path(STORE, "$.store.bicycle.color"), ["red"])
        self.assertEqual(json_path(STORE, "$['store']['bicycle'][\"color\"]"), ["red"])
        self.assertEqual(json_path(STORE, "$['weird key']['a\\'b']"), [1])
        self.assertEqual(json_path(STORE, "$['weird key']['line\\nbreak']"), [2])
        self.assertEqual(json_path(STORE, "$['weird key']['\\u0061\\'b']"), [1])

    def test_wildcards(self):
        self.assertEqual(paths("$.store.*"), ["$.store.book", "$.store.bicycle"])
        self.assertEqual(json_path(STORE, "$.store.book[*].author"),
                         ["Nigel Rees", "Evelyn Waugh", "Herman Melville", "J. R. R. Tolkien"])
        self.assertEqual(json_path(STORE, "$.store.bicycle[*]"), ["red", 399])
        self.assertEqual(json_path(STORE, "$.flags[0].*"), [])

    def test_indexes_and_unions(self):
        self.assertEqual(json_path(STORE, "$.store.book[-1].title"), ["The Lord of the Rings"])
        self.assertEqual(json_path(STORE, "$.store.book[0,2].price"), [8.95, 8.99])
        self.assertEqual(json_path(STORE, "$.store.book[10]"), [])
        self.assertEqual(json_path(STORE, "$.store.book[-10]"), [])
        self.assertEqual(json_path(STORE, "$.store['bicycle','missing'].color"), ["red"])
        self.assertEqual(json_path(STORE, "$.store.bicycle[0]"), [])

    def test_slices(self):
        prices = lambda q: json_path(STORE, q)  # noqa: E731
        self.assertEqual(prices("$.store.book[:2].price"), [8.95, 12.99])
        self.assertEqual(prices("$.store.book[1:3].price"), [12.99, 8.99])
        self.assertEqual(prices("$.store.book[-2:].price"), [8.99, 22.99])
        self.assertEqual(prices("$.store.book[::2].price"), [8.95, 8.99])
        self.assertEqual(prices("$.store.book[::-1].price"), [22.99, 8.99, 12.99, 8.95])
        self.assertEqual(prices("$.store.book[ 0 : 1 ].price"), [8.95])
        self.assertEqual(prices("$.store.book[::0]"), [])
        self.assertEqual(prices("$.store.book[5:9]"), [])
        self.assertEqual(prices("$.store.bicycle[0:1]"), [])

    def test_descendants(self):
        self.assertEqual(len(json_path(STORE, "$..author")), 4)
        self.assertEqual(json_path(STORE, "$.store..price"), [8.95, 12.99, 8.99, 22.99, 399])
        self.assertEqual(paths("$..book[2]"), ["$.store.book[2]"])
        self.assertEqual(json_path(STORE, "$..tags[0]"), ["a"])
        self.assertEqual(paths("$..[?@.active == true]"), ["$.users[0].metadata"])
        self.assertEqual(paths("$.users..*")[:3], ["$.users[0]", "$.users[1]", "$.users[0].name"])
        self.assertEqual(json_path({"a": {"a": {"a": 1}}}, "$..a"), [{"a": {"a": 1}}, {"a": 1}, 1])

    def test_document_order_paths_use_shared_format(self):
        self.assertEqual(paths("$['weird key'].*"), ["$['weird key']['a\\'b']", "$['weird key']['line\\nbreak']"])


class FilterTests(unittest.TestCase):
    def titles(self, query):
        return json_path(STORE, query)

    def test_existence_and_negation(self):
        self.assertEqual(self.titles("$..book[?(@.isbn)].title"), ["Moby Dick", "The Lord of the Rings"])
        self.assertEqual(self.titles("$..book[?@.isbn].title"), ["Moby Dick", "The Lord of the Rings"])
        self.assertEqual(self.titles("$..book[?!@.isbn].title"), ["Sayings of the Century", "Sword of Honour"])

    def test_comparisons_and_logic(self):
        self.assertEqual(self.titles("$..book[?(@.price < 10)].title"), ["Sayings of the Century", "Moby Dick"])
        self.assertEqual(self.titles("$..book[?@.price<=8.99 && @.category=='fiction'].title"), ["Moby Dick"])
        self.assertEqual(self.titles("$..book[?@.price > 20 || @.author == \"Nigel Rees\"].title"),
                         ["Sayings of the Century", "The Lord of the Rings"])
        self.assertEqual(self.titles("$..book[?!(@.price < 10 || @.price > 20)].title"), ["Sword of Honour"])
        self.assertEqual(self.titles("$..book[?@.price >= 22.99].title"), ["The Lord of the Rings"])
        self.assertEqual(self.titles("$..book[?@.category != 'fiction'].title"), ["Sayings of the Century"])
        self.assertEqual(self.titles("$..book[?@.title > 'S'].title"),
                         ["Sayings of the Century", "Sword of Honour", "The Lord of the Rings"])

    def test_root_reference_inside_filter(self):
        self.assertEqual(self.titles("$..book[?@.price < $.store.bicycle.price].price"), [8.95, 12.99, 8.99, 22.99])
        self.assertEqual(self.titles("$..book[?@.price > $.store.bicycle.price]"), [])

    def test_rfc_equality_semantics(self):
        self.assertEqual(paths("$.flags[?@ == 1]"), ["$.flags[1]", "$.flags[2]"])  # true and "1" excluded
        self.assertEqual(paths("$.flags[?@ == true]"), ["$.flags[0]"])
        self.assertEqual(paths("$.flags[?@ == null]"), ["$.flags[4]"])
        self.assertEqual(paths("$.users[?@.missing == @.alsomissing]"), ["$.users[0]", "$.users[1]"])
        self.assertEqual(paths("$.users[?@.missing == null]"), [])
        self.assertEqual(paths("$.users[?@.missing <= @.alsomissing]"), ["$.users[0]", "$.users[1]"])
        self.assertEqual(paths("$.users[?@.name < 5]"), [])
        self.assertEqual(paths("$.users[?@.metadata == @.metadata]"), ["$.users[0]", "$.users[1]"])
        self.assertEqual(paths("$[?@ == $.users[0].metadata]", {"x": {"active": True}, "users": [{"metadata": {"active": True}}]}),
                         ["$.x"])
        self.assertEqual(paths("$[?@ == $.l]", {"a": [1, 2], "b": [1, 3], "l": [1, 2]}), ["$.a", "$.l"])
        self.assertEqual(paths("$.users[?@.tags]"), ["$.users[0]", "$.users[1]"])

    def test_functions(self):
        self.assertEqual(self.titles("$..book[?length(@.title) > 15].title"),
                         ["Sayings of the Century", "The Lord of the Rings"])
        self.assertEqual(paths("$.users[?length(@.tags) == 0]"), ["$.users[1]"])
        self.assertEqual(paths("$.users[?length(@.metadata) == 1]"), ["$.users[0]", "$.users[1]"])
        self.assertEqual(paths("$.users[?length(@.nothing) == 0]"), [])
        self.assertEqual(paths("$.store[?count(@.*) > 2]"), ["$.store.book"])
        self.assertEqual(self.titles("$..book[?match(@.author, '.*Tolkien')].price"), [22.99])
        self.assertEqual(self.titles("$..book[?match(@.author, 'Tolkien')].price"), [])
        self.assertEqual(self.titles("$..book[?search(@.title, 'Dick')].author"), ["Herman Melville"])
        self.assertEqual(self.titles("$..book[?search(@.price, '9')]"), [])
        self.assertEqual(self.titles("$..book[?value(@.isbn) == '0-553-21311-3'].title"), ["Moby Dick"])
        self.assertEqual(self.titles("$..book[?length(@.title)]"), json_path(STORE, "$..book[*]"))
        self.assertEqual(paths("$.users[?search(@.name, $.users[0].name)]"), ["$.users[0]"])

    def test_literal_tests(self):
        self.assertEqual(len(json_path(STORE, "$.users[?true]")), 2)
        self.assertEqual(json_path(STORE, "$.users[?false]"), [])
        self.assertEqual(json_path(STORE, "$.users[?null]"), [])
        self.assertEqual(len(json_path(STORE, "$.users[?(@.name == 'Ada') == true]")), 1)
        self.assertEqual(json_path({"a": [1e2]}, "$.a[?@ == 100]"), [100.0])

    def test_filters_on_objects_select_member_values(self):
        self.assertEqual(paths("$.store[?@.color]"), ["$.store.bicycle"])


class ErrorAndApiTests(unittest.TestCase):
    def assert_error(self, query, message, position=None):
        with self.assertRaises(JSONPathError) as ctx:
            query_json(STORE, query)
        self.assertEqual(ctx.exception.message, message)
        if position is not None:
            self.assertEqual(ctx.exception.position, position)

    def test_syntax_errors(self):
        self.assert_error("store", "A JSONPath query must start with '$'", 0)
        self.assert_error("@.a", "A top-level query must start with '$'", 0)
        self.assert_error("$.", "Expected a member name", 2)
        self.assert_error("$[", "Expected a selector", 2)
        self.assert_error("$[0", "Expected ']', found 'end of query'", 3)
        self.assert_error("$['a", "Unterminated string literal")
        self.assert_error("$['\\q']", "Invalid escape in string literal")
        self.assert_error("$[?@.a ==]", "Expected a filter operand", 9)
        self.assert_error("$[?(@.a]", "Expected ')', found ']'")
        self.assert_error("$..book[?foo(@)]", "Unknown function foo()")
        self.assert_error("$[?length(@, 1)]", "length() takes 1 argument(s)")
        self.assert_error("$.a b", "Unexpected 'b'")
        self.assert_error("$[?count(1) > 0]", "count() takes a query")
        self.assert_error("$[?value(1) > 0]", "value() takes a query")

    def test_regex_guards(self):
        self.assert_error("$[?match(@, '(a+)+$')]",
                          "Nested quantifiers and backreferences are not allowed in match()/search()")
        self.assert_error("$[?search(@, '(x)\\\\1')]",
                          "Nested quantifiers and backreferences are not allowed in match()/search()")
        self.assert_error("$[?match(@, '" + "a" * 201 + "')]", "Regular expressions are limited to 200 characters")
        self.assert_error("$[?match(@, '[')]", "Invalid regular expression: unterminated character set")
        data = {"p": "(a+)+", "s": ["aaa"]}
        with self.assertRaises(JSONPathError):
            query_json(data, "$.s[?match(@, $.p)]")

    def test_long_subjects_are_truncated_for_regex(self):
        data = {"s": ["x" * 20_000 + "needle"]}
        self.assertEqual(json_path(data, "$.s[?search(@, 'needle')]"), [])

    def test_limit_and_intermediate_cap(self):
        self.assertEqual(len(query_json(STORE, "$..*", limit=5)), 5)
        self.assertEqual(len(query_json(STORE, "$..*", limit=10_000)), len(query_json(STORE, "$..*")))
        from engines import query as module
        original = module.MAX_INTERMEDIATE_NODES
        module.MAX_INTERMEDIATE_NODES = 3
        try:
            with self.assertRaises(JSONPathError):
                query_json(STORE, "$..*.*")
        finally:
            module.MAX_INTERMEDIATE_NODES = original

    def test_compiled_selector_and_match_dict(self):
        select = compile_selector("$..price")
        self.assertEqual(select(STORE["store"]), [8.95, 12.99, 8.99, 22.99, 399])
        self.assertEqual(query_json(STORE, "$.store.bicycle.color")[0].to_dict(),
                         {"path": "$.store.bicycle.color", "value": "red"})

    def test_deep_documents_do_not_recurse(self):
        deep = current = {}
        for _ in range(5000):
            current["n"] = {}
            current = current["n"]
        current["leaf"] = 1
        self.assertEqual(json_path(deep, "$..leaf"), [1])


if __name__ == "__main__":
    unittest.main()
