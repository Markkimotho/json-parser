"""Gate tests for engines/types.py (Polyglot type generator).

Structural assertions run everywhere. Two stronger checks run when the
toolchain is present locally: generated Pydantic models must validate the
very document they were generated from, and generated Go must already be
gofmt-formatted. (tsc and go vet run in the eval lane: evals/run_evals.py.)
"""

import importlib.util
import shutil
import subprocess
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engines.core import JSONParseError, parse  # noqa: E402
from engines.types import (  # noqa: E402
    ANY,
    NULL,
    ArrayOf,
    Enum,
    MapOf,
    Named,
    Prim,
    SchemaError,
    UnionOf,
    build_model,
    generate_types,
    go_name,
    looks_like_schema,
    pascal_case,
    singular,
    split_null,
    union,
)

FIXTURES = Path(__file__).resolve().parent
HAS_PYDANTIC = importlib.util.find_spec("pydantic") is not None
GOFMT = shutil.which("gofmt")

USERS = {
    "users": [
        {"id": 1, "name": "Ada", "email": None, "score": 1, "tags": ["a"], "address": {"city": "London"}},
        {"id": 2, "name": "Linus", "email": "l@x.io", "score": 2.5, "tags": [], "admin": True},
    ]
}

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Order",
    "description": "An order",
    "type": "object",
    "required": ["id", "status", "items"],
    "properties": {
        "id": {"type": "string", "description": "Order id"},
        "status": {"enum": ["open", "closed", None]},
        "kind": {"const": "order"},
        "items": {"type": "array", "items": {"$ref": "#/$defs/LineItem"}},
        "customer": {"$ref": "#/definitions/Customer"},
        "notes": {"type": ["string", "null"]},
        "legacy": {"type": "string", "nullable": True},
        "attrs": {"type": "object", "additionalProperties": {"type": "integer"}},
        "anything": {},
        "pair": {"type": "array", "items": [{"type": "string"}, {"type": "number"}]},
        "prefixed": {"type": "array", "prefixItems": [{"type": "boolean"}]},
        "choice": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
        "parent": {"$ref": "#"},
        "label": {"$ref": "#/$defs/Label"},
    },
    "$defs": {
        "LineItem": {
            "type": "object",
            "required": ["sku"],
            "properties": {
                "sku": {"type": "string"},
                "qty": {"type": "integer"},
                "children": {"type": "array", "items": {"$ref": "#/$defs/LineItem"}},
            },
        },
        "Label": {"type": "string", "enum": ["a", "b"]},
    },
    "definitions": {
        "Customer": {
            "allOf": [
                {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
                {"properties": {"vip": {"type": "boolean"}}, "description": "VIP flag"},
            ]
        }
    },
}


def valid_fixtures():
    for path in sorted(FIXTURES.rglob("*.json")):
        try:
            yield path, parse(path.read_text())
        except JSONParseError:
            continue


class IRTests(unittest.TestCase):
    def test_union_flattens_dedupes_and_widens(self):
        self.assertEqual(union(Prim("integer"), Prim("number")), Prim("number"))
        self.assertEqual(union(NULL, Prim("string"), Prim("string")), UnionOf((Prim("string"), NULL)))
        self.assertEqual(union(UnionOf((Prim("string"), NULL)), Prim("boolean")),
                         UnionOf((Prim("string"), Prim("boolean"), NULL)))
        self.assertEqual(union(Prim("string"), ANY), ANY)
        self.assertEqual(union(), ANY)

    def test_split_null(self):
        self.assertEqual(split_null(NULL), (None, True))
        self.assertEqual(split_null(UnionOf((Prim("string"), NULL))), (Prim("string"), True))
        self.assertEqual(split_null(UnionOf((Prim("string"), Prim("boolean"), NULL))),
                         (UnionOf((Prim("string"), Prim("boolean"))), True))
        self.assertEqual(split_null(Prim("string")), (Prim("string"), False))

    def test_naming_helpers(self):
        self.assertEqual(pascal_case("user_profile"), "UserProfile")
        self.assertEqual(pascal_case("userProfile"), "UserProfile")
        self.assertEqual(pascal_case("HTTPServer"), "HTTPServer")
        self.assertEqual(pascal_case("2fa"), "Item2fa")
        self.assertEqual(pascal_case("!!!"), "Item")
        self.assertEqual([singular(w) for w in ("Users", "Categories", "Addresses", "Boxes", "Status", "Data", "Class")],
                         ["User", "Category", "Address", "Box", "StatusItem", "DataItem", "ClassItem"])
        self.assertEqual([go_name(k) for k in ("id", "user_id", "apiURL", "http-status", "9lives", "", "名前")],
                         ["ID", "UserID", "APIURL", "HTTPStatus", "Field9lives", "Field", "Field"])


