"""Fuzzy Healing Gateway: repair damaged or non-standard JSON.

Pipeline
--------
1. **Strict first.** :func:`heal` runs the strict core parser. Valid input is
   returned untouched with no repairs.
2. **Lenient lexing.** A fault-tolerant lexer reads the text and treats
   comments (``//``, ``#``, ``/* */``), Markdown code fences and a BOM as
   removable trivia. It reads single-quoted and smart-quoted strings, bare
   words, and JSON5-style numbers. Every token keeps its source span.
3. **Structural repair.** A recursive-descent repair parser walks the tokens
   and emits output *pieces* (each token's original leading whitespace plus
   its, possibly rewritten, text). It inserts missing commas, colons, values
   and closers, drops trailing or extra commas and stray tokens, quotes keys,
   and removes the earlier copy of a duplicate key.
4. **Verification.** The healed text is parsed again by the strict parser.
   :class:`HealResult` says whether the repair produced valid JSON.

The output keeps the author's layout: only repaired tokens change, so the
diff between input and output is exactly the audit trail.

Every change is recorded as a :class:`Repair` (kind, message, position,
before/after text). ``heal_json(text)`` returns the
``(healed_json_string, list_of_repairs_performed)`` tuple.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from .core import MAX_RECURSION_DEPTH, JSONParseError, Lexer, Parser, ensure_recursion_headroom

# Repair kinds, in the vocabulary the UI and tests use.
COMMENT = "comment-removed"
CODE_FENCE = "code-fence-removed"
BOM = "bom-removed"
STRAY_CHARACTER = "stray-character"
TRAILING_COMMA = "trailing-comma"
EXTRA_COMMA = "extra-comma"
MISSING_COMMA = "missing-comma"
MISSING_COLON = "missing-colon"
MISSING_VALUE = "missing-value"
UNQUOTED_KEY = "unquoted-key"
SINGLE_QUOTED_KEY = "single-quoted-key"
SINGLE_QUOTED_STRING = "single-quoted-string"
SMART_QUOTES = "smart-quotes"
UNQUOTED_STRING = "unquoted-string"
LITERAL_CASE = "literal-case"
NON_JSON_LITERAL = "non-json-literal"
TRUNCATED_LITERAL = "truncated-literal"
NUMBER_FORMAT = "number-format"
INVALID_ESCAPE = "invalid-escape"
CONTROL_CHARACTER = "control-character"
UNTERMINATED_STRING = "unterminated-string"
UNCLOSED_CONTAINER = "unclosed-container"
MISMATCHED_BRACKET = "mismatched-bracket"
STRAY_TOKEN = "stray-token"
DUPLICATE_KEY = "duplicate-key"
MULTIPLE_ROOTS = "multiple-roots"
SURROUNDING_TEXT = "surrounding-text"


@dataclass(frozen=True, slots=True)
class Repair:
    """One modification made while healing."""

    kind: str
    message: str
    line: int
    column: int
    index: int
    before: str
    after: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class HealResult:
    """Outcome of :func:`heal`."""

    original: str
    healed: str
    repairs: list[Repair]
    valid: bool
    strict: bool  # True when the input was already valid JSON
    value: Any = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "healed": self.healed,
            "repairs": [repair.to_dict() for repair in self.repairs],
            "valid": self.valid,
            "strict": self.strict,
            "changed": self.healed != self.original,
            "error": self.error,
        }


class HealingError(Exception):
    """The input cannot be healed (for example, it contains no value at all)."""


# ---------------------------------------------------------------- utilities


class _Positions:
    """Map source indexes to 1-based line/column."""

    def __init__(self, text: str):
        self.starts = [0] + [match.end() for match in re.finditer("\n", text)]

    def at(self, index: int) -> tuple[int, int]:
        line = bisect.bisect_right(self.starts, index) - 1
        return line + 1, index - self.starts[line] + 1


def _clip(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


_CONTROL_ESCAPES = {"\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}
_VALID_ESCAPES = frozenset('"\\/bfnrt')
_HEX = frozenset("0123456789abcdefABCDEF")
_NUMBER = re.compile(r"[+-]?(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)")
_WORD = re.compile(r"[^\W\d]|\$")  # first character of a bare word
_WORD_REST = re.compile(r"[\w$.\-]*")

# Opening quote -> characters that close it. Typographic double quotes also
# close on a straight quote, since mixed quotes are typical word-processor damage.
_CLOSERS = {'"': '"', "'": "'", "“": "”“\"", "‘": "’"}

_LITERAL_FIXES = {
    "True": "true", "TRUE": "true", "False": "false", "FALSE": "false",
    "None": "null", "NULL": "null", "Null": "null", "nil": "null",
}
_NON_JSON_LITERALS = {"NaN", "Infinity", "-Infinity", "+Infinity", "undefined"}


@dataclass(slots=True)
class _Token:
    kind: str  # one of { } [ ] : , STRING NUMBER WORD EOF
    text: str  # output text (already normalized for STRING/NUMBER)
    raw: str  # original source text
    start: int
    end: int
    leading: str  # whitespace before the token, comments already removed
    notes: list[tuple[str, str]] = field(default_factory=list)  # pending (kind, message) repairs
    single_quoted: bool = False


# ------------------------------------------------------------------- lexing


def _skip_comment(text: str, i: int) -> int | None:
    """If a comment starts at ``i`` return the index just past it, else None."""
    if text.startswith("//", i) or text[i] == "#":
        end = text.find("\n", i)
        return len(text) if end < 0 else end
    if text.startswith("/*", i):
        end = text.find("*/", i + 2)
        return len(text) if end < 0 else end + 2
    return None


def strip_comments(text: str) -> str:
    """Remove ``//``, ``#`` and ``/* */`` comments outside string literals.

    Line breaks inside removed block comments are kept so line numbers in
    later error messages still match the original text.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch in ('"', "'"):
            j = i + 1
            while j < n and text[j] != ch and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(text[i:j])
            i = j
            continue
        end = _skip_comment(text, i)
        if end is not None:
            out.append("\n" * text.count("\n", i, end) if text.startswith("/*", i) else "")
            i = end
            continue
        out.append(ch)
        i += 1
    return "".join(out)




