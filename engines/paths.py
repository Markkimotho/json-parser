"""Path formatting shared by every engine.

Every module that reports a location inside a document (AST nodes, audit
findings, duplicate keys, JSONPath matches) formats it with :func:`path_child`
so a path copied from one view can be pasted into the JSONPath terminal and
resolve to exactly the same node.

Format: ``$`` is the root, identifier-like keys use dot notation
(``$.users``), everything else uses single-quoted bracket notation with
RFC 9535 escapes (``$['content-type']``), and array indexes use ``[n]``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

ROOT = "$"

_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
_ESCAPES = {"\\": "\\\\", "'": "\\'", "\b": "\\b", "\f": "\\f", "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def quote_name(name: str) -> str:
    """Return ``name`` as a single-quoted JSONPath string literal."""
    out: list[str] = []
    for ch in name:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "'" + "".join(out) + "'"


def path_child(parent: str, segment: str | int) -> str:
    """Append one member name or array index to ``parent``."""
    if isinstance(segment, int) and not isinstance(segment, bool):
        return f"{parent}[{segment}]"
    if _IDENTIFIER.match(segment):
        return f"{parent}.{segment}"
    return f"{parent}[{quote_name(segment)}]"


def format_path(segments: Iterable[str | int]) -> str:
    """Format a sequence of segments, starting from the root."""
    path = ROOT
    for segment in segments:
        path = path_child(path, segment)
    return path
