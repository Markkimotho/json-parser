"""Gate tests for the Flask routing layer (app.py): every endpoint, input
mode, error path and limit, plus the dashboard page contract."""

import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import app as app_module  # noqa: E402
from app import app, documents  # noqa: E402


class ApiTestCase(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
        documents.clear()

    def post(self, route, **body):
        response = self.client.post(route, json=body)
        return response.status_code, response.get_json()

    def form(self, route, **fields):
        response = self.client.post(route, data=fields)
        return response.status_code, response.get_json()


class InputHandlingTests(ApiTestCase):
    def test_form_json_and_file_inputs(self):
        status, body = self.form("/parse-json", jsonData='{"a": 1}')
        self.assertEqual((status, body["source"], body["result"]), (200, "form", {"a": 1}))
        status, body = self.post("/parse-json", jsonData='{"a": 1}')
        self.assertEqual((status, body["source"]), (200, "json"))
        response = self.client.post("/parse-json", data={"jsonFile": (io.BytesIO(b'[1, 2]'), "x.json")},
                                    content_type="multipart/form-data")
        self.assertEqual((response.status_code, response.json["source"], response.json["result"]), (200, "file", [1, 2]))

    def test_input_errors(self):
        cases = [
            (lambda: self.client.post("/parse-json", data={}), 400, "No JSON data or file provided"),
            (lambda: self.client.post("/parse-json", data={"jsonData": "   "}), 400, "JSON input is empty"),
            (lambda: self.client.post("/parse-json", json={"jsonData": 5}), 400, "jsonData must be a string"),
            (lambda: self.client.post("/parse-json", json=[1]), 400, "JSON request bodies must be an object"),
            (lambda: self.client.post("/parse-json", data={"jsonFile": (io.BytesIO(b""), "e.json")},
                                      content_type="multipart/form-data"), 400, "File is empty"),
            (lambda: self.client.post("/parse-json", data={"jsonFile": (io.BytesIO(b"\xff\xfe"), "b.json")},
                                      content_type="multipart/form-data"), 400, "File is not valid UTF-8 encoded"),
            (lambda: self.client.post("/parse-json", data={"jsonFile": (io.BytesIO(b"[1]"), "")},
                                      content_type="multipart/form-data"), 400, "No file selected"),
        ]
        for send, status, message in cases:
            with self.subTest(message=message):
                response = send()
                self.assertEqual(response.status_code, status)
                self.assertIn(message, response.json["error"])
                self.assertFalse(response.json["valid"])

    def test_size_limits(self):
        original = app_module.MAX_FILE_SIZE
        app_module.MAX_FILE_SIZE = 10
        try:
            status, body = self.post("/parse-json", jsonData='{"long": "value"}')
            self.assertEqual((status, body["error"]), (413, "JSON input exceeds the 10MB limit"))
            response = self.client.post("/parse-json", data={"jsonFile": (io.BytesIO(b'[1,2,3,4,5,6,7]'), "big.json")},
                                        content_type="multipart/form-data")
            self.assertEqual(response.status_code, 400)
            self.assertIn("File size exceeds maximum", response.json["error"])
        finally:
            app_module.MAX_FILE_SIZE = original
        response = self.client.post("/parse-json", data=b"x" * (app.config["MAX_CONTENT_LENGTH"] + 1),
                                    content_type="application/octet-stream")
        self.assertEqual(response.status_code, 413)

    def test_unexpected_errors_become_500(self):
        original = app_module.heal
        app_module.heal = lambda text: 1 / 0
        try:
            with self.assertLogs(app.logger, "ERROR") as logs:
                status, body = self.post("/heal-json", jsonData="[1,]")
        finally:
            app_module.heal = original
        self.assertEqual((status, body), (500, {"error": "Could not process JSON", "valid": False}))
        self.assertIn("Could not process JSON in /heal-json", logs.output[0])


class ParseRouteTests(ApiTestCase):
    def test_success_payload_with_visuals(self):
        status, body = self.post("/parse-json", jsonData='{"b": 1, "a": [true, null]}')
        self.assertEqual(status, 200)
        self.assertEqual(list(body["result"]), ["b", "a"])  # document order, not sorted
        self.assertEqual(body["formatted"], '{\n  "b": 1,\n  "a": [\n    true,\n    null\n  ]\n}')
        self.assertEqual(body["minified"], '{"b":1,"a":[true,null]}')
        self.assertEqual(body["healing"], {"applied": False, "repairs": []})
        self.assertEqual(body["tokens"][0]["type"], "LBRACE")
        self.assertEqual(body["tokens"][-1]["type"], "EOF")
        self.assertEqual(body["trace"][0]["method"], "parse()")
        self.assertEqual(body["ast"]["kind"], "object")
        self.assertEqual(body["stats"]["tokens"], len(body["tokens"]))
        self.assertEqual(body["stats"]["max_depth"], 2)
        self.assertEqual(body["stats"]["nodes"], 9)  # object, 2x(property, key, value), array items
        self.assertTrue(body["visual"]["available"])
        self.assertFalse(body["visual"]["tokens_truncated"])

    def test_integers_survive_and_positions_match_the_editor(self):
        status, body = self.post("/parse-json", jsonData='\n\n  {"n": 9007199254740993}')
        self.assertEqual(body["minified"], '{"n":9007199254740993}')
        number = next(t for t in body["tokens"] if t["type"] == "NUMBER")
        self.assertEqual((number["line"], number["column"], number["raw"]), (3, 9, "9007199254740993"))

    def test_strict_errors_return_partial_visuals(self):
        status, body = self.post("/parse-json", jsonData='{\n  "a": 1,\n}')
        self.assertEqual(status, 400)
        self.assertEqual((body["error"], body["line"], body["column"], body["index"]), ("Trailing comma in object", 3, 1, 12))
        self.assertEqual(body["tokens"][-1]["type"], "RBRACE")
        self.assertEqual(body["trace"][-1]["action"], "error")
        self.assertIsNone(body["ast"])
        status, body = self.post("/parse-json", jsonData="{bad")
        self.assertEqual((status, body["trace"][-1]["action"]), (400, "error"))  # lexer error mid-parse
        status, body = self.post("/parse-json", jsonData="@")
        self.assertEqual((status, body["trace"], body["tokens"]), (400, [], []))  # fails before the first token

    def test_duplicates_and_nonfinite_are_rejected(self):
        self.assertIn("Duplicate key", self.post("/parse-json", jsonData='{"a":1,"a":2}')[1]["error"])
        self.assertEqual(self.post("/parse-json", jsonData="1e999")[0], 400)

    def test_visual_limits(self):
        status, body = self.post("/parse-json", jsonData="[1]", visualize=False)
        self.assertEqual((body["tokens"], body["trace"], body["ast"]), ([], [], None))
        self.assertEqual(body["visual"]["reason"], "Visualization disabled by request")
        self.assertIsNone(body["stats"]["nodes"])
        big = json.dumps({"rows": [{"id": i, "name": "x" * 50} for i in range(10_000)]})
        status, body = self.post("/parse-json", jsonData=big)
        self.assertEqual(status, 200)
        self.assertFalse(body["visual"]["available"])
        self.assertIn("512KB", body["visual"]["reason"])
        original = app_module.TOKEN_LIMIT, app_module.AST_NODE_LIMIT, app_module.TRACE_LIMIT
        app_module.TOKEN_LIMIT, app_module.AST_NODE_LIMIT, app_module.TRACE_LIMIT = 5, 5, 5
        try:
            body = self.post("/parse-json", jsonData="[1, 2, 3, 4, 5]")[1]
        finally:
            app_module.TOKEN_LIMIT, app_module.AST_NODE_LIMIT, app_module.TRACE_LIMIT = original
        self.assertEqual(len(body["tokens"]), 5)
        self.assertTrue(all(body["visual"][k] for k in ("tokens_truncated", "trace_truncated", "ast_truncated")))

    def test_lenient_mode_heals_then_parses(self):
        status, body = self.post("/parse-json", jsonData="{name: 'Ada', tags: [1, 2,],}", lenient=True)
        self.assertEqual(status, 200)
        self.assertEqual(body["result"], {"name": "Ada", "tags": [1, 2]})
        self.assertTrue(body["healing"]["applied"])
        self.assertEqual(body["healing"]["healed_text"], '{"name": "Ada", "tags": [1, 2]}')
        self.assertEqual([r["kind"] for r in body["healing"]["repairs"]], [
            "unquoted-key", "single-quoted-string", "unquoted-key", "trailing-comma", "trailing-comma"])
        self.assertEqual(body["healing"]["original_error"]["error"], "Unexpected character 'n'")
        self.assertEqual(body["tokens"][1]["raw"], '"name"')  # visuals describe the healed text

    def test_lenient_form_flag_and_failure(self):
        status, body = self.form("/parse-json", jsonData="[1,]", lenient="on")
        self.assertEqual(status, 200)
        status, body = self.post("/parse-json", jsonData="{'x': 1e999}", lenient="true")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "Could not heal JSON: Number out of range: 1e999")
        self.assertFalse(body["healing"]["applied"])
        self.assertEqual(body["healing"]["healed_text"], '{"x": 1e999}')