class _LenientLexer:
    """Fault-tolerant tokenizer. Token-level fixes are attached as notes and
    only committed to the audit trail when the repair parser keeps the token."""

    def __init__(self, text: str, positions: _Positions, repairs: list[Repair]):
        self.text = text
        self.n = len(text)
        self.positions = positions
        self.repairs = repairs

    def _record(self, kind: str, message: str, index: int, before: str, after: str) -> None:
        line, column = self.positions.at(index)
        self.repairs.append(Repair(kind, message, line, column, index, _clip(before), _clip(after)))

    def _trivia(self, i: int) -> tuple[str, int]:
        """Collect whitespace from ``i``; remove comments, code fences and BOMs."""
        text, n = self.text, self.n
        parts: list[str] = []
        removed = False
        while i < n:
            ch = text[i]
            if ch in " \t\r\n":
                j = i
                while j < n and text[j] in " \t\r\n":
                    j += 1
                parts.append(text[i:j])
                i = j
            elif ch == "﻿":
                self._record(BOM, "Removed byte order mark", i, "\\ufeff", "")
                i += 1
            elif text.startswith("```", i):
                end = text.find("\n", i)
                end = n if end < 0 else end
                self._record(CODE_FENCE, "Removed Markdown code fence", i, text[i:end], "")
                i, removed = end, True
            elif (end := _skip_comment(text, i)) is not None:
                self._record(COMMENT, "Removed comment", i, text[i:end], "")
                i, removed = end, True
            else:
                break
        trivia = "".join(parts)
        if removed:
            # Drop lines that only held a comment, and spaces left before one.
            lines = trivia.split("\n")
            if len(lines) > 1:
                middle = [line for line in lines[1:-1] if line.strip()]
                trivia = "\n".join([lines[0].rstrip(" \t"), *middle, lines[-1]])
            else:
                trivia = trivia.rstrip(" \t") if i >= n else trivia
        return trivia, i

    def _scan_string(self, start: int, limit: int, closers: str, opener: str) -> tuple[str, int, bool, int, list[tuple[str, str]]]:
        text = self.text
        out: list[str] = []
        notes: list[tuple[str, str]] = []
        j = start + 1
        first_newline = -1
        while j < limit:
            ch = text[j]
            if ch in closers:
                return "".join(out), j + 1, True, first_newline, notes
            if ch == "\\":
                nxt = text[j + 1] if j + 1 < limit else ""
                hex4 = text[j + 2 : j + 6]
                if nxt in _VALID_ESCAPES and nxt:
                    out.append("\\" + nxt)
                    j += 2
                    continue
                if nxt == "u" and len(hex4) == 4 and all(c in _HEX for c in hex4):
                    out.append("\\u" + hex4)
                    j += 6
                    continue
                if nxt == "'":
                    out.append("'")
                    j += 2
                    if opener == '"' and (INVALID_ESCAPE, _APOSTROPHE_NOTE) not in notes:
                        notes.append((INVALID_ESCAPE, _APOSTROPHE_NOTE))
                    continue
                out.append("\\\\")
                j += 1
                if (INVALID_ESCAPE, _BACKSLASH_NOTE) not in notes:
                    notes.append((INVALID_ESCAPE, _BACKSLASH_NOTE))
                continue
            if ch == '"':  # only reachable inside a non-double-quoted string
                out.append('\\"')
            elif ord(ch) < 0x20:
                if ch == "\n" and first_newline < 0:
                    first_newline = j
                out.append(_CONTROL_ESCAPES.get(ch, f"\\u{ord(ch):04x}"))
                if (CONTROL_CHARACTER, _CONTROL_NOTE) not in notes:
                    notes.append((CONTROL_CHARACTER, _CONTROL_NOTE))
            else:
                out.append(ch)
            j += 1
        return "".join(out), limit, False, first_newline, notes

    def _string(self, i: int) -> _Token:
        opener = self.text[i]
        closers = _CLOSERS[opener]
        content, end, closed, first_newline, notes = self._scan_string(i, self.n, closers, opener)
        if closed and first_newline >= 0 and end < self.n and self.text[end] not in _AFTER_STRING:
            # A string with a raw line break whose "closing" quote runs straight into
            # more text (`"abc\n, "b": 1`) almost always lost its real closing quote.
            closed = False
        if not closed:
            if first_newline >= 0:
                # Assume the string ends at its line break rather than swallowing the rest.
                end = first_newline - 1 if self.text[first_newline - 1] == "\r" else first_newline
                content, _, _, _, notes = self._scan_string(i, end, closers, opener)
            notes.append((UNTERMINATED_STRING, "Closed an unterminated string"))
        if opener == "'":
            notes.insert(0, (SINGLE_QUOTED_STRING, "Converted a single-quoted string to double quotes"))
        elif opener != '"':
            notes.insert(0, (SMART_QUOTES, "Replaced typographic quotes with straight double quotes"))
        return _Token("STRING", '"' + content + '"', self.text[i:end], i, end, "", notes, opener == "'")

    @staticmethod
    def _number(raw: str) -> tuple[str, list[tuple[str, str]]]:
        sign = "-" if raw.startswith("-") else ""
        body = raw.lstrip("+-")
        if body[:2].lower() == "0x":
            normalized = sign + str(int(body, 16))
            return normalized, [(NUMBER_FORMAT, f"Converted hexadecimal {raw} to decimal {normalized}")]
        match = re.fullmatch(r"(\d*)(?:\.(\d*))?([eE][+-]?\d+)?", body)
        assert match is not None  # guaranteed by _NUMBER
        integer = match.group(1).lstrip("0") or "0"
        fraction = match.group(2) or ""
        exponent = match.group(3) or ""
        normalized = sign + integer + ("." + fraction if fraction else "") + exponent
        if normalized != raw:
            return normalized, [(NUMBER_FORMAT, f"Normalized number {raw} to {normalized}")]
        return raw, []

    def _one(self, i: int, leading: str) -> _Token | None:
        """Lex one token at ``i``. Returns None (and records a repair) for a stray character."""
        text = self.text
        ch = text[i]
        token: _Token
        if ch in "{}[]:,":
            token = _Token(ch, ch, ch, i, i + 1, leading)
        elif ch in _CLOSERS:
            token = self._string(i)
            token.leading = leading
        elif ch in "+-" and (special := _SIGNED_SPECIAL.match(text, i)):
            token = _Token("WORD", special.group(), special.group(), i, special.end(), leading)
        elif (ch.isdigit() or ch in "+-.") and (number := _NUMBER.match(text, i)):
            rendered, notes = self._number(number.group())
            token = _Token("NUMBER", rendered, number.group(), i, number.end(), leading, notes)
        elif _WORD.match(ch):
            end = _WORD_REST.match(text, i + 1).end()
            token = _Token("WORD", text[i:end], text[i:end], i, end, leading)
        else:
            self._record(STRAY_CHARACTER, f"Removed stray character {ch!r}", i, ch, "")
            return None
        return token

    def tokens(self) -> list[_Token]:
        out: list[_Token] = []
        carried = ""
        i = 0
        while True:
            leading, i = self._trivia(i)
            leading = carried + leading
            carried = ""
            if i >= self.n:
                out.append(_Token("EOF", "", "", self.n, self.n, leading))
                return out
            token = self._one(i, leading)
            if token is None:
                carried = leading
                i += 1
                continue
            out.append(token)
            i = token.end