class SampleInferenceTests(unittest.TestCase):
    def test_array_objects_merge_into_one_type(self):
        model = build_model(USERS)
        user = model.objects["User"]
        fields = {f.key: (f.type, f.optional) for f in user.fields}
        self.assertEqual(fields["id"], (Prim("integer"), False))
        self.assertEqual(fields["email"], (UnionOf((Prim("string"), NULL)), False))
        self.assertEqual(fields["score"], (Prim("number"), False))
        self.assertEqual(fields["tags"], (ArrayOf(Prim("string")), False))
        self.assertEqual(fields["address"], (Named("Address"), True))
        self.assertEqual(fields["admin"], (Prim("boolean"), True))
        self.assertEqual(model.root, Named("RootObject"))
        self.assertEqual(model.source, "sample")

    def test_mixed_and_empty_values(self):
        model = build_model({"mixed": [1, "a", None], "empty_list": [], "empty_obj": {}, "nothing": None,
                             "nested": [[1, 2], [3.5]]})
        fields = {f.key: f.type for f in model.objects["RootObject"].fields}
        self.assertEqual(fields["mixed"], ArrayOf(UnionOf((Prim("string"), Prim("integer"), NULL))))
        self.assertEqual(fields["empty_list"], ArrayOf(ANY))
        self.assertEqual(fields["empty_obj"], MapOf(ANY))
        self.assertEqual(fields["nothing"], NULL)
        self.assertEqual(fields["nested"], ArrayOf(ArrayOf(Prim("number"))))

    def test_identical_shapes_share_a_type_and_different_ones_get_suffixes(self):
        same = build_model({"home": {"address": {"city": "a"}}, "work": {"address": {"city": "b"}}})
        self.assertEqual([o.name for o in same.ordered()], ["RootObject", "Home", "Address", "Work"])
        different = build_model({"a": {"address": {"city": "x"}}, "b": {"address": {"zip": 1}}})
        self.assertEqual(sorted(different.objects), ["A", "Address", "Address2", "B", "RootObject"])

    def test_diamond_references_are_not_cycles(self):
        model = build_model({"b": {"d": {"x": 1}}, "c": {"d": {"x": 1}}})
        self.assertEqual(sorted(model.objects), ["B", "C", "D", "RootObject"])
        self.assertEqual(model.cyclic(), set())

    def test_root_shapes(self):
        self.assertEqual(build_model([{"a": 1}]).root, ArrayOf(Named("RootObjectItem")))
        self.assertEqual(build_model([]).root, ArrayOf(ANY))
        self.assertEqual(build_model("x").root, Prim("string"))
        self.assertEqual(build_model({}).root, MapOf(ANY))
        self.assertEqual(build_model([1, "a"]).root, ArrayOf(UnionOf((Prim("string"), Prim("integer")))))

    def test_root_name_is_sanitized(self):
        self.assertEqual(generate_types({"a": 1}, root_name="my payload")["root"], "MyPayload")
        self.assertEqual(generate_types({"a": 1}, root_name="List")["root"], "ListModel")
        self.assertEqual(generate_types({"a": 1}, root_name="")["root"], "RootObject")
        self.assertEqual(build_model({"error": {"code": 1}}).ordered()[1].name, "ErrorModel")

    def test_unsupported_python_values_are_rejected(self):
        with self.assertRaises(TypeError):
            build_model({"a": {1, 2}})


