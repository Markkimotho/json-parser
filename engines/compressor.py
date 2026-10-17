"""LLM token compressor.

Two output modes:

* ``minified``: standard JSON with no optional whitespace and non-ASCII text
  kept literal (``\\u00e9`` escapes cost more tokens than ``é``). Any JSON
  consumer can read it.
* ``compact``: a TOON-style (Token-Oriented Object Notation) layout made for
  prompts. It drops the braces, brackets and quotes models do not need, and
  turns arrays of uniform objects into tables with a single header row, so a
  repeated key costs nothing per row. :func:`decode_compact` reverses it
  exactly; the test suite round-trips every fixture through
  ``decode_compact(encode_compact(x)) == x``.

Layout choices were measured with real tokenizers (tiktoken cl100k_base and
o200k_base, fixture corpus): one-space indentation and no space after ``:``
cost about 13% fewer tokens than minified JSON and 47% fewer than 2-space
pretty JSON. Two-space indentation with ``": "`` only saved about 5% against
minified, because the indent and separator spaces each became their own token.
Deeply nested objects with few repeated keys can still be cheaper as minified
JSON, so callers can request either mode and compare.

Compact grammar (one space per level; ``N`` is the item count)::

    key:value                       primitive field
    key:                            object field (members follow, indented)
    key:{}                          empty object
    key[N]:v1,v2,v3                 array of primitives (inline)
    key[N]{f1,f2}:                  array of uniform flat objects (table)
     v1,v2                          one row per object
    key[N]:                         any other array (list)
     - value                        primitive item
     - [M]:a,b                      nested array item
     - f1:v1                        object item: first field on the dash line,
       f2:v2                        remaining fields aligned under it
     - {}                           empty object item

The decoder also accepts a space after ``:``.

The root may be an object (fields at indent 0), an array (``[N]...`` with no
key) or a primitive. Strings are bare unless they would be ambiguous:
empty, padded with whitespace, equal to ``true``/``false``/``null``, starting
like a number or with ``-``, or containing ``, : " \\ [ ] { }`` or a control
character. Then they are JSON-quoted. Keys are bare when they match
``[A-Za-z_][A-Za-z0-9_.]*``. Numbers use JSON number syntax.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any, Literal

from .core import JSONParseError, Lexer, ensure_recursion_headroom

Mode = Literal["compact", "minified"]

INDENT = " "  # one space per level: measured cheapest with cl100k and o200k
DASH_WIDTH = len("- ") // len(INDENT)
_BARE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_NUMBER = re.compile(r"^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?$")
_NEEDS_QUOTES = re.compile(r'[,:"\\\[\]{}\x00-\x1f\x7f]')
_TOKEN_PIECES = re.compile(r" ?[A-Za-z_]+| ?\d+| ?[^\x00-\x7f]+|[^\sA-Za-z\d_\x80-\U0010ffff]+|\s+")
_WORD = re.compile(r"[A-Za-z_]+")
# Characters per token for each piece class, fitted by evals/calibrate_tokens.py
# against tiktoken cl100k_base and o200k_base (mean absolute error ~7%).
_CHARS_PER_TOKEN = {"word": 16, "digits": 2, "punctuation": 4, "space": 16, "unicode": 2}


class CompactDecodeError(ValueError):
    def __init__(self, message: str, line: int):
        super().__init__(f"line {line}: {message}")
        self.line = line


# ------------------------------------------------------------------ helpers


def minify(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def estimate_tokens(text: str) -> int:
    """Approximate BPE token count without shipping a tokenizer.

    Text is split into words (with their leading space), digit runs,
    punctuation runs, non-ASCII runs and whitespace runs; each piece costs
    ``ceil(len / chars_per_token)`` tokens for its class. The divisors were
    fitted by ``evals/calibrate_tokens.py`` against tiktoken's cl100k_base and
    o200k_base on 108 JSON/compact texts: mean absolute error ~7%, worst case
    ~40% on texts under 30 tokens. Good enough to compare formats; exact
    counts depend on the target model's tokenizer.
    """
    total = 0
    for piece in _TOKEN_PIECES.findall(text):
        core = piece.strip(" ")
        if _WORD.fullmatch(core):
            kind = "word"
        elif core.isdigit():
            kind = "digits"
        elif not core.isascii():
            kind = "unicode"
        elif piece.isspace():
            kind = "space"
            core = piece
        else:
            kind = "punctuation"
        total += max(1, math.ceil(len(core) / _CHARS_PER_TOKEN[kind]))
    return total


def _is_primitive(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _encode_key(key: str) -> str:
    return key if _BARE_KEY.match(key) else json.dumps(key, ensure_ascii=False)


def _encode_primitive(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite numbers cannot be encoded")
        return repr(value)
    text = str(value)
    if (
        text == ""
        or text != text.strip()
        or text in ("true", "false", "null")
        or text[0] == "-"
        or text[0].isdigit()
        or _NEEDS_QUOTES.search(text)
    ):
        return json.dumps(text, ensure_ascii=False)
    return text


def _tabular_fields(items: list[Any]) -> list[str] | None:
    """Header fields if ``items`` are non-empty objects sharing one key set
    whose values are all primitives."""
    if not items or not all(isinstance(item, dict) and item for item in items):
        return None
    fields = list(items[0])
    key_set = set(fields)
    for item in items:
        if set(item) != key_set or not all(_is_primitive(v) for v in item.values()):
            return None
    return fields


# ------------------------------------------------------------------ encoder


def encode_compact(value: Any) -> str:
    """Encode ``value`` in the compact format (see module docs)."""
    ensure_recursion_headroom()
    lines: list[str] = []
    if isinstance(value, dict):
        _encode_fields(value, 0, lines)
    elif isinstance(value, list):
        _encode_array(None, value, 0, lines, "")
    else:
        lines.append(_encode_primitive(value))
    return "\n".join(lines)


def _encode_fields(obj: dict[str, Any], depth: int, lines: list[str], first_prefix: str | None = None) -> None:
    for i, (key, value) in enumerate(obj.items()):
        prefix = first_prefix if (i == 0 and first_prefix is not None) else INDENT * depth
        _encode_field(key, value, depth, lines, prefix)


def _encode_field(key: str, value: Any, depth: int, lines: list[str], prefix: str) -> None:
    name = _encode_key(key)
    if isinstance(value, dict):
        if not value:
            lines.append(f"{prefix}{name}:{{}}")
        else:
            lines.append(f"{prefix}{name}:")
            _encode_fields(value, depth + 1, lines)
    elif isinstance(value, list):
        _encode_array(name, value, depth, lines, prefix)
    else:
        lines.append(f"{prefix}{name}:{_encode_primitive(value)}")


def _encode_array(name: str | None, items: list[Any], depth: int, lines: list[str], prefix: str) -> None:
    head = f"{prefix}{name or ''}[{len(items)}]"
    if not items:
        lines.append(head + ":")
        return
    if all(_is_primitive(item) for item in items):
        lines.append(f"{head}:{','.join(_encode_primitive(item) for item in items)}")
        return
    fields = _tabular_fields(items)
    if fields is not None:
        lines.append(f"{head}{{{','.join(_encode_key(f) for f in fields)}}}:")
        row_indent = INDENT * (depth + 1)
        for item in items:
            lines.append(row_indent + ",".join(_encode_primitive(item[f]) for f in fields))
        return
    lines.append(head + ":")
    item_indent = INDENT * (depth + 1)
    for item in items:
        if isinstance(item, dict):
            if not item:
                lines.append(f"{item_indent}- {{}}")
            else:
                # The first field shares the dash line; the rest align under it,
                # DASH_WIDTH indentation units deeper than the dash.
                _encode_fields(item, depth + 1 + DASH_WIDTH, lines, first_prefix=f"{item_indent}- ")
        elif isinstance(item, list):
            _encode_array(None, item, depth + 1, lines, f"{item_indent}- ")
        else:
            lines.append(f"{item_indent}- {_encode_primitive(item)}")


# ------------------------------------------------------------------ decoder


@dataclass(slots=True)
class _Line:
    depth: int
    text: str
    number: int


class _Decoder:
    def __init__(self, text: str):
        self.lines: list[_Line] = []
        for number, raw in enumerate(text.split("\n"), 1):
            if not raw.strip():
                continue
            spaces = len(raw) - len(raw.lstrip(" "))
            self.lines.append(_Line(spaces // len(INDENT), raw[spaces:], number))
        self.i = 0

    # -- scalars

    def _string_token(self, text: str, start: int, number: int) -> tuple[str, int]:
        lexer = Lexer(text[start:])
        try:
            token_type, value = lexer.next_token()
        except JSONParseError as exc:
            raise CompactDecodeError(f"bad quoted string: {exc.message}", number) from None
        # Callers only ask for a string where the text starts with '"', so the
        # lexer either returns a STRING token or raises.
        return value, start + lexer.pos

    def primitive(self, text: str, number: int) -> Any:
        if text.startswith('"'):
            value, end = self._string_token(text, 0, number)
            if end != len(text):
                raise CompactDecodeError("unexpected text after quoted string", number)
            return value
        if text == "null":
            return None
        if text == "true":
            return True
        if text == "false":
            return False
        if _NUMBER.match(text):
            return int(text) if not any(c in text for c in ".eE") else float(text)
        if text == "":
            raise CompactDecodeError("missing value", number)
        return text

    def split_values(self, text: str, number: int) -> list[Any]:
        values: list[Any] = []
        i = 0
        while True:
            if text.startswith('"', i):
                value, end = self._string_token(text, i, number)
                values.append(value)
                i = end
            else:
                end = text.find(",", i)
                end = len(text) if end < 0 else end
                values.append(self.primitive(text[i:end], number))
                i = end
            if i >= len(text):
                return values
            if text[i] != ",":
                raise CompactDecodeError("expected ',' between values", number)
            i += 1

    def key(self, text: str, number: int) -> tuple[str, int] | None:
        """Parse a key at the start of ``text``; None if the line is not a field."""
        if text.startswith('"'):
            try:
                key, end = self._string_token(text, 0, number)
            except CompactDecodeError:
                return None
        else:
            match = re.match(r"[A-Za-z_][A-Za-z0-9_.]*", text)
            if not match:
                return None
            key, end = match.group(), match.end()
        if end < len(text) and text[end] in ":[":
            return key, end
        return None

    # -- structure

    def decode(self) -> Any:
        if not self.lines:
            return {}
        first = self.lines[0]
        if first.depth != 0:
            raise CompactDecodeError("the first line must not be indented", first.number)
        if first.text.startswith("["):
            self.i = 1
            value = self.array_after_header(first.text, 0, first.number)
        elif self.key(first.text, first.number) is not None:
            value = self.fields(0)
        else:
            if len(self.lines) != 1:
                raise CompactDecodeError("a primitive root must be the only line", self.lines[1].number)
            self.i = 1
            value = self.primitive(first.text, first.number)
        if self.i != len(self.lines):
            line = self.lines[self.i]
            raise CompactDecodeError("unexpected line", line.number)
        return value

    def fields(self, depth: int, first: tuple[str, int] | None = None) -> dict[str, Any]:
        """Read fields at ``depth``. ``first`` is an inline first field (text, line)."""
        obj: dict[str, Any] = {}
        if first is not None:
            key, value = self.field(first[0], depth, first[1])
            obj[key] = value
        while self.i < len(self.lines) and self.lines[self.i].depth == depth:
            line = self.lines[self.i]
            if line.text.startswith("- ") or line.text == "-":
                break
            self.i += 1
            key, value = self.field(line.text, depth, line.number)
            if key in obj:
                raise CompactDecodeError(f"duplicate key {key!r}", line.number)
            obj[key] = value
        return obj

    def field(self, text: str, depth: int, number: int) -> tuple[str, Any]:
        parsed = self.key(text, number)
        if parsed is None:
            raise CompactDecodeError("expected 'key: value'", number)
        key, end = parsed
        rest = text[end:]
        if rest.startswith("["):
            return key, self.array_after_header(rest, depth, number)
        rest = rest[1:]  # drop ':'
        if rest == "":
            if self.i < len(self.lines) and self.lines[self.i].depth > depth:
                return key, self.fields(depth + 1)
            raise CompactDecodeError(f"field {key!r} has no value", number)
        if rest.startswith(" "):  # tolerated: "key: value"
            rest = rest[1:]
        if rest == "{}":
            return key, {}
        return key, self.primitive(rest, number)

    def array_after_header(self, header: str, depth: int, number: int) -> list[Any]:
        match = re.match(r"\[(\d+)\](\{(.*)\})?:(.*)$", header)
        if not match:
            raise CompactDecodeError("malformed array header", number)
        count = int(match.group(1))
        inline = match.group(4)
        if match.group(2) is not None:
            fields = self.split_values(match.group(3), number) if match.group(3) else []
            if not all(isinstance(f, str) for f in fields):
                raise CompactDecodeError("table header fields must be strings", number)
            rows: list[Any] = []
            for _ in range(count):
                if self.i >= len(self.lines) or self.lines[self.i].depth != depth + 1:
                    raise CompactDecodeError(f"expected {count} table rows", number)
                row = self.lines[self.i]
                self.i += 1
                values = self.split_values(row.text, row.number)
                if len(values) != len(fields):
                    raise CompactDecodeError(f"row has {len(values)} values, header has {len(fields)}", row.number)
                rows.append(dict(zip(fields, values)))
            return rows
        if inline.strip():
            values = self.split_values(inline[1:] if inline.startswith(" ") else inline, number)
            if len(values) != count:
                raise CompactDecodeError(f"declared {count} items, found {len(values)}", number)
            return values
        items: list[Any] = []
        while self.i < len(self.lines) and self.lines[self.i].depth == depth + 1:
            line = self.lines[self.i]
            if line.text == "-":
                raise CompactDecodeError("empty list item", line.number)
            if not line.text.startswith("- "):
                break
            self.i += 1
            items.append(self.list_item(line.text[2:], depth + 1, line.number))
        if len(items) != count:
            raise CompactDecodeError(f"declared {count} items, found {len(items)}", number)
        return items

    def list_item(self, text: str, depth: int, number: int) -> Any:
        if text == "{}":
            return {}
        if text.startswith("["):
            return self.array_after_header(text, depth, number)
        if self.key(text, number) is not None:
            return self.fields(depth + DASH_WIDTH, first=(text, number))
        return self.primitive(text, number)


def decode_compact(text: str) -> Any:
    """Decode the compact format back into Python values."""
    ensure_recursion_headroom()
    return _Decoder(text).decode()


# ------------------------------------------------------------------- public


@dataclass(slots=True)
class CompressionResult:
    mode: Mode
    output: str
    original_chars: int
    output_chars: int
    original_tokens: int
    output_tokens: int

    @property
    def savings(self) -> float:
        if not self.original_tokens:
            return 0.0
        return round(100 * (1 - self.output_tokens / self.original_tokens), 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "output": self.output,
            "stats": {
                "original_chars": self.original_chars,
                "output_chars": self.output_chars,
                "original_tokens_est": self.original_tokens,
                "output_tokens_est": self.output_tokens,
                "token_savings_pct": self.savings,
                "char_savings_pct": round(100 * (1 - self.output_chars / self.original_chars), 1)
                if self.original_chars else 0.0,
            },
        }


def compress_json(value: Any, mode: Mode = "compact", original: str | None = None) -> CompressionResult:
    """Compress ``value``. Savings are measured against ``original`` (the
    source text) or, if absent, against 2-space pretty-printed JSON."""
    baseline = original if original is not None else json.dumps(value, indent=2, ensure_ascii=False)
    if mode == "minified":
        output = minify(value)
    elif mode == "compact":
        output = encode_compact(value)
    else:
        raise ValueError(f"Unknown mode {mode!r}; use 'compact' or 'minified'")
    return CompressionResult(
        mode, output, len(baseline), len(output), estimate_tokens(baseline), estimate_tokens(output)
    )
