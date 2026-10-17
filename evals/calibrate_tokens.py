"""Fit the constants of engines.compressor.estimate_tokens against real
tokenizers.

Needs ``tiktoken`` (not a project dependency; install it in a scratch venv):

    python -m venv /tmp/tk && /tmp/tk/bin/pip install tiktoken
    PYTHONPATH=. /tmp/tk/bin/python evals/calibrate_tokens.py

The corpus is every valid fixture rendered four ways (source, pretty,
minified, compact) plus synthetic tabular/nested/unicode documents. The
script grid-searches the per-class divisors and prints the best set with its
mean and worst absolute percentage error per tokenizer. Copy the winning
constants into ``engines/compressor.py``.
"""

from __future__ import annotations

import itertools
import json
import math
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tiktoken  # noqa: E402

from engines.compressor import encode_compact, minify  # noqa: E402
from engines.core import JSONParseError, parse  # noqa: E402

PIECES = re.compile(r" ?[A-Za-z_]+| ?\d+| ?[^\x00-\x7f]+|[^\sA-Za-z\d_\x80-\U0010ffff]+|\s+")
WORD = re.compile(r"[A-Za-z_]+")


def corpus() -> list[str]:
    docs = []
    for path in sorted((ROOT / "resources" / "tests").rglob("*.json")):
        try:
            docs.append((path.read_text(), parse(path.read_text())))
        except JSONParseError:
            continue
    synthetic = [
        {"users": [{"id": i, "name": f"User {i}", "email": f"u{i}@example.com", "active": i % 2 == 0,
                    "score": round(i * 1.37, 2)} for i in range(50)]},
        {"order": {"id": "A-1001", "customer": {"name": "Ada Lovelace", "address": {"street": "12 St James Sq",
                   "city": "London"}}, "items": [{"sku": "X-1", "qty": 2, "tags": ["gift", "fragile"]},
                   {"sku": "Y-2", "qty": 1, "meta": {"note": "leave at door"}}], "total": 129.5}},
        {"i18n": {"ja": "こんにちは世界", "ru": "Привет мир", "emoji": "🚀🔥", "de": "Grüße aus München"}},
        {"matrix": [[i * j for j in range(12)] for i in range(12)]},
        {"log": [{"ts": f"2026-10-17T10:{m:02d}:00Z", "level": "INFO" if m % 3 else "WARN",
                  "msg": f"request {m} served in {m * 7} ms"} for m in range(30)]},
    ]
    docs += [(json.dumps(d, indent=2, ensure_ascii=False), d) for d in synthetic]
    texts = []
    for source, value in docs:
        texts += [source, json.dumps(value, indent=2, ensure_ascii=False), minify(value), encode_compact(value)]
    return [t for t in texts if t.strip()]


def estimate(text: str, letters: float, digits: float, punct: float, space: float, unicode: float) -> int:
    total = 0
    for piece in PIECES.findall(text):
        core = piece.strip(" ")
        if WORD.fullmatch(core):
            total += max(1, math.ceil(len(core) / letters))
        elif core.isdigit():
            total += max(1, math.ceil(len(core) / digits))
        elif not core.isascii():
            total += max(1, math.ceil(len(core) / unicode))
        elif piece.isspace():
            total += max(1, math.ceil(len(piece) / space))
        else:
            total += max(1, math.ceil(len(piece) / punct))
    return total


def main() -> None:
    texts = corpus()
    encoders = [tiktoken.get_encoding(name) for name in ("cl100k_base", "o200k_base")]
    real = {enc.name: [len(enc.encode(t)) for t in texts] for enc in encoders}
    grid = itertools.product((6, 8, 10, 12, 16), (2, 3, 4), (2, 3, 4, 5), (8, 16, 32), (1, 1.5, 2, 3))
    best = None
    for params in grid:
        estimates = [estimate(t, *params) for t in texts]
        errors = {name: [abs(e - r) / r for e, r in zip(estimates, counts)] for name, counts in real.items()}
        score = statistics.mean(statistics.mean(v) for v in errors.values())
        if best is None or score < best[0]:
            best = (score, params, errors)
    assert best is not None
    score, params, errors = best
    print(f"corpus: {len(texts)} texts")
    print("best (letters, digits, punct, space, unicode):", params)
    for name, errs in errors.items():
        print(f"  {name}: mean abs error {100 * statistics.mean(errs):.1f}%, worst {100 * max(errs):.1f}%")


if __name__ == "__main__":
    main()


def worst(params: tuple[float, ...], count: int = 6) -> None:
    texts = corpus()
    enc = tiktoken.get_encoding("o200k_base")
    rows = sorted(((abs(estimate(t, *params) - len(enc.encode(t))) / len(enc.encode(t)), estimate(t, *params),
                    len(enc.encode(t)), t[:70].replace("\n", "⏎")) for t in texts), reverse=True)
    for row in rows[:count]:
        print(f"  {100 * row[0]:.0f}% est={row[1]} real={row[2]} {row[3]!r}")
