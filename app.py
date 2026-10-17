"""Flask routing layer for the JSON Developer Engine.

All parsing and analysis lives in the ``engines`` package; this module only
reads requests, applies size limits, calls the engines and shapes responses.

Routes
------
GET  /                 -> redirect to /engine/
GET  /engine/          Developer Engine dashboard
GET  /python/          Workbench (site/) running against the Python parser
GET  /terminal/        Legacy retro terminal UI
POST /parse-json       parse (+ tokens, trace, AST; optional lenient healing)
POST /heal-json        repair damaged JSON, with an audit trail
POST /generate-types   TypeScript / Pydantic / Go code
POST /audit-json       security & interoperability audit
POST /query-json       JSONPath query
POST /compress-json    LLM token compression

POST bodies may be form fields or a JSON object. The document is
``jsonData`` (or a ``jsonFile`` upload).
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for
from werkzeug.exceptions import RequestEntityTooLarge

from engines import (
    MAX_NUMBER,
    MAX_RECURSION_DEPTH,
    MIN_NUMBER,
    DocumentCache,
    JSONParseError,
    JSONPathError,
    Lexer,
    Parser,
    SchemaError,
    TokenType,
    audit_json,
    compress_json,
    generate_types,
    heal,
    query_json,
)

__all__ = ["app", "Lexer", "Parser", "JSONParseError", "TokenType", "MAX_RECURSION_DEPTH", "MIN_NUMBER", "MAX_NUMBER"]

app = Flask(__name__)
app.json.sort_keys = False  # keep object keys in document order

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE + 1024 * 1024  # multipart overhead
# Visualization payloads (tokens, trace, AST) grow ~20x faster than the input,
# so they are only produced for documents up to this size.
VISUAL_LIMIT_BYTES = 512 * 1024
TOKEN_LIMIT = 20_000
TRACE_LIMIT = 20_000
AST_NODE_LIMIT = 20_000
QUERY_LIMIT_DEFAULT = 500
QUERY_LIMIT_MAX = 5_000
QUERY_VALUE_PREVIEW = 100_000  # characters of pretty JSON shown per match

documents = DocumentCache()


class InputError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# ------------------------------------------------------------------ helpers


def _payload() -> dict[str, Any]:
    if request.is_json:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise InputError("JSON request bodies must be an object such as {\"jsonData\": \"...\"}")
        return data
    return request.form.to_dict()


def _flag(payload: dict[str, Any], name: str, default: bool) -> bool:
    value = payload.get(name, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _document(payload: dict[str, Any]) -> tuple[str, str]:
    """Return ``(text, source)`` from ``jsonData`` or a ``jsonFile`` upload."""
    if "jsonData" in payload:
        text = payload["jsonData"]
        if not isinstance(text, str):
            raise InputError("jsonData must be a string containing JSON text")
        source = "json" if request.is_json else "form"
    elif "jsonFile" in request.files:
        upload = request.files["jsonFile"]
        if not upload.filename:
            raise InputError("No file selected")
        content = upload.read()
        if not content:
            raise InputError("File is empty")
        if len(content) > MAX_FILE_SIZE:
            raise InputError(f"File size exceeds maximum of {MAX_FILE_SIZE / 1024 / 1024:.0f}MB")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise InputError("File is not valid UTF-8 encoded") from None
        source = "file"
    else:
        raise InputError("No JSON data or file provided")
    if not text.strip():
        raise InputError("JSON input is empty")
    if len(text.encode("utf-8", "surrogatepass")) > MAX_FILE_SIZE:
        raise InputError("JSON input exceeds the 10MB limit", 413)
    return text, source


def _error(exc: JSONParseError, **extra: Any) -> dict[str, Any]:
    return {"error": exc.message, "line": exc.line, "column": exc.column, "index": exc.index, "valid": False, **extra}


def api_route(func: Callable[..., Any]) -> Callable[..., Any]:
    """Shared error handling and a structured log line per request."""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        outcome = "ok"
        try:
            response = func(*args, **kwargs)
            if isinstance(response, tuple):
                outcome = f"http_{response[1]}"
            return response
        except InputError as exc:
            outcome = f"input_error_{exc.status}"
            return jsonify(error=exc.message, valid=False), exc.status
        except JSONParseError as exc:
            outcome = "parse_error"
            return jsonify(_error(exc)), 400
        except RequestEntityTooLarge:
            outcome = "too_large"
            raise
        except Exception:
            outcome = "server_error"
            app.logger.exception("Could not process JSON in %s", request.path)
            return jsonify(error="Could not process JSON", valid=False), 500
        finally:
            app.logger.info(
                "route=%s outcome=%s bytes=%s duration_ms=%.1f",
                request.path, outcome, request.content_length or 0, (time.perf_counter() - started) * 1000,
            )

    return wrapper


def _visual_parse(text: str, visual: bool) -> tuple[Any, Lexer, Parser]:
    lexer = Lexer(text, capture_tokens=visual, capture_limit=TOKEN_LIMIT)
    parser = Parser(
        lexer, max_depth=MAX_RECURSION_DEPTH, capture_ast=visual, trace=visual,
        trace_limit=TRACE_LIMIT, max_ast_nodes=AST_NODE_LIMIT,
    )
    return parser.parse(), lexer, parser


def _visual_payload(lexer: Lexer, parser: Parser | None, visual: bool, reason: str) -> dict[str, Any]:
    return {
        "tokens": lexer.tokens,
        "trace": parser.execution_trace if parser else [],
        "ast": parser.ast if parser else None,
        "visual": {
            "available": visual,
            "reason": reason,
            "tokens_truncated": lexer.tokens_truncated,
            "trace_truncated": bool(parser and parser.trace_truncated),
            "ast_truncated": bool(parser and parser.ast_truncated),
            "limits": {"bytes": VISUAL_LIMIT_BYTES, "tokens": TOKEN_LIMIT, "trace": TRACE_LIMIT, "ast_nodes": AST_NODE_LIMIT},
        },
    }


# ------------------------------------------------------------------- pages


@app.route("/")
def index():
    return redirect(url_for("engine"))


@app.route("/engine/")
def engine():
    return render_template("engine.html")


@app.route("/favicon.ico")
def favicon():
    return send_from_directory("static/engine", "favicon.svg", mimetype="image/svg+xml")


@app.route("/python/")
def python_workbench():
    return send_from_directory("site", "index.html")


@app.route("/python/<path:filename>")
def python_assets(filename: str):
    if filename not in {"index.html", "app.js", "logic.js", "style.css"}:
        abort(404)
    return send_from_directory("site", filename)


@app.route("/terminal/")
def terminal():
    return render_template("index.html")


@app.errorhandler(RequestEntityTooLarge)
def too_large(_error: Exception):
    return jsonify(error="JSON input exceeds the 10MB limit", valid=False), 413


# --------------------------------------------------------------------- API


@app.post("/parse-json")
@api_route
def parse_json():
    """Parse with the strict engine; visual metadata for documents <= 512KB.

    Options: ``visualize`` (default true), ``lenient`` (default false). With
    ``lenient``, a document the strict parser rejects goes through the
    healing gateway and the repaired text is parsed instead.
    """
    payload = _payload()
    text, source = _document(payload)
    size = len(text.encode("utf-8", "surrogatepass"))
    visual = _flag(payload, "visualize", True) and size <= VISUAL_LIMIT_BYTES
    reason = "" if visual else (
        f"Visualization is limited to {VISUAL_LIMIT_BYTES // 1024}KB documents" if size > VISUAL_LIMIT_BYTES
        else "Visualization disabled by request"
    )
    lenient = _flag(payload, "lenient", False)
    started = time.perf_counter()
    healing: dict[str, Any] = {"applied": False, "repairs": []}
    parsed_text = text
    try:
        value, lexer, parser = _visual_parse(text, visual)
    except JSONParseError as strict_error:
        if not lenient:
            lexer = Lexer(text, capture_tokens=visual, capture_limit=TOKEN_LIMIT)
            partial: Parser | None = None
            try:
                partial = Parser(lexer, capture_ast=False, trace=visual, trace_limit=TRACE_LIMIT)
                partial.parse()
            except JSONParseError:
                pass
            return jsonify(_error(strict_error, **_visual_payload(lexer, partial, visual, reason))), 400
        result = heal(text)
        healing = {
            "applied": result.valid,
            "repairs": [repair.to_dict() for repair in result.repairs],
            "healed_text": result.healed,
            "original_error": _error(strict_error),
        }
        if not result.valid:
            assert result.error is not None
            return jsonify(
                error=f"Could not heal JSON: {result.error['error']}",
                line=result.error.get("line"), column=result.error.get("column"),
                index=result.error.get("index"), valid=False, healing=healing,
            ), 400
        parsed_text = result.healed
        value, lexer, parser = _visual_parse(parsed_text, visual)

    duration = (time.perf_counter() - started) * 1000
    return jsonify(
        result=value,
        formatted=json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False),
        minified=json.dumps(value, separators=(",", ":"), ensure_ascii=True, allow_nan=False),
        # Same output with non-ASCII text kept literal, for writing back into the editor.
        editor_formatted=json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False),
        editor_minified=json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
        source=source,
        valid=True,
        healing=healing,
        stats={
            "bytes": len(parsed_text.encode("utf-8", "surrogatepass")),
            "tokens": lexer.token_count,
            "nodes": parser.node_count if visual else None,
            "max_depth": parser.max_depth_seen,
            "parse_ms": round(duration, 2),
        },
        **_visual_payload(lexer, parser, visual, reason),
    )


@app.post("/heal-json")
@api_route
def heal_route():
    """Repair damaged JSON. Always 200; ``valid`` says whether it worked."""
    text, _ = _document(_payload())
    result = heal(text)
    body = result.to_dict()
    if result.valid:
        body["formatted"] = json.dumps(result.value, indent=2, ensure_ascii=False, allow_nan=False)
    return jsonify(body)


@app.post("/generate-types")
@api_route
def types_route():
    payload = _payload()
    text, _ = _document(payload)
    root_name = str(payload.get("rootName") or "RootObject")[:64]
    mode = str(payload.get("mode") or "auto")
    if mode not in ("auto", "sample", "schema"):
        raise InputError("mode must be auto, sample or schema")
    value, cached = documents.parse(text)
    try:
        generated = generate_types(value, root_name=root_name, mode=mode)  # type: ignore[arg-type]
    except SchemaError as exc:
        return jsonify(error=str(exc), valid=False), 422
    return jsonify(
        types={lang: generated[lang] for lang in ("typescript", "pydantic", "go")},
        mode=generated["mode"], root=generated["root"], names=generated["types"], cached=cached, valid=True,
    )


@app.post("/audit-json")
@api_route
def audit_route():
    text, _ = _document(_payload())
    report = audit_json(text).to_dict()
    if report["status"] == "INVALID":
        return jsonify(_parse_error_dict(report["error"]) | {"audit": report}), 400
    return jsonify(audit=report, valid=True)


def _parse_error_dict(error: dict[str, Any]) -> dict[str, Any]:
    return {"error": error["error"], "line": error["line"], "column": error["column"], "index": error["index"], "valid": False}


@app.post("/query-json")
@api_route
def query_route():
    payload = _payload()
    text, _ = _document(payload)
    path = str(payload.get("path") or payload.get("query") or "$")
    try:
        limit = max(1, min(int(payload.get("limit", QUERY_LIMIT_DEFAULT)), QUERY_LIMIT_MAX))
    except (TypeError, ValueError):
        raise InputError("limit must be an integer") from None
    value, cached = documents.parse(text)
    started = time.perf_counter()
    try:
        matches = query_json(value, path, limit=limit + 1)
    except JSONPathError as exc:
        return jsonify(error=exc.message, position=exc.position, path=path, valid=False), 400
    truncated = len(matches) > limit
    matches = matches[:limit]
    rendered = []
    for match in matches:
        pretty = json.dumps(match.value, indent=2, ensure_ascii=False, allow_nan=False)
        rendered.append({
            "path": match.path,
            "value": match.value,
            "value_json": pretty if len(pretty) <= QUERY_VALUE_PREVIEW else pretty[:QUERY_VALUE_PREVIEW] + "\n…",
        })
    return jsonify(
        path=path, matches=rendered, result=[m.value for m in matches], count=len(matches),
        truncated=truncated, cached=cached, duration_ms=round((time.perf_counter() - started) * 1000, 2), valid=True,
    )


@app.post("/compress-json")
@api_route
def compress_route():
    payload = _payload()
    text, _ = _document(payload)
    mode = str(payload.get("mode") or "all")
    if mode not in ("all", "compact", "minified"):
        raise InputError("mode must be all, compact or minified")
    value, cached = documents.parse(text)
    modes = ("compact", "minified") if mode == "all" else (mode,)
    return jsonify(
        modes={m: compress_json(value, m, original=text).to_dict() for m in modes},  # type: ignore[arg-type]
        cached=cached, valid=True,
    )


if __name__ == "__main__":  # pragma: no cover
    port = int(os.environ.get("PORT", 5001))
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=port)