_AFTER_STRING = frozenset(" \t\r\n,:}]")
_APOSTROPHE_NOTE = "Replaced the invalid escape \\' with a plain apostrophe"
_BACKSLASH_NOTE = "Escaped a backslash that did not start a valid escape sequence"
_CONTROL_NOTE = "Escaped a raw control character inside a string"
_SIGNED_SPECIAL = re.compile(r"[+-](?:Infinity|NaN)\b")
_VALUE_WORDS = frozenset({"true", "false", "null", *_LITERAL_FIXES, *_NON_JSON_LITERALS})


# ------------------------------------------------------------------ repairing


class _Repairer:
    """Recursive-descent repair parser that emits healed output pieces."""

    def __init__(self, tokens: list[_Token], positions: _Positions, repairs: list[Repair], source: str, max_depth: int):
        self.toks = tokens
        self.k = 0
        self.pieces: list[str] = []
        self.positions = positions
        self.repairs = repairs
        self.source = source
        self.max_depth = max_depth
        self.stack: list[str] = []
        self.last_end = 0
        # balance[i]: openers minus closers among tokens[i:], used to tell a
        # mistyped closer from a missing one.
        self.balance = [0] * (len(tokens) + 1)
        for i in range(len(tokens) - 1, -1, -1):
            kind = tokens[i].kind
            self.balance[i] = self.balance[i + 1] + (1 if kind in ("{", "[") else -1 if kind in ("}", "]") else 0)

    @property
    def cur(self) -> _Token:
        return self.toks[self.k]

    def _record(self, kind: str, message: str, index: int, before: str, after: str) -> None:
        line, column = self.positions.at(index)
        self.repairs.append(Repair(kind, message, line, column, index, _clip(before), _clip(after)))

    def _emit(self, token: _Token, text: str | None = None, *, as_key: bool = False, notes: bool = True) -> None:
        self.pieces.append(token.leading)
        self.pieces.append(token.text if text is None else text)
        if notes:
            for kind, message in token.notes:
                if as_key and kind == SINGLE_QUOTED_STRING:
                    kind, message = SINGLE_QUOTED_KEY, "Converted a single-quoted key to double quotes"
                self._record(kind, message, token.start, token.raw, token.text)
        self.last_end = token.end
        self.k += 1

    def _drop(self, token: _Token, kind: str, message: str) -> None:
        self.pieces.append(token.leading)
        self._record(kind, message, token.start, token.raw, "")
        self.k += 1

    def _insert(self, text: str, kind: str, message: str, *, after_space: bool = False) -> None:
        if after_space:
            # Place the text after the whitespace that precedes the next token.
            self.pieces.append(self.cur.leading)
            self.cur.leading = ""
        self.pieces.append(text)
        self._record(kind, message, self.last_end, "", text)

    def _blank(self, start: int) -> None:
        for index in range(start, len(self.pieces)):
            self.pieces[index] = ""

    def _where(self, token: _Token) -> str:
        line, column = self.positions.at(token.start)
        return f"line {line}, column {column}"

    # ---------------------------------------------------------------- values

    def value(self) -> None:
        token = self.cur
        if token.kind == "{":
            self.obj()
        elif token.kind == "[":
            self.arr()
        elif token.kind in ("STRING", "NUMBER"):
            self._emit(token)
        elif token.kind == "WORD":
            self._word(token)
        else:
            self._insert("null", MISSING_VALUE, "Inserted null for a missing value", after_space=True)

    def _word(self, token: _Token) -> None:
        word = token.text
        if word in ("true", "false", "null"):
            self._emit(token)
        elif word in _LITERAL_FIXES:
            fixed = _LITERAL_FIXES[word]
            self._emit(token, fixed)
            self._record(LITERAL_CASE, f"Replaced {word} with {fixed}", token.start, word, fixed)
        elif word in _NON_JSON_LITERALS:
            self._emit(token, "null")
            self._record(NON_JSON_LITERAL, f"Replaced {word} with null (JSON cannot represent it)", token.start, word, "null")
        elif self.toks[self.k + 1].kind == "EOF" and (full := _complete_literal(word)):
            self._emit(token, full)
            self._record(TRUNCATED_LITERAL, f"Completed truncated literal {word} to {full}", token.start, word, full)
        else:
            quoted = '"' + word + '"'
            self._emit(token, quoted)
            self._record(UNQUOTED_STRING, f"Quoted bare word '{word}'", token.start, word, quoted)

    def _key(self, token: _Token) -> str:
        if token.kind == "STRING":
            self._emit(token, as_key=True)
            # The lenient lexer always renders a valid JSON string, so this decodes.
            return str(Lexer(token.text).next_token()[1])
        quoted = '"' + token.raw + '"'
        self._emit(token, quoted, notes=False)
        style = "unquoted key" if token.kind == "WORD" else "numeric key"
        self._record(UNQUOTED_KEY, f"Quoted {style} '{token.raw}'", token.start, token.raw, quoted)
        return token.raw

    def _commas(self, closer: str, has_items: bool) -> tuple[int, int] | None:
        """Handle a run of commas inside a container; return ``(piece index,
        source index)`` of the comma that was kept, if any."""
        j = self.k
        while self.toks[j].kind == ",":
            j += 1
        following = self.toks[j].kind
        trailing = following in ("}", "]", "EOF")
        first = True
        kept: tuple[int, int] | None = None
        while self.cur.kind == ",":
            token = self.cur
            if trailing:
                if has_items:
                    self._drop(token, TRAILING_COMMA, f"Removed trailing comma before '{closer}'")
                else:
                    self._drop(token, EXTRA_COMMA, "Removed comma in an empty container")
            elif not has_items:
                self._drop(token, EXTRA_COMMA, "Removed comma before the first item")
            elif first:
                self._emit(token)
                kept = (len(self.pieces) - 1, token.start)
            else:
                self._drop(token, EXTRA_COMMA, "Removed repeated comma")
            first = False
        return kept

    def _orphaned_comma(self, comma: tuple[int, int] | None, closer: str) -> None:
        """A kept comma whose next item was removed is now a trailing comma."""
        if comma is not None and self.pieces[comma[0]] == ",":
            self.pieces[comma[0]] = ""
            self._record(TRAILING_COMMA, f"Removed trailing comma before '{closer}'", comma[1], ",", "")

    def _remove_text(self, start: int, end: int, message: str) -> None:
        """Record removal of prose; stray characters inside it need no entry of their own."""
        self.repairs[:] = [r for r in self.repairs if not (r.kind == STRAY_CHARACTER and start <= r.index < end)]
        self._record(SURROUNDING_TEXT, message, start, self.source[start:end].rstrip(), "")

    def _closer_is_typo(self) -> bool:
        """At a closer of the wrong kind whose parent is the other container
        kind, decide whether it is a typo for this container's closer (rest of
        the document balances if it closes only this container) rather than
        this container missing its own closer (rest balances if it closes both)."""
        return self.balance[self.k + 1] == -(len(self.stack) - 1)

    def _container_end(self, i: int) -> int:
        """Index just past the container opening at token ``i``."""
        depth = 0
        for j in range(i, len(self.toks)):
            kind = self.toks[j].kind
            depth += 1 if kind in ("{", "[") else -1 if kind in ("}", "]") else 0
            if depth <= 0:
                return j + 1
        return len(self.toks) - 1

    def _enter(self, kind: str) -> None:
        if len(self.stack) >= self.max_depth:
            raise HealingError(f"Maximum nesting depth ({self.max_depth}) exceeded")
        self.stack.append(kind)

    # ------------------------------------------------------------ containers

    def obj(self, implicit: bool = False) -> None:
        opener = self.cur
        self._enter("{")
        if implicit:
            self._insert("{", UNCLOSED_CONTAINER, "Inserted '{' around top-level members")
        else:
            self._emit(opener)
        members: list[int] = []
        seen: dict[str, int] = {}
        expecting = True
        comma: tuple[int, int] | None = None
        while True:
            token = self.cur
            if token.kind in ("}", "]", "EOF") and expecting:
                self._orphaned_comma(comma, "}")
            if token.kind == "}":
                self._emit(token)
                break
            if token.kind == "EOF":
                where = "top-level members" if implicit else f"object opened at {self._where(opener)}"
                self._insert("}", UNCLOSED_CONTAINER, f"Closed {where}")
                break
            if token.kind == "]":
                if len(self.stack) >= 2 and self.stack[-2] == "[" and not self._closer_is_typo():
                    self._insert("}", UNCLOSED_CONTAINER, f"Closed object opened at {self._where(opener)} before ']'")
                else:
                    self._emit(token, "}")
                    self._record(MISMATCHED_BRACKET, "Replaced ']' with '}' to close the object", token.start, "]", "}")
                break
            if token.kind == ",":
                comma = self._commas("}", bool(members))
                expecting = True
                continue
            if token.kind == ":":
                self._drop(token, STRAY_TOKEN, "Removed a stray ':'")
                continue
            if token.kind in ("{", "["):
                start = len(self.pieces)
                self.value()
                self._blank(start)
                self._record(STRAY_TOKEN, "Removed a value that had no key", token.start, token.raw, "")
                continue
            if members and not expecting:
                self._insert(",", MISSING_COMMA, "Inserted missing comma between object members")
            comma = None
            member_start = len(self.pieces)
            key = self._key(token)
            members.append(member_start)
            if key in seen:
                earlier = seen[key]
                start, end = members[earlier], members[earlier + 1]
                # Keep the removed member's leading whitespace in place of the next one's.
                for index in range(start + 1, end + 1):
                    self.pieces[index] = ""
                self._record(
                    DUPLICATE_KEY,
                    f"Removed an earlier duplicate of key {key!r} (kept the last value, like JSON.parse)",
                    token.start, token.raw, token.raw,
                )
            seen[key] = len(members) - 1
            expecting = False
            following = self.cur
            if following.kind == ":":
                self._emit(following)
            elif following.kind in (",", "}", "]", "EOF"):
                self._insert(": null", MISSING_VALUE, f"Inserted ': null' for key {key!r} that had no value")
                continue
            else:
                self._insert(":", MISSING_COLON, f"Inserted missing ':' after key {key!r}")
            self.value()
        self.stack.pop()

    def arr(self) -> None:
        opener = self.cur
        self._enter("[")
        self._emit(opener)
        count = 0
        expecting = True
        comma: tuple[int, int] | None = None
        while True:
            token = self.cur
            if token.kind in ("}", "]", "EOF") and expecting:
                self._orphaned_comma(comma, "]")
            if token.kind == "]":
                self._emit(token)
                break
            if token.kind == "EOF":
                self._insert("]", UNCLOSED_CONTAINER, f"Closed array opened at {self._where(opener)}")
                break
            if token.kind == "}":
                if len(self.stack) >= 2 and self.stack[-2] == "{" and not self._closer_is_typo():
                    self._insert("]", UNCLOSED_CONTAINER, f"Closed array opened at {self._where(opener)} before '}}'")
                else:
                    self._emit(token, "]")
                    self._record(MISMATCHED_BRACKET, "Replaced '}' with ']' to close the array", token.start, "}", "]")
                break
            if token.kind == ",":
                comma = self._commas("]", count > 0)
                expecting = True
                continue
            if token.kind == ":":
                self._drop(token, STRAY_TOKEN, "Removed a stray ':' inside an array")
                continue
            if count and not expecting:
                self._insert(",", MISSING_COMMA, "Inserted missing comma between array items")
            comma = None
            self.value()
            count += 1
            expecting = False
        self.stack.pop()

    # -------------------------------------------------------------- document

    def document(self) -> str:
        toks = self.toks
        first_container = next((i for i, t in enumerate(toks) if t.kind in ("{", "[")), None)
        implicit_object = toks[0].kind in ("STRING", "WORD", "NUMBER") and toks[1].kind == ":"
        if (implicit_object and toks[0].kind == "WORD" and toks[2].kind in ("{", "[")
                and toks[self._container_end(2)].kind != ","):
            # "Output: {...}" is a label in front of the document, not a member.
            implicit_object = False
            self._remove_text(toks[0].start, toks[2].start, "Removed text before the JSON value")
            toks[2].leading = toks[0].leading
            self.k = 2
            first_container = None
        if first_container and not implicit_object:
            prefix = toks[:first_container]
            prose = any(
                a.kind == "WORD" and b.kind == "WORD" and a.text not in _VALUE_WORDS for a, b in zip(prefix, prefix[1:])
            )
            if prose or any(t.kind in ("}", "]", ",") for t in prefix):
                start, end = prefix[0].start, toks[first_container].start
                self._remove_text(start, end, "Removed text before the JSON value")
                toks[first_container].leading = prefix[0].leading
                self.k = first_container

        roots = 0
        root_piece = 0
        while self.cur.kind != "EOF":
            token = self.cur
            if roots == 0:
                if token.kind in ("STRING", "WORD", "NUMBER") and toks[self.k + 1].kind == ":":
                    root_piece = len(self.pieces)
                    self.obj(implicit=True)
                    roots = 1
                elif token.kind in ("{", "[", "STRING", "NUMBER", "WORD"):
                    root_piece = len(self.pieces) + 1
                    self.value()
                    roots = 1
                else:
                    self._drop(token, STRAY_TOKEN, f"Removed stray {token.raw!r} before the JSON value")
                continue
            j = self.k
            while toks[j].kind == ",":
                j += 1
            if toks[j].kind in ("{", "["):
                while self.cur.kind == ",":
                    self.pieces.append(self.cur.leading)
                    self.k += 1
                self.pieces.append(",")
                self.value()
                roots += 1
                continue
            # Trailing text: drop everything up to the next container or EOF.
            start = token.start
            while self.cur.kind not in ("EOF", "{", "["):
                self.k += 1
            self._remove_text(start, self.cur.start, "Removed text after the JSON value")

        if roots == 0:
            raise HealingError("No JSON value found")
        if roots > 1:
            self.pieces.insert(root_piece, "[")
            self.pieces.append("]")
            first = toks[0]
            self._record(MULTIPLE_ROOTS, f"Wrapped {roots} top-level values in an array", first.start, "", "[ … ]")
        self.pieces.append(self.cur.leading)
        return "".join(self.pieces)


