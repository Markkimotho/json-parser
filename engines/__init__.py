"""JSON Developer Engine: the hand-written parser plus its ecosystem modules.

==================  ==========================================================
module              responsibility
==================  ==========================================================
``core``            Lexer, recursive-descent Parser, tokens/AST/trace capture
``paths``           JSONPath-compatible path formatting shared by all modules
``healing``         Fuzzy Healing Gateway: repair damaged JSON with an audit
                    trail
``types``           Polyglot generator: TypeScript, Pydantic v2, Go
``auditor``         Interoperability & security linter
``compressor``      LLM token compressor (minified JSON and compact notation)
``query``           JSONPath interpreter
``documents``       Bounded cache of parsed documents
==================  ==========================================================

No module imports Flask; ``app.py`` maps HTTP requests onto these functions.
"""

from .auditor import AuditReport, audit_json
from .compressor import CompressionResult, compress_json, decode_compact, encode_compact, estimate_tokens, minify
from .core import (
    MAX_NUMBER,
    MAX_RECURSION_DEPTH,
    MIN_NUMBER,
    JSONParseError,
    Lexer,
    Parser,
    TokenType,
    ensure_recursion_headroom,
    parse,
)
from .documents import DocumentCache
from .healing import HealResult, Repair, heal, heal_json, strip_comments
from .paths import format_path, path_child
from .query import JSONPathError, Match, compile_selector, json_path, query_json
from .types import SchemaError, build_model, generate_types

__all__ = [
    "AuditReport",
    "CompressionResult",
    "DocumentCache",
    "HealResult",
    "JSONParseError",
    "JSONPathError",
    "Lexer",
    "MAX_NUMBER",
    "MAX_RECURSION_DEPTH",
    "MIN_NUMBER",
    "Match",
    "Parser",
    "Repair",
    "SchemaError",
    "TokenType",
    "audit_json",
    "build_model",
    "compile_selector",
    "compress_json",
    "decode_compact",
    "encode_compact",
    "ensure_recursion_headroom",
    "estimate_tokens",
    "format_path",
    "generate_types",
    "heal",
    "heal_json",
    "json_path",
    "minify",
    "parse",
    "path_child",
    "query_json",
    "strip_comments",
]