class SchemaInferenceTests(unittest.TestCase):
    def test_schema_constructs(self):
        model = build_model(SCHEMA)
        self.assertEqual(model.source, "schema")
        root = model.objects["RootObject"]
        self.assertEqual(root.description, "An order")
        fields = {f.key: f for f in root.fields}
        self.assertEqual(fields["id"].description, "Order id")
        self.assertFalse(fields["id"].optional)
        self.assertTrue(fields["notes"].optional)
        self.assertEqual(fields["status"].type, UnionOf((Enum(("open", "closed")), NULL)))
        self.assertEqual(fields["kind"].type, Enum(("order",)))
        self.assertEqual(fields["items"].type, ArrayOf(Named("LineItem")))
        self.assertEqual(fields["customer"].type, Named("Customer"))
        self.assertEqual(fields["notes"].type, UnionOf((Prim("string"), NULL)))
        self.assertEqual(fields["legacy"].type, UnionOf((Prim("string"), NULL)))
        self.assertEqual(fields["attrs"].type, MapOf(Prim("integer")))
        self.assertEqual(fields["anything"].type, ANY)
        self.assertEqual(fields["pair"].type, ArrayOf(UnionOf((Prim("string"), Prim("number")))))
        self.assertEqual(fields["prefixed"].type, ArrayOf(Prim("boolean")))
        self.assertEqual(fields["choice"].type, UnionOf((Prim("string"), Prim("integer"))))
        self.assertEqual(fields["parent"].type, Named("RootObject"))
        self.assertEqual(fields["label"].type, Enum(("a", "b")))
        line_item = {f.key: f for f in model.objects["LineItem"].fields}
        self.assertEqual(line_item["children"].type, ArrayOf(Named("LineItem")))
        customer = model.objects["Customer"]
        self.assertEqual([(f.key, f.optional) for f in customer.fields], [("name", False), ("vip", True)])
        self.assertEqual(customer.description, "VIP flag")
        self.assertEqual(model.cyclic(), {"RootObject", "LineItem"})

    def test_non_object_and_degenerate_schemas(self):
        self.assertEqual(build_model({"type": "array", "items": {"type": "string"}}).root, ArrayOf(Prim("string")))
        self.assertEqual(build_model({"type": "object", "properties": {}}, mode="schema").root, MapOf(ANY))
        self.assertEqual(build_model({"type": "integer"}, mode="schema").root, Prim("integer"))
        self.assertEqual(build_model({"description": "free"}, mode="schema").root, ANY)
        self.assertEqual(build_model({"type": "array", "items": []}, mode="schema").root, ArrayOf(ANY))
        self.assertEqual(build_model({"type": "object", "properties": {"x": True, "y": False}}).objects["RootObject"]
                         .fields[1].type, ANY)
        self.assertEqual(build_model({"enum": [None]}, mode="schema").root, NULL)

    def test_less_common_schema_shapes(self):
        model = build_model({"type": "object", "properties": {
            "nested": {"properties": {"x": {"type": "string"}}},
            "list": {"items": {"type": "integer"}},
            "when": {"type": "date"},
            "merged": {"title": "Merged", "description": "own docs",
                       "allOf": [{"$ref": "#/$defs/Base"}, {"properties": {"extra": {"type": "boolean"}}}]},
        }, "$defs": {"Base": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}}})
        fields = {f.key: f.type for f in model.objects["RootObject"].fields}
        self.assertEqual(fields["nested"], Named("Nested"))
        self.assertEqual(fields["list"], ArrayOf(Prim("integer")))
        self.assertEqual(fields["when"], ANY)
        self.assertEqual(fields["merged"], Named("Merged"))
        merged = model.objects["Merged"]
        self.assertEqual(merged.description, "own docs")
        self.assertEqual([(f.key, f.optional) for f in merged.fields], [("id", False), ("extra", True)])

    def test_schema_errors(self):
        for schema in (
            {"type": "object", "properties": {"a": {"$ref": "https://example.com/x.json"}}},
            {"type": "object", "properties": {"a": {"$ref": "#/$defs/Missing"}}},
            {"allOf": [{"$ref": "#/$defs/Missing"}]},
            {"type": "object", "properties": {"a": 5}},
        ):
            with self.subTest(schema=schema), self.assertRaises(SchemaError):
                build_model(schema, mode="schema")
        with self.assertRaises(SchemaError):
            build_model([1], mode="schema")

    def test_self_referencing_non_object_definition_terminates(self):
        schema = {"type": "object", "properties": {"t": {"$ref": "#/$defs/Tree"}},
                  "$defs": {"Tree": {"type": "array", "items": {"$ref": "#/$defs/Tree"}}}}
        self.assertEqual(build_model(schema).objects["RootObject"].fields[0].type, ArrayOf(ANY))

    def test_auto_detection(self):
        self.assertTrue(looks_like_schema({"$schema": "x"}))
        self.assertTrue(looks_like_schema({"type": "object", "properties": {"a": {"type": "string"}}}))
        self.assertTrue(looks_like_schema({"type": "array", "items": {"type": "string"}}))
        self.assertTrue(looks_like_schema({"type": ["string", "null"], "enum": ["a", None]}))
        self.assertFalse(looks_like_schema({"type": "object", "properties": {"a": "plain value"}}))
        self.assertFalse(looks_like_schema({"type": "car", "properties": {}}))
        self.assertFalse(looks_like_schema({"type": "string"}))
        self.assertFalse(looks_like_schema([1]))
        self.assertEqual(generate_types({"type": "object", "properties": {"a": {"type": "string"}}},
                                        mode="sample")["mode"], "sample")


