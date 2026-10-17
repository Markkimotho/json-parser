"""Interoperability & security auditor (static linter for JSON documents).

The auditor reads the *source text*, not an already-parsed value: duplicate
keys and the exact digits of a number disappear once a document has been
parsed. It runs the core parser with ``duplicate_keys="record"`` and an
observer that sees every key and scalar with its raw text, JSONPath and
line/column.

Checks (each one becomes a checklist row with status pass/warning/critical):

==================  ========  ==================================================
check               severity  why it matters
==================  ========  ==================================================
duplicate-keys      critical  Parsers disagree on which value wins (first, last,
                              error). Attackers use the disagreement to slip
                              values past a validator that reads a different
                              copy than the consumer does.
prototype-keys      critical  ``__proto__`` merged into a JavaScript object
                    /warning  rewrites the prototype of every object (prototype
                              pollution). ``constructor``/``prototype`` are the
                              second half of that chain.
safe-integers       warning   Integers beyond ±(2^53 - 1) silently lose
                              precision in JavaScript and every IEEE-754 double
                              consumer.
float-precision     warning   Decimals with more significant digits than a
                              double holds are rounded on the wire.
nesting-depth       warning   Deep nesting is a stack-exhaustion vector for
                    /critical recursive parsers and serializers. Depth > 64
                              is rejected outright by System.Text.Json and
                              Newtonsoft.Json defaults.
lone-surrogates     warning   Unpaired UTF-16 surrogates cannot be encoded as
                              UTF-8; strict decoders reject the document.
==================  ========  ==================================================

The overall status is ``CRITICAL`` if any check is critical, ``WARNING`` if
any check warns, else ``SECURE``. A document that does not parse gets
``INVALID``.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from .core import MAX_RECURSION_DEPTH, JSONParseError, Lexer, Parser, ScalarEvent

MAX_SAFE_INTEGER = 9007199254740991
MIN_SAFE_INTEGER = -9007199254740991
DEPTH_WARNING = 25
DEPTH_CRITICAL = 64
MAX_FINDINGS_PER_CHECK = 200

Severity = Literal["pass", "warning", "critical"]
Status = Literal["SECURE", "WARNING", "CRITICAL", "INVALID"]

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
_RANK = {"pass": 0, "warning": 1, "critical": 2}


@dataclass(slots=True)
class Finding:
    check: str
    severity: Literal["warning", "critical"]
    message: str
    path: str
    line: int
    column: int
    index: int
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Check:
    id: str
    title: str
    description: str
    status: Severity = "pass"
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    truncated: bool = False

    def add(self, finding: Finding) -> None:
        if _RANK[finding.severity] > _RANK[self.status]:
            self.status = finding.severity
        if len(self.findings) < MAX_FINDINGS_PER_CHECK:
            self.findings.append(finding)
        else:
            self.truncated = True


@dataclass(slots=True)
class AuditReport:
    status: Status
    checks: list[Check]
    stats: dict[str, Any]
    error: dict[str, Any] | None = None

    @property
    def findings(self) -> list[Finding]:
        return [finding for check in self.checks for finding in check.findings]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": [asdict(check) for check in self.checks],
            "findings": [asdict(finding) for finding in self.findings],
            "counts": {
                "critical": sum(1 for f in self.findings if f.severity == "critical"),
                "warning": sum(1 for f in self.findings if f.severity == "warning"),
            },
            "stats": self.stats,
            "error": self.error,
        }


def _float_rounds(raw: str, value: float) -> bool:
    """True if the decimal literal ``raw`` is not exactly the double it parses to."""
    try:
        return Decimal(raw) != Decimal(repr(value))
    except InvalidOperation:  # pragma: no cover - raw is validated by the lexer
        return False


def _integral_beyond_safe(raw: str) -> bool:
    """True for literals like ``1e16`` or ``9007199254740993.0`` that denote an
    integer outside the safe range even though they are written as floats."""
    try:
        exact = Decimal(raw)
    except InvalidOperation:  # pragma: no cover
        return False
    return exact == exact.to_integral_value() and not (MIN_SAFE_INTEGER <= exact <= MAX_SAFE_INTEGER)


class _Observer:
    def __init__(self, checks: dict[str, Check]):
        self.checks = checks
        self.keys = 0
        self.scalars = 0
        self.numbers = 0
        self.strings = 0

    def __call__(self, event: ScalarEvent) -> None:
        checks = self.checks
        if event.kind == "key":
            self.keys += 1
            key = event.value
            if key == "__proto__":
                checks["prototype-keys"].add(Finding(
                    "prototype-keys", "critical",
                    "Key '__proto__' can rewrite object prototypes when merged in JavaScript (prototype pollution)",
                    event.path, event.line, event.column, event.index, {"key": key},
                ))
            elif key in ("constructor", "prototype"):
                checks["prototype-keys"].add(Finding(
                    "prototype-keys", "warning",
                    f"Key '{key}' is part of the constructor.prototype pollution chain in JavaScript",
                    event.path, event.line, event.column, event.index, {"key": key},
                ))
            self._surrogates(event, "key")
            return

        self.scalars += 1
        if event.kind == "string":
            self.strings += 1
            self._surrogates(event, "string")
        elif event.kind == "number":
            self.numbers += 1
            self._number(event)

    def _surrogates(self, event: ScalarEvent, what: str) -> None:
        if _LONE_SURROGATE.search(event.value):
            self.checks["lone-surrogates"].add(Finding(
                "lone-surrogates", "warning",
                f"This {what} contains an unpaired UTF-16 surrogate that cannot be encoded as UTF-8",
                event.path, event.line, event.column, event.index,
            ))

    def _number(self, event: ScalarEvent) -> None:
        value, raw = event.value, event.raw
        if isinstance(value, int):
            if value > MAX_SAFE_INTEGER or value < MIN_SAFE_INTEGER:
                bound = "Number.MAX_SAFE_INTEGER" if value > 0 else "Number.MIN_SAFE_INTEGER"
                double = _as_double(value)
                consequence = (
                    f"JavaScript would read it as {double}" if double != raw
                    else "neighbouring integers collapse to the same double"
                )
                self.checks["safe-integers"].add(Finding(
                    "safe-integers", "warning",
                    f"{raw} exceeds {bound}; {consequence}",
                    event.path, event.line, event.column, event.index,
                    {"raw": raw, "javascript_value": _as_double(value)},
                ))
            return
        if _integral_beyond_safe(raw):
            self.checks["safe-integers"].add(Finding(
                "safe-integers", "warning",
                f"{raw} is an integer beyond the JavaScript safe range written in float notation",
                event.path, event.line, event.column, event.index, {"raw": raw, "javascript_value": repr(value)},
            ))
        elif _float_rounds(raw, value):
            self.checks["float-precision"].add(Finding(
                "float-precision", "warning",
                f"{raw} has more precision than a 64-bit float; it will arrive as {value!r}",
                event.path, event.line, event.column, event.index, {"raw": raw, "double_value": repr(value)},
            ))


def _as_double(value: int) -> str:
    as_float = float(value)
    return str(int(as_float)) if as_float.is_integer() else repr(as_float)


def _new_checks(depth_warning: int, depth_critical: int) -> dict[str, Check]:
    return {
        check.id: check
        for check in (
            Check("duplicate-keys", "Duplicate keys",
                  "Objects must not repeat a key: parsers disagree on which value wins."),
            Check("prototype-keys", "Prototype pollution keys",
                  "Flags __proto__, constructor and prototype keys."),
            Check("safe-integers", "Integer precision",
                  "Integers must fit within ±9007199254740991 (Number.MAX_SAFE_INTEGER)."),
            Check("float-precision", "Decimal precision",
                  "Decimals must survive a round trip through a 64-bit float."),
            Check("nesting-depth", "Nesting depth",
                  f"Warn above depth {depth_warning}; critical above {depth_critical}."),
            Check("lone-surrogates", "Unicode encoding",
                  "Strings must not contain unpaired UTF-16 surrogates."),
        )
    }


def audit_json(
    text: str,
    *,
    depth_warning: int = DEPTH_WARNING,
    depth_critical: int = DEPTH_CRITICAL,
    max_depth: int = MAX_RECURSION_DEPTH,
) -> AuditReport:
    """Audit JSON source text. See the module docs for the checks."""
    started = time.perf_counter()
    checks = _new_checks(depth_warning, depth_critical)
    observer = _Observer(checks)
    lexer = Lexer(text)
    try:
        parser = Parser(lexer, max_depth=max_depth, duplicate_keys="record", observer=observer)
        parser.parse()
    except JSONParseError as exc:
        return AuditReport("INVALID", list(checks.values()), {"bytes": len(text.encode("utf-8"))}, exc.to_dict())

    for duplicate in parser.duplicates:
        checks["duplicate-keys"].add(Finding(
            "duplicate-keys", "critical",
            f"Key '{duplicate.key}' repeats (first seen at line {duplicate.first_line}, "
            f"column {duplicate.first_column}); parsers disagree on which value wins",
            duplicate.path, duplicate.line, duplicate.column, duplicate.index,
            {"key": duplicate.key, "first_line": duplicate.first_line, "first_column": duplicate.first_column},
        ))

    depth = parser.max_depth_seen
    if depth > depth_warning:
        severity: Literal["warning", "critical"] = "critical" if depth > depth_critical else "warning"
        checks["nesting-depth"].add(Finding(
            "nesting-depth", severity,
            f"Nesting depth {depth} exceeds {depth_critical if severity == 'critical' else depth_warning}: "
            "a stack-exhaustion vector for recursive parsers and serializers"
            + ("; System.Text.Json and Newtonsoft.Json reject it by default" if severity == "critical" else ""),
            parser.deepest_path, *parser.deepest, {"depth": depth},
        ))

    for check in checks.values():
        count = len(check.findings) + (1 if check.truncated else 0)
        if check.status == "pass":
            check.summary = "No issues found"
        else:
            check.summary = f"{count}{'+' if check.truncated else ''} finding(s)"
    checks["nesting-depth"].summary = f"Max depth {depth}" + (
        "" if checks["nesting-depth"].status == "pass" else " (over threshold)"
    )

    statuses = {check.status for check in checks.values()}
    status: Status = "CRITICAL" if "critical" in statuses else "WARNING" if "warning" in statuses else "SECURE"
    stats = {
        "bytes": len(text.encode("utf-8")),
        "tokens": lexer.token_count,
        "keys": observer.keys,
        "scalars": observer.scalars,
        "numbers": observer.numbers,
        "strings": observer.strings,
        "max_depth": depth,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    return AuditReport(status, list(checks.values()), stats)
