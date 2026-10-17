"""Hand-written JSON lexer and recursive-descent parser.

This is the project's core engine. It accepts exactly RFC 8259 JSON and raises
:class:`JSONParseError` (with line, column and source index) on anything else.

On top of plain parsing, the engine can optionally record metadata for the
"Visual Compiler" views. Every option is off by default, and when off the hot
path does the same work as a plain parser plus a few boolean checks.

* ``Lexer(capture_tokens=True)`` keeps an ordered list of every token it
  emits: type, value, raw source text, start/end index, line and column.
* ``Parser(capture_ast=True)`` builds an abstract syntax tree in which every
  object, property, key, array and scalar node carries its source position,
  its JSONPath, its parent id and its depth.
* ``Parser(trace=True)`` logs each method call/return, token match and token
  pointer advance into a serializable list the frontend replays step by step.
* ``Parser(duplicate_keys="record")`` records duplicate keys (last value wins,
  like ``JSON.parse``) instead of rejecting the document. The auditor uses it.
* ``Parser(observer=fn)`` calls ``fn`` with a :class:`ScalarEvent` for every
  key and scalar, including the raw number text the auditor needs for
  precision checks.
"""

from __future__ import annotations

import math
import re
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any, Literal, TypedDict

from .paths import ROOT, format_path, path_child

MAX_RECURSION_DEPTH = 1000
MIN_NUMBER = -9223372036854775808  # 64-bit min
MAX_NUMBER = 9223372036854775807  # 64-bit max
DEFAULT_TRACE_LIMIT = 20_000

_DIGITS = frozenset("0123456789")
_HEX = frozenset("0123456789abcdefABCDEF")
_WHITESPACE = re.compile(r"[ \t\n\r]*")
# A run of string characters that needs no special handling: no quote, no
# backslash, no control character. Strings cannot contain raw newlines, so
# the column advances by exactly the run length.
_STRING_RUN = re.compile(r'[^"\\\x00-\x1f]+')
_SIMPLE_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


def ensure_recursion_headroom(max_depth: int = MAX_RECURSION_DEPTH) -> None:
    """Raise Python's recursion limit so ``max_depth`` levels can be walked.

    Each nesting level costs the recursive-descent parser two Python frames
    (``parse_value`` -> ``parse_array``), plus a leaf call for tracing or the
    observer. The limit only ever goes up, so concurrent callers cannot lower
    it under each other. Python 3.11+ does not use the C stack for
    Python-to-Python calls, so a few thousand frames are safe.
    """
    needed = max_depth * 3 + 300
    if sys.getrecursionlimit() < needed:
        sys.setrecursionlimit(needed)


class TokenType:
    """Token type enumeration for the JSON lexer."""

    LBRACE = "LBRACE"  # {
    RBRACE = "RBRACE"  # }
    LBRACKET = "LBRACKET"  # [
    RBRACKET = "RBRACKET"  # ]
    COLON = "COLON"  # :
    COMMA = "COMMA"  # ,
    STRING = "STRING"  # "..."
    NUMBER = "NUMBER"  # 123, 1.5, 1e5
    TRUE = "TRUE"  # true
    FALSE = "FALSE"  # false
    NULL = "NULL"  # null
    EOF = "EOF"  # end of input


_PUNCTUATION = {
    "{": TokenType.LBRACE,
    "}": TokenType.RBRACE,
    "[": TokenType.LBRACKET,
    "]": TokenType.RBRACKET,
    ":": TokenType.COLON,
    ",": TokenType.COMMA,
}
_KEYWORDS = (("true", TokenType.TRUE, True), ("false", TokenType.FALSE, False), ("null", TokenType.NULL, None))