class RenderTests(unittest.TestCase):
    def test_typescript(self):
        ts = generate_types(USERS)["typescript"]
        self.assertIn("export interface RootObject {\n  users: User[];\n}", ts)
        self.assertIn("  email: string | null;\n", ts)
        self.assertIn("  address?: Address;\n", ts)
        ts = generate_types({"content-type": "x", "list": [1, "a"], "map": {}, "e": None})["typescript"]
        self.assertIn('  "content-type": string;', ts)
        self.assertIn("  list: (string | number)[];", ts)
        self.assertIn("  map: Record<string, unknown>;", ts)
        self.assertIn("  e: null;", ts)
        schema_ts = generate_types(SCHEMA)["typescript"]
        self.assertIn('/** An order */\nexport interface RootObject {\n  /** Order id */\n  id: string;', schema_ts)
        self.assertIn('  status: "open" | "closed" | null;', schema_ts)
        self.assertIn("  label?: \"a\" | \"b\";", schema_ts)
        self.assertIn("export type RootObject = RootObjectItem[];", generate_types([{"a": 1}])["typescript"])
        multi = generate_types({"type": "object", "description": "line one\nline */ two",
                                "properties": {"x": {"type": "array", "items": {"enum": [1, 2]}}}})["typescript"]
        self.assertIn("/**\n * line one\n * line * / two\n */", multi)
        self.assertIn("x?: (1 | 2)[];", multi)

    def test_pydantic(self):
        py = generate_types(USERS)["pydantic"]
        self.assertTrue(py.startswith("from typing import List, Optional\n\nfrom pydantic import BaseModel\n"))
        self.assertIn("    email: Optional[str]\n", py)
        self.assertIn("    address: Optional[Address] = None\n", py)
        self.assertLess(py.index("class Address"), py.index("class User"))
        self.assertLess(py.index("class User"), py.index("class RootObject"))
        collide = generate_types({"a-b": 1, "a_b": 2, "A b": 3})["pydantic"]
        self.assertIn('    a_b: int = Field(alias="a-b")', collide)
        self.assertIn("    a_b_2: int", collide)
        self.assertIn('    a_b_3: int = Field(alias="A b")', collide)
        tricky = generate_types({"class": 1, "content-type": "x", "_id": 2, "json": True, "model_name": "m",
                                 "Field": 3, "userName": "u", "9to5": 1})["pydantic"]
        for line in ('    class_: int = Field(alias="class")', '    content_type: str = Field(alias="content-type")',
                     '    id: int = Field(alias="_id")', '    json_: bool = Field(alias="json")',
                     '    model_name_: str = Field(alias="model_name")', '    field: int = Field(alias="Field")',
                     "    userName: str", '    field_9to5: int = Field(alias="9to5")',
                     "    model_config = ConfigDict(populate_by_name=True)"):
            self.assertIn(line, tricky)
        schema_py = generate_types(SCHEMA)["pydantic"]
        self.assertTrue(schema_py.startswith("from __future__ import annotations\n"))
        self.assertIn('    """An order"""', schema_py)
        self.assertIn('    id: str = Field(description="Order id")', schema_py)
        self.assertIn('    status: Optional[Literal["open", "closed"]]\n', schema_py)
        self.assertIn("    choice: Optional[Union[str, int]] = None", schema_py)
        self.assertIn("RootObject.model_rebuild()", schema_py)
        self.assertIn("class RootObject(RootModel[List[RootObjectItem]]):\n    pass",
                      generate_types([{"a": 1}])["pydantic"])
        self.assertEqual(generate_types("x")["pydantic"],
                         "from pydantic import RootModel\n\n\nclass RootObject(RootModel[str]):\n    pass\n")
        self.assertIn("class RootObject(RootModel[Dict[str, Any]]):\n    pass",
                      generate_types({"type": "object", "properties": {}}, mode="schema")["pydantic"])

    def test_go(self):
        go = generate_types(USERS)["go"]
        self.assertTrue(go.startswith("package models\n"))
        self.assertIn("\tUsers []User `json:\"users\"`\n", go)
        self.assertIn("\tID      int64    `json:\"id\"`", go)
        self.assertIn("\tEmail   *string  `json:\"email\"`", go)
        self.assertIn("\tAddress *Address `json:\"address,omitempty\"`", go)
        self.assertIn("\tTags    []string `json:\"tags\"`", go)
        odd = generate_types({"": 1, "a,b": 2, 'q"uote': 3, "back`tick": 4, "id": 5, "ID": 6, "m": {}, "x": [1, "a"]})["go"]
        self.assertIn("// key \"\" cannot be expressed in an encoding/json tag; map it by hand", odd)
        self.assertIn('`json:"-"`', odd)
        self.assertIn('`json:"q\\"uote"`', odd)
        self.assertIn('"json:\\"back`tick\\""', odd)
        self.assertIn("ID2 ", odd)
        self.assertIn("map[string]any", odd)
        self.assertIn("[]any", odd)
        schema_go = generate_types(SCHEMA)["go"]
        self.assertIn("// An order\ntype RootObject struct {\n\t// Order id\n", schema_go)
        self.assertIn("Status   *string", schema_go)
        self.assertIn("Attrs    map[string]int64", schema_go)
        self.assertIn("type RootObject []RootObjectItem", generate_types([{"a": 1}])["go"])
        enums = generate_types({"type": "object", "required": ["b", "i", "f", "m"], "properties": {
            "b": {"enum": [True, False]}, "i": {"enum": [1, 2]}, "f": {"enum": [1, 2.5]}, "m": {"enum": [1, "a"]}}})["go"]
        self.assertIn("B bool ", enums)
        self.assertIn("I int64 ", enums)
        self.assertIn("F float64 ", enums)
        self.assertIn("M any ", enums)

    def test_generate_types_payload(self):
        out = generate_types(USERS)
        self.assertEqual(out["mode"], "sample")
        self.assertEqual(out["root"], "RootObject")
        self.assertEqual(out["types"], ["RootObject", "User", "Address"])