def _complete_literal(word: str) -> str | None:
    for literal in ("true", "false", "null"):
        if literal.startswith(word) and word != literal:
            return literal
    return None


# ------------------------------------------------------------------- public


def heal(text: str, max_depth: int = MAX_RECURSION_DEPTH) -> HealResult:
    """Repair ``text`` if the strict parser rejects it. See the module docs."""
    try:
        value = Parser(Lexer(text), max_depth=max_depth).parse()
        return HealResult(text, text, [], True, True, value)
    except JSONParseError as exc:
        strict_error = exc

    ensure_recursion_headroom(max_depth)
    positions = _Positions(text)
    repairs: list[Repair] = []
    try:
        tokens = _LenientLexer(text, positions, repairs).tokens()
        healed = _Repairer(tokens, positions, repairs, text, max_depth).document()
    except HealingError as exc:
        error = strict_error.to_dict() | {"error": f"{exc}; strict parser said: {strict_error.message}"}
        return HealResult(text, text, [], False, False, None, error)

    repairs.sort(key=lambda repair: repair.index)
    # Keep the original document's edge whitespace; drop any left by removed text.
    stripped = text.strip()
    healed = text[: len(text) - len(text.lstrip())] + healed.strip() + text[len(text.rstrip()) :] if stripped else healed
    try:
        value = Parser(Lexer(healed), max_depth=max_depth).parse()
    except JSONParseError as exc:
        return HealResult(text, healed, repairs, False, False, None, exc.to_dict())
    return HealResult(text, healed, repairs, True, False, value)


def heal_json(text: str) -> tuple[str, list[Repair]]:
    """Return ``(healed_json_string, list_of_repairs_performed)``."""
    result = heal(text)
    return result.healed, result.repairs
