"""JSONPath interpreter: a dependency-free, tree-walking query engine.

Supported syntax (RFC 9535 plus the common Goessner forms):

=====================  ==========================================  ========================
selector               meaning                                     example
=====================  ==========================================  ========================
``$``                  root                                        ``$``
``.name`` ``['name']`` child member (bracket form takes any key)   ``$.users['first-name']``
``[n]`` ``[-n]``       array index (negative counts from the end)  ``$.users[-1]``
``.*`` ``[*]``         every child                                 ``$.users[*].name``
``..``                 descendants (then any selector)             ``$..id`` ``$..[0]``
``[a,b]``              union of selectors                          ``$['a','b']`` ``$[0,2]``
``[start:end:step]``   slice (Python/RFC semantics)                ``$.items[1:5:2]``
``[?expr]``            filter children                             ``$.users[?@.age >= 18]``
``[?(expr)]``          Goessner filter syntax                      ``$..book[?(@.price<10)]``
=====================  ==========================================  ========================

Filter expressions: ``@`` (current node) and ``$`` (root) queries; literals
(numbers, quoted strings, ``true``, ``false``, ``null``); comparisons
``== != < <= > >=``; logic ``&& || !``; parentheses; existence tests
(``?@.isbn``); and the RFC 9535 functions ``length()``, ``count()``,
``value()``, ``match()`` and ``search()``.

Comparison semantics follow RFC 9535: an empty query result is *Nothing*,
which equals only Nothing; ordering applies only to two numbers or two
strings; ``true`` never equals ``1``.

Each result is a :class:`Match` carrying the normalized path (the same
format as AST paths, see :mod:`engines.paths`) and the value.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from .core import ensure_recursion_headroom
from .paths import ROOT, path_child

MAX_REGEX_LENGTH = 200
MAX_INTERMEDIATE_NODES = 2_000_000
MAX_REGEX_SUBJECT = 10_000
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*[+*}](?:[^()\\]|\\.)*\)\s*[+*{]")


class JSONPathError(ValueError):
    """A malformed query; ``position`` is the 0-based offset of the problem."""

    def __init__(self, message: str, position: int):
        super().__init__(f"{message} at position {position}")
        self.message = message
        self.position = position


@dataclass(frozen=True, slots=True)
class Match:
    path: str
    value: Any

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "value": self.value}


# ----------------------------------------------------------------- AST nodes

_NOTHING = object()  # RFC 9535 "Nothing": result of an empty singular query


@dataclass(frozen=True)
class _Name:
    name: str


@dataclass(frozen=True)
class _Index:
    index: int


@dataclass(frozen=True)
class _Wildcard:
    pass


@dataclass(frozen=True)
class _Slice:
    start: int | None
    stop: int | None
    step: int | None


@dataclass(frozen=True)
class _Filter:
    expr: Any


@dataclass(frozen=True)
class _Segment:
    selectors: tuple[Any, ...]
    descendant: bool


@dataclass(frozen=True)
class _Query:
    relative: bool  # @ instead of $
    segments: tuple[_Segment, ...]


@dataclass(frozen=True)
class _Literal:
    value: Any


@dataclass(frozen=True)
class _Not:
    operand: Any


@dataclass(frozen=True)
class _Logical:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class _Compare:
    op: str
    left: Any
    right: Any


@dataclass(frozen=True)
class _Call:
    name: str
    args: tuple[Any, ...]


# -------------------------------------------------------------------- parser

_NAME_START = re.compile(r"[A-Za-z_$\u0080-\U0010ffff]")
_NAME = re.compile(r"[A-Za-z0-9_$\-\u0080-\U0010ffff]*")
_INT = re.compile(r"-?\d+")
_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "/": "/", "\\": "\\", "'": "'", '"': '"'}
_FUNCTIONS = {"length": 1, "count": 1, "value": 1, "match": 2, "search": 2}


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.i = 0

    def error(self, message: str) -> JSONPathError:
        return JSONPathError(message, self.i)

    def ws(self) -> None:
        while self.i < len(self.text) and self.text[self.i] in " \t\n\r":
            self.i += 1

    def peek(self, s: str) -> bool:
        return self.text.startswith(s, self.i)

    def take(self, s: str) -> bool:
        if self.peek(s):
            self.i += len(s)
            return True
        return False

    def expect(self, s: str) -> None:
        if not self.take(s):
            found = self.text[self.i : self.i + 1] or "end of query"
            raise self.error(f"Expected {s!r}, found {found!r}")

    # -- queries

    def query(self) -> _Query:
        self.ws()
        if self.take("$"):
            relative = False
        elif self.take("@"):
            relative = True
        else:
            raise self.error("A JSONPath query must start with '$'")
        return _Query(relative, tuple(self.segments()))

    def segments(self) -> Iterator[_Segment]:
        while True:
            if self.take(".."):
                if self.peek("["):
                    yield _Segment(self.bracket(), True)
                elif self.take("*"):
                    yield _Segment((_Wildcard(),), True)
                else:
                    yield _Segment((_Name(self.name()),), True)
            elif self.take("."):
                if self.take("*"):
                    yield _Segment((_Wildcard(),), False)
                else:
                    yield _Segment((_Name(self.name()),), False)
            elif self.peek("["):
                yield _Segment(self.bracket(), False)
            else:
                return

    def name(self) -> str:
        if self.i >= len(self.text) or not _NAME_START.match(self.text[self.i]):
            raise self.error("Expected a member name")
        match = _NAME.match(self.text, self.i + 1)
        assert match is not None
        start, self.i = self.i, match.end()
        return self.text[start : self.i]

    def string(self) -> str:
        quote = self.text[self.i]
        self.i += 1
        out: list[str] = []
        while self.i < len(self.text):
            ch = self.text[self.i]
            if ch == quote:
                self.i += 1
                return "".join(out)
            if ch == "\\":
                nxt = self.text[self.i + 1 : self.i + 2]
                if nxt in _ESCAPES:
                    out.append(_ESCAPES[nxt])
                    self.i += 2
                    continue
                if nxt == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", self.text[self.i + 2 : self.i + 6]):
                    out.append(chr(int(self.text[self.i + 2 : self.i + 6], 16)))
                    self.i += 6
                    continue
                raise self.error("Invalid escape in string literal")
            out.append(ch)
            self.i += 1
        raise self.error("Unterminated string literal")

    def bracket(self) -> tuple[Any, ...]:
        self.expect("[")
        selectors = [self.selector()]
        self.ws()
        while self.take(","):
            selectors.append(self.selector())
            self.ws()
        self.expect("]")
        return tuple(selectors)

    def selector(self) -> Any:
        self.ws()
        if self.peek("'") or self.peek('"'):
            return _Name(self.string())
        if self.take("*"):
            return _Wildcard()
        if self.take("?"):
            self.ws()
            return _Filter(self.logical_or())
        start = self.int_or_none()
        self.ws()
        if self.take(":"):
            self.ws()
            stop = self.int_or_none()
            self.ws()
            step = None
            if self.take(":"):
                self.ws()
                step = self.int_or_none()
            return _Slice(start, stop, step)
        if start is None:
            raise self.error("Expected a selector")
        return _Index(start)

    def int_or_none(self) -> int | None:
        match = _INT.match(self.text, self.i)
        if not match:
            return None
        self.i = match.end()
        return int(match.group())

    # -- filter expressions

    def logical_or(self) -> Any:
        left = self.logical_and()
        self.ws()
        while self.take("||"):
            left = _Logical("||", left, self.logical_and())
            self.ws()
        return left

    def logical_and(self) -> Any:
        left = self.unary()
        self.ws()
        while self.take("&&"):
            left = _Logical("&&", left, self.unary())
            self.ws()
        return left

    def unary(self) -> Any:
        self.ws()
        if self.peek("!") and not self.peek("!="):
            self.i += 1
            return _Not(self.unary())
        return self.comparison()

    def comparison(self) -> Any:
        left = self.operand()
        self.ws()
        for op in ("==", "!=", "<=", ">=", "<", ">"):
            if self.take(op):
                return _Compare(op, left, self.operand())
        return left

    def operand(self) -> Any:
        self.ws()
        if self.take("("):
            expr = self.logical_or()
            self.ws()
            self.expect(")")
            return expr
        if self.peek("@") or self.peek("$"):
            return self.query()
        if self.peek("'") or self.peek('"'):
            return _Literal(self.string())
        for word, value in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(word, self.i) and not _NAME.match(self.text, self.i + len(word)).group():
                self.i += len(word)
                return _Literal(value)
        number = _NUMBER.match(self.text, self.i)
        if number:
            self.i = number.end()
            raw = number.group()
            return _Literal(float(raw) if any(c in raw for c in ".eE") else int(raw))
        match = re.compile(r"[a-z][a-z0-9_]*").match(self.text, self.i)
        if match and self.text.startswith("(", match.end()):
            name = match.group()
            if name not in _FUNCTIONS:
                raise self.error(f"Unknown function {name}()")
            self.i = match.end() + 1
            args = [self.logical_or()]
            self.ws()
            while self.take(","):
                args.append(self.logical_or())
                self.ws()
            self.expect(")")
            if len(args) != _FUNCTIONS[name]:
                raise self.error(f"{name}() takes {_FUNCTIONS[name]} argument(s)")
            if name in ("match", "search") and isinstance(args[1], _Literal) and isinstance(args[1].value, str):
                try:
                    _safe_regex(args[1].value)  # reject unsafe literal patterns at compile time
                except JSONPathError as exc:
                    raise self.error(exc.message) from None
            return _Call(name, tuple(args))
        raise self.error("Expected a filter operand")


def compile_path(query: str) -> _Query:
    """Parse ``query`` into a reusable compiled form."""
    parser = _Parser(query.strip())
    compiled = parser.query()
    if compiled.relative:
        raise JSONPathError("A top-level query must start with '$'", 0)
    parser.ws()
    if parser.i != len(parser.text):
        raise parser.error(f"Unexpected {parser.text[parser.i]!r}")
    return compiled


# ----------------------------------------------------------------- evaluator

_Node = tuple[str, Any]


def _children(node: _Node) -> Iterator[_Node]:
    path, value = node
    if isinstance(value, dict):
        for key, child in value.items():
            yield path_child(path, key), child
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield path_child(path, index), child


def _descendants(node: _Node) -> Iterator[_Node]:
    """The node itself, then all descendants in document order (iterative)."""
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(reversed(list(_children(current))))


class _Evaluator:
    def __init__(self, root: Any):
        self.root = root

    def run(self, query: _Query, start: _Node, limit: int | None = None) -> list[_Node]:
        """Apply every segment. ``limit`` caps the final result (callers ask
        for one extra node to detect truncation)."""
        nodes = [start]
        last = len(query.segments) - 1
        for position, segment in enumerate(query.segments):
            cap = limit if position == last else None
            out: list[_Node] = []
            sources = (d for n in nodes for d in _descendants(n)) if segment.descendant else iter(nodes)
            for node in sources:
                for selector in segment.selectors:
                    for found in self.select(selector, node):
                        out.append(found)
                        if cap is not None and len(out) >= cap:
                            return out
                if len(out) > MAX_INTERMEDIATE_NODES:
                    raise JSONPathError(f"Query matched more than {MAX_INTERMEDIATE_NODES:,} intermediate nodes", 0)
            nodes = out
        return nodes[:limit] if limit is not None else nodes

    def select(self, selector: Any, node: _Node) -> Iterator[_Node]:
        path, value = node
        if isinstance(selector, _Name):
            if isinstance(value, dict) and selector.name in value:
                yield path_child(path, selector.name), value[selector.name]
        elif isinstance(selector, _Index):
            if isinstance(value, list):
                index = selector.index + len(value) if selector.index < 0 else selector.index
                if 0 <= index < len(value):
                    yield path_child(path, index), value[index]
        elif isinstance(selector, _Wildcard):
            yield from _children(node)
        elif isinstance(selector, _Slice):
            if isinstance(value, list) and selector.step != 0:
                for index in range(*slice(selector.start, selector.stop, selector.step).indices(len(value))):
                    yield path_child(path, index), value[index]
        else:  # _Filter
            for child in _children(node):
                if self.truthy(selector.expr, child):
                    yield child

    # -- filter evaluation

    def truthy(self, expr: Any, current: _Node) -> bool:
        if isinstance(expr, _Logical):
            if expr.op == "&&":
                return self.truthy(expr.left, current) and self.truthy(expr.right, current)
            return self.truthy(expr.left, current) or self.truthy(expr.right, current)
        if isinstance(expr, _Not):
            return not self.truthy(expr.operand, current)
        if isinstance(expr, _Compare):
            return _compare(expr.op, self.value(expr.left, current), self.value(expr.right, current))
        if isinstance(expr, _Query):
            return bool(self.nodes(expr, current))  # existence test
        if isinstance(expr, _Call):
            result = self.call(expr, current)
            return result is True if isinstance(result, bool) else result not in (_NOTHING, None, False)
        return bool(expr.value)  # _Literal

    def nodes(self, query: _Query, current: _Node) -> list[_Node]:
        return self.run(query, current if query.relative else (ROOT, self.root))

    def value(self, expr: Any, current: _Node) -> Any:
        if isinstance(expr, _Literal):
            return expr.value
        if isinstance(expr, _Query):
            found = self.nodes(expr, current)
            return found[0][1] if len(found) == 1 else _NOTHING
        if isinstance(expr, _Call):
            return self.call(expr, current)
        # A logical expression used as a value.
        return self.truthy(expr, current)

    def call(self, expr: _Call, current: _Node) -> Any:
        if expr.name == "count":
            arg = expr.args[0]
            if not isinstance(arg, _Query):
                raise JSONPathError("count() takes a query", 0)
            return len(self.nodes(arg, current))
        if expr.name == "value":
            arg = expr.args[0]
            if not isinstance(arg, _Query):
                raise JSONPathError("value() takes a query", 0)
            found = self.nodes(arg, current)
            return found[0][1] if len(found) == 1 else _NOTHING
        if expr.name == "length":
            subject = self.value(expr.args[0], current)
            if isinstance(subject, (str, list, dict)) and not isinstance(subject, bool):
                return len(subject)
            return _NOTHING
        subject = self.value(expr.args[0], current)
        pattern = self.value(expr.args[1], current)
        if not isinstance(subject, str) or not isinstance(pattern, str):
            return False
        compiled = _safe_regex(pattern)
        subject = subject[:MAX_REGEX_SUBJECT]
        if expr.name == "match":
            return compiled.fullmatch(subject) is not None
        return compiled.search(subject) is not None


def _safe_regex(pattern: str) -> re.Pattern[str]:
    """Compile a user regex, refusing shapes that backtrack catastrophically."""
    if len(pattern) > MAX_REGEX_LENGTH:
        raise JSONPathError(f"Regular expressions are limited to {MAX_REGEX_LENGTH} characters", 0)
    if _NESTED_QUANTIFIER.search(pattern) or re.search(r"\\\d", pattern):
        raise JSONPathError("Nested quantifiers and backreferences are not allowed in match()/search()", 0)
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise JSONPathError(f"Invalid regular expression: {exc.msg}", 0) from None


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _equal(a: Any, b: Any) -> bool:
    if a is _NOTHING or b is _NOTHING:
        return a is b
    if _is_number(a) and _is_number(b):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_equal(a[k], b[k]) for k in a)
    return a == b


def _compare(op: str, a: Any, b: Any) -> bool:
    if op == "==":
        return _equal(a, b)
    if op == "!=":
        return not _equal(a, b)
    if op in ("<=", ">=") and _equal(a, b):
        return True
    comparable = (_is_number(a) and _is_number(b)) or (isinstance(a, str) and isinstance(b, str))
    if not comparable:
        return False
    if op in ("<", "<="):
        return a < b
    return a > b


# -------------------------------------------------------------------- public


def query_json(data: Any, query: str, limit: int | None = None) -> list[Match]:
    """Evaluate ``query`` against ``data`` and return matches in document order.

    With ``limit``, at most ``limit`` matches are returned (pass ``limit + 1``
    to find out whether more exist)."""
    ensure_recursion_headroom()
    compiled = compile_path(query)
    nodes = _Evaluator(data).run(compiled, (ROOT, data), limit)
    return [Match(path, value) for path, value in nodes]


def json_path(data: Any, query: str) -> list[Any]:
    """Return the matched values only."""
    return [match.value for match in query_json(data, query)]


Selector = Callable[[Any], list[Any]]


def compile_selector(query: str) -> Selector:
    """Compile once, run many times: ``select = compile_selector('$..id')``."""
    compiled = compile_path(query)
    return lambda data: [value for _, value in _Evaluator(data).run(compiled, (ROOT, data))]