class EngineRouteTests(ApiTestCase):
    def test_heal(self):
        status, body = self.post("/heal-json", jsonData="// c\n{a: 1,}")
        self.assertEqual(status, 200)
        self.assertEqual((body["healed"], body["valid"], body["strict"], body["changed"]), ('{"a": 1}', True, False, True))
        self.assertEqual([r["kind"] for r in body["repairs"]], ["comment-removed", "unquoted-key", "trailing-comma"])
        self.assertEqual(body["formatted"], '{\n  "a": 1\n}')
        status, body = self.post("/heal-json", jsonData="[1]")
        self.assertEqual((body["strict"], body["changed"], body["repairs"]), (True, False, []))
        status, body = self.post("/heal-json", jsonData="[1e999")
        self.assertEqual((status, body["valid"]), (200, False))
        self.assertNotIn("formatted", body)

    def test_generate_types(self):
        status, body = self.post("/generate-types", jsonData='{"users": [{"id": 1}]}', rootName="Payload")
        self.assertEqual(status, 200)
        self.assertEqual((body["mode"], body["root"], body["names"]), ("sample", "Payload", ["Payload", "User"]))
        self.assertIn("export interface Payload", body["types"]["typescript"])
        self.assertIn("class Payload(BaseModel)", body["types"]["pydantic"])
        self.assertIn("type Payload struct", body["types"]["go"])
        schema = '{"type": "object", "properties": {"a": {"type": "string"}}}'
        self.assertEqual(self.post("/generate-types", jsonData=schema)[1]["mode"], "schema")
        self.assertEqual(self.post("/generate-types", jsonData=schema, mode="sample")[1]["mode"], "sample")
        status, body = self.post("/generate-types", jsonData=schema, mode="guess")
        self.assertEqual((status, body["error"]), (400, "mode must be auto, sample or schema"))
        status, body = self.post("/generate-types", jsonData='{"$ref": "http://x"}', mode="schema")
        self.assertEqual(status, 422)
        status, body = self.post("/generate-types", jsonData="{bad")
        self.assertEqual((status, body["line"]), (400, 1))

    def test_audit(self):
        status, body = self.post("/audit-json", jsonData='{"__proto__": 1, "n": 9007199254740993}')
        self.assertEqual((status, body["audit"]["status"]), (200, "CRITICAL"))
        self.assertEqual(body["audit"]["counts"], {"critical": 1, "warning": 1})
        status, body = self.post("/audit-json", jsonData='{"a": }')
        self.assertEqual((status, body["error"], body["audit"]["status"]), (400, "Expected JSON value, got RBRACE", "INVALID"))

    def test_query(self):
        doc = '{"users": [{"name": "Ada", "meta": {"active": true}}, {"name": "Bo", "meta": {"active": false}}]}'
        status, body = self.post("/query-json", jsonData=doc, path="$.users[0].meta.active")
        self.assertEqual(status, 200)
        self.assertEqual((body["count"], body["result"], body["cached"], body["truncated"]), (1, [True], False, False))
        self.assertEqual(body["matches"][0], {"path": "$.users[0].meta.active", "value": True, "value_json": "true"})
        status, body = self.post("/query-json", jsonData=doc, query="$.users[*].name", limit=1)
        self.assertEqual((body["result"], body["truncated"], body["cached"]), (["Ada"], True, True))
        status, body = self.form("/query-json", jsonData=doc)
        self.assertEqual(body["path"], "$")
        status, body = self.post("/query-json", jsonData=doc, path="$.users[")
        self.assertEqual((status, body["error"], body["position"]), (400, "Expected a selector", 8))
        status, body = self.post("/query-json", jsonData=doc, path="$", limit="many")
        self.assertEqual((status, body["error"]), (400, "limit must be an integer"))

    def test_query_previews_are_capped(self):
        original = app_module.QUERY_VALUE_PREVIEW
        app_module.QUERY_VALUE_PREVIEW = 10
        try:
            body = self.post("/query-json", jsonData='{"a": [1, 2, 3, 4, 5]}', path="$.a")[1]
        finally:
            app_module.QUERY_VALUE_PREVIEW = original
        self.assertTrue(body["matches"][0]["value_json"].endswith("\n…"))

    def test_compress(self):
        doc = json.dumps({"users": [{"id": i, "name": f"U{i}"} for i in range(10)]}, indent=2)
        status, body = self.post("/compress-json", jsonData=doc)
        self.assertEqual(status, 200)
        self.assertEqual(set(body["modes"]), {"compact", "minified"})
        self.assertTrue(body["modes"]["compact"]["output"].startswith("users[10]{id,name}:"))
        self.assertEqual(body["modes"]["minified"]["stats"]["original_chars"], len(doc))
        status, body = self.post("/compress-json", jsonData=doc, mode="minified")
        self.assertEqual(list(body["modes"]), ["minified"])
        self.assertEqual(self.post("/compress-json", jsonData=doc, mode="gzip")[0], 400)


