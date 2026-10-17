"""Periodic eval lane: quality and performance measurements with thresholds.

Gate tests (resources/tests) prove behaviour on fixed cases in under two
seconds. This lane measures how well the engines do on realistic inputs and
uses slower external toolchains:

* healing      -- repair success on evals/healing_corpus.jsonl (LLM output,
                  truncation, Python/JS literals, JSONC, NDJSON, ...)
* compression  -- exact round trips and token savings over every fixture
                  (real tiktoken counts when tiktoken is importable)
* types        -- generated TypeScript passes `tsc --strict`, generated Go
                  passes `go vet`, generated Pydantic models validate the
                  document they came from
* performance  -- parse throughput with and without instrumentation

Usage:
    python evals/run_evals.py                 # all suites
    python evals/run_evals.py healing types   # selected suites
    python evals/run_evals.py --json out.json # also write a JSON report

Exits non-zero when any threshold fails. Suites whose toolchain is missing
report SKIPPED rather than passing silently.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import types as pytypes
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engines import (  # noqa: E402
    Lexer,
    Parser,
    compress_json,
    decode_compact,
    encode_compact,
    estimate_tokens,
    generate_types,
    heal,
    minify,
    parse,
)
from engines.core import JSONParseError  # noqa: E402

FIXTURES = ROOT / "resources" / "tests"
THRESHOLDS = {
    "healing_success_rate": 1.0,  # every corpus case must heal to its expected value
    "compact_round_trip_rate": 1.0,
    "compact_median_savings_vs_pretty_pct": 30.0,
    "types_compile_rate": 1.0,
    "plain_parse_mb_per_s": 1.0,
    "instrumentation_overhead_max_x": 12.0,
}


def fixtures() -> list[tuple[str, str, Any]]:
    out = []
    for path in sorted(FIXTURES.rglob("*.json")):
        text = path.read_text()
        try:
            out.append((str(path.relative_to(FIXTURES)), text, parse(text)))
        except JSONParseError:
            continue
    return out


def strict_equal(a: Any, b: Any) -> bool:
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(strict_equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(strict_equal(x, y) for x, y in zip(a, b))
    return a == b


# ------------------------------------------------------------------ suites


def eval_healing() -> dict[str, Any]:
    cases = [json.loads(line) for line in (ROOT / "evals" / "healing_corpus.jsonl").read_text().splitlines() if line]
    by_category: dict[str, list[bool]] = {}
    failures = []
    repairs = []
    for case in cases:
        result = heal(case["input"])
        ok = result.valid and result.value == case["expected"]
        by_category.setdefault(case["category"], []).append(ok)
        repairs.append(len(result.repairs))
        if not ok:
            failures.append({"input": case["input"][:80], "healed": result.healed[:80], "error": result.error})
    rate = sum(all_ok for oks in by_category.values() for all_ok in oks) / len(cases)
    return {
        "metric": {"healing_success_rate": rate},
        "passed": rate >= THRESHOLDS["healing_success_rate"],
        "detail": {
            "cases": len(cases),
            "by_category": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_category.items())},
            "mean_repairs_per_case": round(statistics.mean(repairs), 2),
            "failures": failures,
        },
    }


def eval_compression() -> dict[str, Any]:
    try:
        import tiktoken  # type: ignore[import-not-found]

        encoders = [tiktoken.get_encoding(name) for name in ("cl100k_base", "o200k_base")]
    except Exception:  # noqa: BLE001 - optional dependency
        encoders = []
    round_trips = []
    savings_pretty = []
    savings_minified = []
    real_rows = []
    for name, _, value in fixtures():
        if value in ({}, [], None, "") or not isinstance(value, (dict, list)):
            continue
        compact = encode_compact(value)
        round_trips.append(strict_equal(decode_compact(compact), value))
        pretty = json.dumps(value, indent=2, ensure_ascii=False)
        mini = minify(value)
        savings_pretty.append(100 * (1 - estimate_tokens(compact) / estimate_tokens(pretty)))
        savings_minified.append(100 * (1 - estimate_tokens(compact) / estimate_tokens(mini)))
        for encoder in encoders:
            real_rows.append((encoder.name, len(encoder.encode(pretty)), len(encoder.encode(mini)), len(encoder.encode(compact))))
    detail: dict[str, Any] = {
        "documents": len(round_trips),
        "estimated_median_savings_vs_minified_pct": round(statistics.median(savings_minified), 1),
        "compact_cheaper_than_minified": f"{sum(s > 0 for s in savings_minified)}/{len(savings_minified)}",
    }
    for encoder in encoders:
        rows = [r for r in real_rows if r[0] == encoder.name]
        totals = [sum(r[i] for r in rows) for i in (1, 2, 3)]
        detail[f"{encoder.name}_total_tokens"] = {"pretty": totals[0], "minified": totals[1], "compact": totals[2],
                                                 "compact_vs_minified_pct": round(100 * (1 - totals[2] / totals[1]), 1)}
    if not encoders:
        detail["real_tokenizer"] = "SKIPPED (pip install tiktoken to measure real token counts)"
    metric = {
        "compact_round_trip_rate": sum(round_trips) / len(round_trips),
        "compact_median_savings_vs_pretty_pct": round(statistics.median(savings_pretty), 1),
    }
    passed = (metric["compact_round_trip_rate"] >= THRESHOLDS["compact_round_trip_rate"]
              and metric["compact_median_savings_vs_pretty_pct"] >= THRESHOLDS["compact_median_savings_vs_pretty_pct"])
    return {"metric": metric, "passed": passed, "detail": detail}


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> tuple[bool, str]:
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    return result.returncode == 0, (result.stdout + result.stderr)[-2000:]


def eval_types() -> dict[str, Any]:
    documents = [(name, value) for name, _, value in fixtures()]
    documents.append(("schema/order", json.loads((ROOT / "evals" / "order.schema.json").read_text())))
    outcomes: dict[str, Any] = {}
    work = Path(tempfile.mkdtemp(prefix="jde-types-"))
    generated = {name: generate_types(value) for name, value in documents}

    # TypeScript
    npx = shutil.which("npx")
    if npx:
        ts_dir = work / "ts"
        ts_dir.mkdir()
        for i, (name, out) in enumerate(generated.items()):
            (ts_dir / f"m{i}.ts").write_text(out["typescript"] + "export {};\n")
        ok, log = _run([npx, "-y", "-p", "typescript@5.6.3", "tsc", "--noEmit", "--strict", *sorted(str(p) for p in ts_dir.glob("*.ts"))])
        outcomes["typescript"] = {"passed": ok, "files": len(generated), "log": "" if ok else log}
    else:
        outcomes["typescript"] = "SKIPPED (npx not found)"

    # Go
    go = shutil.which("go")
    if go:
        failures = []
        for i, (name, out) in enumerate(generated.items()):
            pkg = work / f"go{i}"
            pkg.mkdir()
            (pkg / "go.mod").write_text("module m\n\ngo 1.21\n")
            (pkg / "models.go").write_text(out["go"])
            ok, log = _run([go, "vet", "./..."], cwd=pkg)
            if not ok:
                failures.append({"document": name, "log": log})
        outcomes["go"] = {"passed": not failures, "files": len(generated), "failures": failures}
    else:
        outcomes["go"] = "SKIPPED (go not found)"

    # Pydantic
    if importlib.util.find_spec("pydantic"):
        failures = []
        for name, value in documents:
            if name.startswith("schema/"):
                continue
            out = generated[name]
            module = pytypes.ModuleType(f"jde_eval_{abs(hash(name))}")
            sys.modules[module.__name__] = module
            try:
                exec(compile(out["pydantic"], name, "exec"), module.__dict__)  # noqa: S102 - generated by this project
                model = module.__dict__[out["root"]]
                dumped = model.model_validate(value).model_dump(by_alias=True, exclude_unset=True)
                if dumped != value:
                    failures.append({"document": name, "error": "round trip changed the value"})
            except Exception as exc:  # noqa: BLE001
                failures.append({"document": name, "error": repr(exc)[:300]})
            finally:
                sys.modules.pop(module.__name__, None)
        outcomes["pydantic"] = {"passed": not failures, "files": len(documents) - 1, "failures": failures}
    else:
        outcomes["pydantic"] = "SKIPPED (pip install -r requirements-dev.txt)"

    ran = [v for v in outcomes.values() if isinstance(v, dict)]
    rate = sum(v["passed"] for v in ran) / len(ran) if ran else 0.0
    shutil.rmtree(work, ignore_errors=True)
    return {"metric": {"types_compile_rate": rate}, "passed": bool(ran) and rate >= THRESHOLDS["types_compile_rate"],
            "detail": outcomes}


def eval_performance() -> dict[str, Any]:
    rows = [{"id": i, "name": f"user {i}", "email": f"u{i}@example.com", "active": i % 2 == 0,
             "score": i * 1.25, "tags": ["a", "b", "c"], "meta": {"created": "2026-10-17", "n": None}} for i in range(12_000)]
    text = json.dumps({"rows": rows}, indent=2)
    size_mb = len(text.encode()) / 1024 / 1024

    def best(fn: Any, repeats: int = 3) -> float:
        times = []
        for _ in range(repeats):
            started = time.perf_counter()
            fn()
            times.append(time.perf_counter() - started)
        return min(times)

    plain = best(lambda: Parser(Lexer(text)).parse())
    instrumented = best(lambda: Parser(Lexer(text, capture_tokens=True), capture_ast=True, trace=True,
                                       trace_limit=10**9).parse(), repeats=1)
    stdlib = best(lambda: json.loads(text))
    metric = {
        "plain_parse_mb_per_s": round(size_mb / plain, 2),
        "instrumentation_overhead_x": round(instrumented / plain, 2),
    }
    passed = (metric["plain_parse_mb_per_s"] >= THRESHOLDS["plain_parse_mb_per_s"]
              and metric["instrumentation_overhead_x"] <= THRESHOLDS["instrumentation_overhead_max_x"])
    return {"metric": metric, "passed": passed, "detail": {
        "document_mb": round(size_mb, 2), "plain_s": round(plain, 3), "instrumented_s": round(instrumented, 3),
        "stdlib_json_s": round(stdlib, 4), "note": "stdlib json is C-accelerated; shown for scale, not as a target"}}


SUITES = {"healing": eval_healing, "compression": eval_compression, "types": eval_types, "performance": eval_performance}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("suites", nargs="*", metavar="SUITE", help=f"any of: {', '.join(SUITES)}")
    parser.add_argument("--json", type=Path, help="write the full report to this path")
    args = parser.parse_args()
    report: dict[str, Any] = {"thresholds": THRESHOLDS, "suites": {}}
    failed = False
    unknown = set(args.suites) - set(SUITES)
    if unknown:
        parser.error(f"unknown suite(s): {', '.join(sorted(unknown))}")
    for name in args.suites or SUITES:
        started = time.perf_counter()
        result = SUITES[name]()
        result["seconds"] = round(time.perf_counter() - started, 1)
        report["suites"][name] = result
        failed |= not result["passed"]
        status = "PASS" if result["passed"] else "FAIL"
        print(f"[{status}] {name:<12} {json.dumps(result['metric'])}  ({result['seconds']}s)")
        for key, value in result["detail"].items():
            if value in ([], {}):
                continue
            print(f"         {key}: {json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value}")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"report written to {args.json}")
    return 1 if failed else 0


if __name__ == "__main__":
    os.environ.setdefault("PYTHONHASHSEED", "0")
    sys.exit(main())