class JSONParseError(Exception):
    """A syntax error with the 1-based line/column and 0-based source index."""

    def __init__(self, message: str, line: int = 0, column: int = 0, index: int | None = None):
        self.message = message
        self.line = line
        self.column = column
        self.index = index
        super().__init__(f"JSONParseError at line {line}, column {column}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.message, "line": self.line, "column": self.column, "index": self.index}


class TokenRecord(TypedDict):
    index: int
    type: str
    value: Any
    raw: str
    start_index: int
    end_index: int
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class DuplicateKey:
    """A key that appeared more than once in the same object."""

    key: str
    path: str
    line: int
    column: int
    index: int
    first_line: int
    first_column: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ScalarEvent:
    """One key or scalar value, as seen by an observer."""

    kind: Literal["key", "string", "number", "boolean", "null"]
    value: Any
    raw: str
    path: str
    line: int
    column: int
    index: int


class Lexer:
    """Tokenizes JSON input with strict escape and number validation."""

    def __init__(self, input_string: str, capture_tokens: bool = False, capture_limit: int | None = None):
        self.input = input_string
        self.length = len(input_string)
        self.pos = 0
        self.line = 1
        self.column = 1
        self.capture_tokens = capture_tokens
        self.capture_limit = capture_limit
        self.tokens: list[TokenRecord] = []
        self.tokens_truncated = False
        self.token_count = 0
        # Position of the most recently emitted token.
        self.token_start = 0
        self.token_line = 1
        self.token_column = 1

    def _advance(self) -> None:
        """Move forward one character inside a token. Tokens never contain raw
        line breaks (whitespace between tokens is skipped by
        ``_skip_whitespace``), so only the column moves."""
        self.pos += 1
        self.column += 1

    def _peek(self, offset: int = 0) -> str | None:
        """Peek at a character without consuming it."""
        pos = self.pos + offset
        if pos < self.length:
            return self.input[pos]
        return None

    def _skip_whitespace(self) -> None:
        """Skip JSON whitespace (space, tab, line feed, carriage return)."""
        match = _WHITESPACE.match(self.input, self.pos)
        if match is None or match.end() == self.pos:
            return
        chunk = match.group()
        newlines = chunk.count("\n")
        if newlines:
            self.line += newlines
            self.column = len(chunk) - chunk.rfind("\n")
        else:
            self.column += len(chunk)
        self.pos = match.end()

    def _error(self, message: str) -> JSONParseError:
        return JSONParseError(message, self.line, self.column, self.pos)

    def _read_hex4(self) -> int:
        """Read the four hex digits of a ``\\uXXXX`` escape (pos is on the first digit)."""
        digits = self.input[self.pos : self.pos + 4]
        if len(digits) < 4:
            raise self._error("Invalid unicode escape")
        if not all(ch in _HEX for ch in digits):
            raise self._error("Invalid unicode escape sequence")
        self.pos += 4
        self.column += 4
        return int(digits, 16)

    def _parse_string(self) -> str:
        """Parse a JSON string; pos is on the opening quote."""
        result: list[str] = []
        self._advance()  # opening quote
        text = self.input
        while self.pos < self.length:
            run = _STRING_RUN.match(text, self.pos)
            if run:
                chunk = run.group()
                result.append(chunk)
                self.pos = run.end()
                self.column += len(chunk)
                continue
            char = text[self.pos]
            if char == '"':
                self._advance()
                return "".join(result)
            if char == "\\":
                self._advance()
                if self.pos >= self.length:
                    raise self._error("Unterminated string escape")
                escape_char = text[self.pos]
                if escape_char in _SIMPLE_ESCAPES:
                    result.append(_SIMPLE_ESCAPES[escape_char])
                    self._advance()
                elif escape_char == "u":
                    self._advance()
                    code = self._read_hex4()
                    # Combine a UTF-16 surrogate pair into one code point. A lone
                    # surrogate is still valid JSON grammar; the auditor flags it.
                    if 0xD800 <= code <= 0xDBFF and text.startswith("\\u", self.pos):
                        low_digits = text[self.pos + 2 : self.pos + 6]
                        if len(low_digits) == 4 and all(ch in _HEX for ch in low_digits):
                            low = int(low_digits, 16)
                            if 0xDC00 <= low <= 0xDFFF:
                                self.pos += 6
                                self.column += 6
                                code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                    result.append(chr(code))
                else:
                    raise self._error(f"Invalid escape sequence: \\{escape_char}")
                continue
            # Only control characters are left.
            raise self._error("Unescaped control character in string")
        raise self._error("Unterminated string")

    def _parse_number(self) -> str:
        """Parse a JSON number and return its raw source text."""
        start_pos = self.pos
        if self._peek() == "-":
            self._advance()
        if self._peek() == "0":
            self._advance()
            if self._peek() in _DIGITS:
                raise self._error("Leading zeros not allowed in numbers")
        elif self._peek() in _DIGITS:
            while self._peek() in _DIGITS:
                self._advance()
        else:
            raise self._error("Invalid number format")
        if self._peek() == ".":
            self._advance()
            if self._peek() not in _DIGITS:
                raise self._error("Invalid number: digit expected after decimal point")
            while self._peek() in _DIGITS:
                self._advance()
        if self._peek() in ("e", "E"):
            self._advance()
            if self._peek() in ("+", "-"):
                self._advance()
            if self._peek() not in _DIGITS:
                raise self._error("Invalid number: digit expected in exponent")
            while self._peek() in _DIGITS:
                self._advance()
        return self.input[start_pos : self.pos]

    def _emit(self, token_type: str, value: Any, start: int, line: int, column: int) -> tuple[str, Any]:
        self.token_start = start
        self.token_line = line
        self.token_column = column
        self.token_count += 1
        if self.capture_tokens:
            if self.capture_limit is not None and len(self.tokens) >= self.capture_limit:
                self.tokens_truncated = True
            else:
                self.tokens.append(
                    {
                        "index": self.token_count - 1,
                        "type": token_type,
                        "value": value,
                        "raw": self.input[start : self.pos],
                        "start_index": start,
                        "end_index": self.pos,
                        "line": line,
                        "column": column,
                    }
                )
        return token_type, value

    def next_token(self) -> tuple[str, Any]:
        """Return the next ``(type, value)`` token. NUMBER values are raw text."""
        self._skip_whitespace()
        start, line, column = self.pos, self.line, self.column
        if self.pos >= self.length:
            return self._emit(TokenType.EOF, None, start, line, column)

        char = self.input[self.pos]
        if char in _PUNCTUATION:
            self._advance()
            return self._emit(_PUNCTUATION[char], char, start, line, column)
        if char == '"':
            value = self._parse_string()
            return self._emit(TokenType.STRING, value, start, line, column)
        if char == "-" or char in _DIGITS:
            raw = self._parse_number()
            return self._emit(TokenType.NUMBER, raw, start, line, column)
        for word, token_type, value in _KEYWORDS:
            if self.input.startswith(word, self.pos):
                self.pos += len(word)
                self.column += len(word)
                return self._emit(token_type, value, start, line, column)
        raise JSONParseError(f"Unexpected character '{char}'", self.line, self.column, self.pos)

    def tokenize(self) -> list[TokenRecord]:
        """Lex the whole input and return every token record, EOF included."""
        self.capture_tokens = True
        while self.next_token()[0] != TokenType.EOF:
            pass
        return self.tokens


def _preview(value: Any, limit: int = 60) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[: limit - 1] + "…"
    return value


class Parser:
    """Recursive-descent JSON parser with optional instrumentation."""

    def __init__(
        self,
        lexer: Lexer,
        max_depth: int = MAX_RECURSION_DEPTH,
        capture_ast: bool = False,
        trace: bool = False,
        trace_limit: int = DEFAULT_TRACE_LIMIT,
        duplicate_keys: Literal["error", "record"] = "error",
        observer: Callable[[ScalarEvent], None] | None = None,
        max_ast_nodes: int | None = None,
    ):
        self.lexer = lexer
        self.max_depth = max_depth
        self.current_depth = 0
        self.max_depth_seen = 0
        # Position (line, column, index) and path of the container that set max_depth_seen.
        self.deepest: tuple[int, int, int] = (1, 1, 0)
        self.deepest_path = ROOT
        self.capture_ast = capture_ast
        self.max_ast_nodes = max_ast_nodes
        self.ast: dict[str, Any] | None = None
        self.ast_truncated = False
        self.node_count = 0
        self._stack: list[dict[str, Any]] = []
        self.trace = trace
        self.trace_limit = trace_limit
        self.trace_truncated = False
        self.execution_trace: list[dict[str, Any]] = []
        self._call_depth = 0
        self.duplicate_keys = duplicate_keys
        self.duplicates: list[DuplicateKey] = []
        self.observer = observer
        self._track_paths = observer is not None or duplicate_keys == "record"
        self._segments: list[str | int] = []
        self._last_end = 0
        self.current_token: tuple[str, Any] = (TokenType.EOF, None)
        self._sync_token(lexer.next_token())

    # ------------------------------------------------------------------ state

    def _sync_token(self, token: tuple[str, Any]) -> None:
        lexer = self.lexer
        self.current_token = token
        self.tok_index = lexer.token_count - 1
        self.tok_start = lexer.token_start
        self.tok_end = lexer.pos
        self.tok_line = lexer.token_line
        self.tok_column = lexer.token_column

    def _advance(self) -> None:
        """Move the token pointer to the next token."""
        self._last_end = self.tok_end
        self._sync_token(self.lexer.next_token())
        if self.trace:
            self._event("advance", "next_token()", f"pointer -> token #{self.tok_index} {self.current_token[0]}")

    def match_token(self, expected: str, message: str) -> None:
        """Consume the current token if it has type ``expected``, else raise ``message``."""
        if self.current_token[0] != expected:
            raise self._error(message)
        if self.trace:
            self._event("match", f"match_token({expected})", f"matched {self.current_token[1]!r}")
        self._advance()

    def _error(self, message: str) -> JSONParseError:
        """An error positioned at the start of the current token."""
        return JSONParseError(message, self.tok_line, self.tok_column, self.tok_start)

    def _check_depth(self) -> None:
        """Ensure we don't exceed the maximum nesting depth."""
        if self.current_depth >= self.max_depth:
            raise JSONParseError(
                f"Maximum nesting depth ({self.max_depth}) exceeded", self.tok_line, self.tok_column, self.tok_start
            )

    def _enter(self) -> None:
        self._check_depth()
        self.current_depth += 1
        if self.current_depth > self.max_depth_seen:
            self.max_depth_seen = self.current_depth
            self.deepest = (self.tok_line, self.tok_column, self.tok_start)
            if self._track_paths:
                self.deepest_path = self.current_path()

    # ------------------------------------------------------------------ trace

    def _event(self, action: str, method: str, detail: str = "") -> None:
        if len(self.execution_trace) >= self.trace_limit:
            self.trace_truncated = True
            return
        token_type, token_value = self.current_token
        self.execution_trace.append(
            {
                "step": len(self.execution_trace),
                "action": action,
                "method": method,
                "detail": detail,
                "call_depth": self._call_depth,
                "depth": self.current_depth,
                "token_index": self.tok_index,
                "token_type": token_type,
                "token_value": _preview(token_value),
                "start_index": self.tok_start,
                "end_index": self.tok_end,
                "line": self.tok_line,
                "column": self.tok_column,
            }
        )

    def _call(self, method: str) -> None:
        self._event("call", method)
        self._call_depth += 1

    def _return(self, method: str, detail: str) -> None:
        self._call_depth -= 1
        self._event("return", method, detail)

    # -------------------------------------------------------------------- AST

    def _value_path(self) -> str:
        if not self._stack:
            return ROOT
        parent = self._stack[-1]
        if parent["kind"] == "array":
            return path_child(parent["path"], len(parent["children"]))
        return parent["path"]  # the value of a property shares its path

    def _node(self, kind: str, path: str, **fields: Any) -> dict[str, Any]:
        """Create an AST node at the current token and attach it to its parent."""
        if self.max_ast_nodes is not None and self.node_count >= self.max_ast_nodes:
            self.capture_ast = False
            self.ast = None
            self.ast_truncated = True
            self._stack.clear()
            return {}
        parent = self._stack[-1] if self._stack else None
        node: dict[str, Any] = {
            "id": self.node_count,
            "kind": kind,
            "path": path,
            "parent": parent["id"] if parent else None,
            "depth": self.current_depth,
            "line": self.tok_line,
            "column": self.tok_column,
            "start": self.tok_start,
            "end": self.tok_end,
            **fields,
        }
        if parent is None:
            self.ast = node
        else:
            if parent["kind"] == "array":
                node["index"] = len(parent["children"])
            parent["children"].append(node)
        self.node_count += 1
        return node

    def _open(self, kind: str, path: str, **fields: Any) -> None:
        node = self._node(kind, path, children=[], **fields)
        if self.capture_ast:
            self._stack.append(node)

    def _close(self) -> None:
        if self.capture_ast:
            self._stack.pop()["end"] = self._last_end

    # ------------------------------------------------------------- paths/obs

    def current_path(self) -> str:
        """JSONPath of the value being parsed (needs path tracking)."""
        return format_path(self._segments)

    def _observe(self, kind: Any, value: Any, raw: str) -> None:
        assert self.observer is not None
        self.observer(
            ScalarEvent(kind, value, raw, self.current_path(), self.tok_line, self.tok_column, self.tok_start)
        )

    # ---------------------------------------------------------------- grammar

    def parse(self) -> Any:
        """Parse the whole input and return the evaluated Python value."""
        ensure_recursion_headroom(self.max_depth)
        try:
            if self.trace:
                self._call("parse()")
            result = self.parse_value()
            if self.current_token[0] != TokenType.EOF:
                raise self._error("Unexpected token after JSON value")
            if self.trace:
                self._return("parse()", "reached EOF")
            return result
        except JSONParseError as exc:
            if self.trace:
                self._event("error", "parse()", exc.message)
            raise

    def parse_value(self) -> Any:
        """Parse any JSON value."""
        token_type = self.current_token[0]
        if token_type == TokenType.LBRACE:
            return self.parse_object()
        if token_type == TokenType.LBRACKET:
            return self.parse_array()
        if token_type == TokenType.STRING:
            return self.parse_string()
        if token_type == TokenType.NUMBER:
            return self.parse_number()
        if token_type in (TokenType.TRUE, TokenType.FALSE, TokenType.NULL):
            return self.parse_literal()
        raise self._error(f"Expected JSON value, got {token_type}")

    def parse_object(self) -> dict[str, Any]:
        """Parse a JSON object."""
        if self.trace:
            self._call("parse_object()")
        if self.capture_ast:
            self._open("object", self._value_path())
        self._enter()
        obj: dict[str, Any] = {}
        seen: dict[str, tuple[int, int]] = {}
        self.match_token(TokenType.LBRACE, "Expected '{'")

        if self.current_token[0] == TokenType.RBRACE:
            self.match_token(TokenType.RBRACE, "Expected '}'")
        else:
            while True:
                if self.current_token[0] != TokenType.STRING:
                    raise self._error("Expected string key in object")
                key = self.current_token[1]
                key_line, key_column, key_start = self.tok_line, self.tok_column, self.tok_start
                duplicate = key in obj
                if duplicate and self.duplicate_keys == "error":
                    raise JSONParseError(f"Duplicate key '{key}' in object", key_line, key_column, key_start)
                if self._track_paths:
                    self._segments.append(key)
                    if duplicate:
                        first_line, first_column = seen[key]
                        self.duplicates.append(
                            DuplicateKey(key, self.current_path(), key_line, key_column, key_start, first_line, first_column)
                        )
                    else:
                        seen[key] = (key_line, key_column)
                    if self.observer:
                        self._observe("key", key, self.lexer.input[self.tok_start : self.tok_end])
                if self.capture_ast:
                    self._open("property", path_child(self._stack[-1]["path"], key), key=key, duplicate=duplicate)
                if self.capture_ast:  # re-checked: _open stops capture when the node budget runs out
                    raw_key = self.lexer.input[self.tok_start : self.tok_end]
                    self._node("key", self._stack[-1]["path"], value=key, raw=raw_key)
                if self.trace:
                    self._event("match", "match_token(STRING)", f"key {_preview(key)!r}")
                self._advance()
                self.match_token(TokenType.COLON, "Expected ':' after object key")
                obj[key] = self.parse_value()
                self._close()  # property
                if self._track_paths:
                    self._segments.pop()

                if self.current_token[0] == TokenType.COMMA:
                    self.match_token(TokenType.COMMA, "Expected ','")
                    if self.current_token[0] == TokenType.RBRACE:
                        raise self._error("Trailing comma in object")
                elif self.current_token[0] == TokenType.RBRACE:
                    self.match_token(TokenType.RBRACE, "Expected '}'")
                    break
                else:
                    raise self._error("Expected ',' or '}' in object")

        self._close()  # object
        self.current_depth -= 1
        if self.trace:
            self._return("parse_object()", f"object with {len(obj)} key(s)")
        return obj

    def parse_array(self) -> list[Any]:
        """Parse a JSON array."""
        if self.trace:
            self._call("parse_array()")
        if self.capture_ast:
            self._open("array", self._value_path())
        self._enter()
        arr: list[Any] = []
        self.match_token(TokenType.LBRACKET, "Expected '['")

        if self.current_token[0] == TokenType.RBRACKET:
            self.match_token(TokenType.RBRACKET, "Expected ']'")
        else:
            while True:
                if self._track_paths:
                    self._segments.append(len(arr))
                arr.append(self.parse_value())
                if self._track_paths:
                    self._segments.pop()
                if self.current_token[0] == TokenType.COMMA:
                    self.match_token(TokenType.COMMA, "Expected ','")
                    if self.current_token[0] == TokenType.RBRACKET:
                        raise self._error("Trailing comma in array")
                elif self.current_token[0] == TokenType.RBRACKET:
                    self.match_token(TokenType.RBRACKET, "Expected ']'")
                    break
                else:
                    raise self._error("Expected ',' or ']' in array")

        self._close()
        self.current_depth -= 1
        if self.trace:
            self._return("parse_array()", f"array with {len(arr)} item(s)")
        return arr

    def parse_string(self) -> str:
        """Parse a string value."""
        if self.current_token[0] != TokenType.STRING:
            raise self._error("Expected string")
        value = self.current_token[1]
        if self.trace:
            self._call("parse_string()")
        if self.capture_ast:
            self._node("string", self._value_path(), value=value, raw=self.lexer.input[self.tok_start : self.tok_end])
        if self.observer:
            self._observe("string", value, self.lexer.input[self.tok_start : self.tok_end])
        self._advance()
        if self.trace:
            self._return("parse_string()", f"string {_preview(value)!r}")
        return value

    def parse_number(self) -> float | int:
        """Parse a number value. Integers stay exact; others become floats."""
        if self.current_token[0] != TokenType.NUMBER:
            raise self._error("Expected number")
        num_str = self.current_token[1]
        if self.trace:
            self._call("parse_number()")
        # The lexer already enforced JSON number grammar, so these cannot fail.
        value: int | float = int(num_str) if "." not in num_str and "e" not in num_str.lower() else float(num_str)
        if isinstance(value, float) and not math.isfinite(value):
            raise self._error(f"Number out of range: {num_str}")
        if isinstance(value, int) and (value < MIN_NUMBER or value > MAX_NUMBER):
            raise self._error(f"Number out of range: {num_str}")
        if self.capture_ast:
            self._node("number", self._value_path(), value=value, raw=num_str)
        if self.observer:
            self._observe("number", value, num_str)
        self._advance()
        if self.trace:
            self._return("parse_number()", f"number {num_str}")
        return value

    def parse_literal(self) -> bool | None:
        """Parse ``true``, ``false`` or ``null``."""
        token_type, value = self.current_token
        if token_type not in (TokenType.TRUE, TokenType.FALSE, TokenType.NULL):
            raise self._error("Expected literal")
        raw = self.lexer.input[self.tok_start : self.tok_end]
        if self.trace:
            self._call("parse_literal()")
        kind = "null" if value is None else "boolean"
        if self.capture_ast:
            self._node(kind, self._value_path(), value=value, raw=raw)
        if self.observer:
            self._observe(kind, value, raw)
        self._advance()
        if self.trace:
            self._return("parse_literal()", raw)
        return value

    # Original method names, kept for callers written against them.
    value = parse_value
    object = parse_object
    array = parse_array
    string = parse_string
    number = parse_number


def parse(text: str, max_depth: int = MAX_RECURSION_DEPTH) -> Any:
    """Parse ``text`` strictly and return the Python value."""
    return Parser(Lexer(text), max_depth=max_depth).parse()
