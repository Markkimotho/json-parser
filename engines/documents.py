"""Parsed-document cache.

The JSONPath terminal, type generator and compressor all start from the same
parsed tree. :class:`DocumentCache` keeps recently parsed documents keyed by
the SHA-256 of their text, so repeated queries against an unchanged editor
skip re-parsing. It is bounded by entry count and total source size, is
thread-safe, and is per-process (a cache miss just re-parses, so multiple
gunicorn workers stay correct).
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Any

from .core import MAX_RECURSION_DEPTH, Lexer, Parser


class DocumentCache:
    def __init__(self, max_entries: int = 32, max_bytes: int = 64 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._entries: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()

    def parse(self, text: str, max_depth: int = MAX_RECURSION_DEPTH) -> tuple[Any, bool]:
        """Return ``(value, cache_hit)``. Raises ``JSONParseError`` like the parser."""
        key = self.key(text)
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
                self.hits += 1
                return self._entries[key][0], True
        value = Parser(Lexer(text), max_depth=max_depth).parse()
        size = len(text)
        with self._lock:
            self.misses += 1
            if size <= self.max_bytes and key not in self._entries:
                self._entries[key] = (value, size)
                self._bytes += size
                while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                    _, (_, evicted) = self._entries.popitem(last=False)
                    self._bytes -= evicted
        return value, False

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0

    def __len__(self) -> int:
        return len(self._entries)
