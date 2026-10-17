"""Gate tests for engines/auditor.py (security & interoperability linter)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engines import auditor  # noqa: E402
from engines.auditor import MAX_SAFE_INTEGER, audit_json  # noqa: E402


def check(report, check_id):
    return next(c for c in report.checks if c.id == check_id)


class AuditTests(unittest.TestCase):
    def test_clean_document_is_secure_with_every_check_passing(self):
        report = audit_json('{"name": "Ada", "n": [1, 2.5, -3], "ok": true}')
        self.assertEqual(report.status, "SECURE")
        self.assertEqual([c.id for c in report.checks], [
            "duplicate-keys", "prototype-keys", "safe-integers", "float-precision", "nesting-depth", "lone-surrogates"])
        self.assertTrue(all(c.status == "pass" for c in report.checks))
        self.assertEqual(check(report, "nesting-depth").summary, "Max depth 2")
        self.assertEqual(check(report, "duplicate-keys").summary, "No issues found")
        self.assertEqual(report.findings, [])

    def test_duplicate_keys_are_critical_with_both_positions(self):
        report = audit_json('{\n  "role": "user",\n  "role": "admin"\n}')
        self.assertEqual(report.status, "CRITICAL")
        finding = check(report, "duplicate-keys").findings[0]
        self.assertEqual((finding.severity, finding.path, finding.line, finding.column), ("critical", "$.role", 3, 3))
        self.assertEqual(finding.detail, {"key": "role", "first_line": 2, "first_column": 3})
        self.assertIn("first seen at line 2, column 3", finding.message)

    def test_nested_duplicates_report_their_path(self):
        report = audit_json('{"a": [{"x": 1, "x": 2}]}')
        self.assertEqual(check(report, "duplicate-keys").findings[0].path, "$.a[0].x")

    def test_prototype_pollution_keys(self):
        report = audit_json('{"__proto__": {"admin": true}, "constructor": {"prototype": {"x": 1}}}')
        findings = check(report, "prototype-keys").findings
        self.assertEqual([(f.severity, f.path) for f in findings], [
            ("critical", "$.__proto__"), ("warning", "$.constructor"), ("warning", "$.constructor.prototype")])
        self.assertEqual(report.status, "CRITICAL")
        self.assertEqual(audit_json('{"constructor": 1}').status, "WARNING")

    def test_safe_integer_boundaries(self):
        self.assertEqual(audit_json(f"[{MAX_SAFE_INTEGER}, -{MAX_SAFE_INTEGER}]").status, "SECURE")
        report = audit_json(f'{{"id": {MAX_SAFE_INTEGER + 2}, "neg": -{MAX_SAFE_INTEGER + 1}}}')
        findings = check(report, "safe-integers").findings
        self.assertEqual(report.status, "WARNING")
        self.assertEqual(findings[0].message,
                         "9007199254740993 exceeds Number.MAX_SAFE_INTEGER; JavaScript would read it as 9007199254740992")
        self.assertEqual(findings[0].detail, {"raw": "9007199254740993", "javascript_value": "9007199254740992"})
        self.assertIn("Number.MIN_SAFE_INTEGER; neighbouring integers collapse", findings[1].message)

    def test_integers_written_as_floats(self):
        findings = check(audit_json("[1e16, 9007199254740993.0, 1e15, 2.5e3]"), "safe-integers").findings
        self.assertEqual([f.detail["raw"] for f in findings], ["1e16", "9007199254740993.0"])

    def test_float_precision(self):
        report = audit_json("[0.1, 1.0, 1e-7, 3.14159265358979323846, 1e-400, 0.30000000000000004]")
        findings = check(report, "float-precision").findings
        self.assertEqual([f.detail["raw"] for f in findings], ["3.14159265358979323846", "1e-400"])
        self.assertEqual(findings[1].detail["double_value"], "0.0")

    def test_depth_thresholds_and_location(self):
        self.assertEqual(audit_json("[" * 25 + "]" * 25).status, "SECURE")
        warning = audit_json('{"a": ' + "[" * 26 + "]" * 26 + "}")
        finding = check(warning, "nesting-depth").findings[0]
        self.assertEqual((warning.status, finding.severity, finding.detail), ("WARNING", "warning", {"depth": 27}))
        self.assertEqual(finding.path, "$.a" + "[0]" * 25)
        self.assertEqual(check(warning, "nesting-depth").summary, "Max depth 27 (over threshold)")
        critical = audit_json("[" * 65 + "]" * 65)
        self.assertEqual(critical.status, "CRITICAL")
        self.assertIn("System.Text.Json", critical.findings[0].message)
        custom = audit_json("[[[]]]", depth_warning=1, depth_critical=2)
        self.assertEqual(custom.status, "CRITICAL")

    def test_lone_surrogates_in_keys_and_values(self):
        report = audit_json('{"\\udc00": "ok", "v": "\\ud800", "pair": "\\ud83d\\ude00"}')
        findings = check(report, "lone-surrogates").findings
        self.assertEqual([(f.path, f.message.split()[1]) for f in findings], [("$['\udc00']", "key"), ("$.v", "string")])

    def test_invalid_documents(self):
        report = audit_json('{"a": }')
        self.assertEqual(report.status, "INVALID")
        self.assertEqual(report.error, {"error": "Expected JSON value, got RBRACE", "line": 1, "column": 7, "index": 6})
        self.assertEqual(report.to_dict()["status"], "INVALID")

    def test_findings_are_capped_per_check(self):
        original = auditor.MAX_FINDINGS_PER_CHECK
        auditor.MAX_FINDINGS_PER_CHECK = 3
        try:
            report = audit_json("[" + ",".join(["9007199254740993"] * 5) + "]")
        finally:
            auditor.MAX_FINDINGS_PER_CHECK = original
        safe = check(report, "safe-integers")
        self.assertEqual((len(safe.findings), safe.truncated, safe.summary), (3, True, "4+ finding(s)"))

    def test_report_dict_shape_and_stats(self):
        body = audit_json('{"a": [1, "x", 9007199254740993], "a": null}').to_dict()
        self.assertEqual(body["status"], "CRITICAL")
        self.assertEqual(body["counts"], {"critical": 1, "warning": 1})
        self.assertEqual({k: body["stats"][k] for k in ("tokens", "keys", "scalars", "numbers", "strings", "max_depth")},
                         {"tokens": 16, "keys": 2, "scalars": 4, "numbers": 2, "strings": 1, "max_depth": 2})
        self.assertEqual(len(body["checks"]), 6)
        self.assertEqual(set(body["findings"][0]),
                         {"check", "severity", "message", "path", "line", "column", "index", "detail"})


if __name__ == "__main__":
    unittest.main()