class PageTests(ApiTestCase):
    def test_routes(self):
        self.assertEqual(self.client.get("/").headers["Location"], "/engine/")
        for route in ("/python/", "/python/app.js", "/terminal/"):
            response = self.client.get(route)
            self.assertEqual(response.status_code, 200, route)
            response.close()
        self.assertEqual(self.client.get("/python/secret.py").status_code, 404)
        icon = self.client.get("/favicon.ico")
        self.assertEqual((icon.status_code, icon.mimetype), (200, "image/svg+xml"))
        icon.close()

    def test_dashboard_contract(self):
        response = self.client.get("/engine/")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for element_id in ("editor", "file-input", "btn-prettify", "btn-minify", "opt-lenient", "status-badge",
                           "panel-ast", "panel-trace", "panel-fuzzy", "panel-polyglot", "panel-audit",
                           "panel-jsonpath", "panel-compress", "trace-step", "token-stream", "jp-input", "poly-copy"):
            self.assertIn(f'id="{element_id}"', html)
        for asset in ("engine.css", "engine.js"):
            self.assertIn(f"/static/engine/{asset}", html)
            asset_response = self.client.get(f"/static/engine/{asset}")
            self.assertEqual(asset_response.status_code, 200)
            asset_response.close()


if __name__ == "__main__":
    unittest.main()