@unittest.skipUnless(HAS_PYDANTIC, "pydantic not installed (pip install -r requirements-dev.txt)")
class PydanticExecutionTests(unittest.TestCase):
    def load(self, code):
        # Pydantic resolves annotations through sys.modules, so give the code a real module.
        module = types.ModuleType(f"generated_models_{id(code)}")
        sys.modules[module.__name__] = module
        self.addCleanup(sys.modules.pop, module.__name__, None)
        exec(compile(code, "<generated>", "exec"), module.__dict__)  # noqa: S102 - code generated by this project
        return module.__dict__

    def test_generated_models_validate_their_own_source_document(self):
        documents = [(p.name, v) for p, v in valid_fixtures()] + [("users", USERS)]
        for name, value in documents:
            with self.subTest(document=name):
                generated = generate_types(value, mode="sample")
                namespace = self.load(generated["pydantic"])
                model = namespace[generated["root"]]
                instance = model.model_validate(value)
                self.assertEqual(instance.model_dump(by_alias=True, exclude_unset=True), value)

    def test_schema_models_accept_valid_and_reject_invalid_data(self):
        namespace = self.load(generate_types(SCHEMA)["pydantic"])
        order = namespace["RootObject"]
        order.model_validate({"id": "1", "status": None, "items": [{"sku": "a", "children": [{"sku": "b"}]}],
                              "parent": {"id": "0", "status": "open", "items": []}})
        with self.assertRaises(Exception):
            order.model_validate({"id": "1", "status": "pending", "items": []})


@unittest.skipUnless(GOFMT, "gofmt not installed")
class GoFormatTests(unittest.TestCase):
    def test_generated_go_is_gofmt_clean(self):
        documents = [v for _, v in valid_fixtures()] + [USERS, SCHEMA]
        for value in documents:
            code = generate_types(value)["go"]
            result = subprocess.run([GOFMT, "-l"], input=code, capture_output=True, text=True, check=False)
            formatted = subprocess.run([GOFMT], input=code, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(formatted.stdout, code)


if __name__ == "__main__":
    unittest.main()
