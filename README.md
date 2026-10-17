# JSON Parser

Choose the [browser workbench](https://markkimotho.github.io/json-parser/) to validate, format, and minify JSON without uploading it, the [hosted Python workbench](https://json-parser-python.onrender.com/python/) to use this repository's hand-written lexer/parser through Flask, or the [Developer Engine](https://json-parser-python.onrender.com/engine/) to inspect tokens, the AST and a step-by-step parser trace, heal damaged JSON, generate TypeScript/Pydantic/Go types, audit for security issues, run JSONPath queries and compress JSON for LLM prompts.

## Python version

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
python3 app.py
```

Open [http://127.0.0.1:5001/python/](http://127.0.0.1:5001/python/) for the local Python edition. It uses `Lexer` and `Parser` from `app.py` through `/parse-json`; inputs are sent to the Flask process on your machine. The older terminal interface remains at `/terminal/`.

The [public Python edition](https://json-parser-python.onrender.com/python/) runs the same Flask app on Render. Submitted JSON is sent to that server; use the browser edition if you need to keep input on your device. Render's free service may take time to wake after inactivity. Its deployment configuration is in `render.yaml`.

To run the browser-only edition locally: `python3 -m http.server 8000 --directory site`, then open `http://127.0.0.1:8000/`. Run the Python tests with `python3 -m unittest discover -s resources/tests` and browser logic tests with `node --test site/logic.test.js`.

## Developer Engine

`/engine/` is a two-pane dashboard built on the same hand-written parser. The left pane is the source editor: file drop or open, samples, Prettify, Minify, a Lenient toggle, and live compile. The right pane has seven views:

| View | What it shows | Engine |
|---|---|---|
| **AST** | Collapsible syntax tree. Every object, property, key, array and scalar node carries its line, column, source span, JSONPath, parent id and depth. Click a node to select its source. | `engines/core.py` |
| **Tokens & Tracer** | The lexer's token stream, plus a step debugger replaying the parser's execution trace (`parse_object()`, `match_token(COLON)`, pointer advances) with the live call stack and a source pointer. | `engines/core.py` |
| **Fuzzy Logs** | Every repair the healing gateway made, with position and before/after text, a line diff, and "Apply healed JSON". | `engines/healing.py` |
| **Polyglot** | TypeScript interfaces, Pydantic v2 models and Go structs, from a sample document or a JSON Schema. | `engines/types.py` |
| **Audit** | SECURE / WARNING / CRITICAL badge and a checklist: duplicate keys, prototype-pollution keys, unsafe integers, float rounding, nesting depth, lone surrogates. | `engines/auditor.py` |
| **JSONPath** | A terminal that runs RFC 9535 queries (filters, slices, descendants, functions) against the cached parse tree. | `engines/query.py` |
| **LLM Compress** | Minified JSON, or a TOON-style compact notation that decodes back to the identical value, with token savings. | `engines/compressor.py` |

Run it locally with `python3 app.py` and open [http://127.0.0.1:5001/engine/](http://127.0.0.1:5001/engine/) (`/` redirects there).

### What each engine does

- **Visual compiler.** `Lexer(text, capture_tokens=True)` records `{index, type, value, raw, start_index, end_index, line, column}` per token. `Parser(lexer, capture_ast=True, trace=True)` builds the AST and the execution trace. Everything is off by default; with it off, parsing does the same work as before.
- **Fuzzy healing.** `heal_json(text)` returns `(healed_json_string, repairs)`. The strict parser runs first; only rejected input is healed. A lenient lexer treats `//`, `#` and `/* */` comments, Markdown fences and a BOM as removable trivia. A repair parser then fixes:
  - trailing, missing and doubled commas
  - unquoted and single-quoted keys; single-quoted and typographic-quoted strings
  - `True`/`None`/`NaN`/`undefined`, and literals truncated at end of input
  - `+1`/`.5`/`0x1F` numbers, invalid escapes and raw control characters
  - unterminated strings, unclosed or mistyped brackets, and missing colons or values
  - duplicate keys (keeps the last value, like `JSON.parse`)
  - prose around LLM output, and multiple top-level values (NDJSON)

  The output keeps the author's formatting, and the strict parser re-verifies the result. `strip_comments(text)` is also available on its own.
- **Polyglot types.** One IR, two front-ends, three back-ends.
  - Sample mode merges every object seen at the same position: missing fields become optional, `null` makes a field nullable, `int` with `float` widens to a number, and anything else becomes a union. Structurally identical objects share one type.
  - Schema mode covers `type`, `properties`, `required`, `items`, `enum`, `const`, `anyOf`/`oneOf`/`allOf`, `additionalProperties`, `nullable`, and `$ref` into `$defs`/`definitions`, including recursive references.
  - Keys that are not valid identifiers get Pydantic aliases, quoted TypeScript keys and Go JSON tags.
- **Auditor.** Reads source text, not parsed values, because duplicate keys and exact number digits are gone after parsing. Unsafe integers are checked against `Number.MAX_SAFE_INTEGER`, including `1e16`-style literals. Depth above 25 warns; above 64 is critical, the depth at which System.Text.Json and Newtonsoft reject by default.
- **Compressor.** The compact layout (one-space indentation, `key:value`, tables for arrays of uniform objects) was chosen by measuring real tokenizers. Across the fixtures it costs about 13% fewer tokens than minified JSON and 47% fewer than pretty JSON. Deeply nested documents with few repeated keys can be cheaper minified, so the UI shows both. Token counts are estimated by a heuristic fitted to cl100k/o200k (mean error about 7%).
- **JSONPath.** A tree-walking interpreter with no dependencies. Paths use one format across the AST, the auditor and query results, so any path shown anywhere can be pasted into the terminal.

### HTTP API

Every route takes form fields or a JSON body with `jsonData` (or a `jsonFile` upload). Errors return `{"error", "line", "column", "index", "valid": false}`.

| Route | Extra inputs | Returns |
|---|---|---|
| `POST /parse-json` | `lenient` (heal on failure), `visualize` (default true) | `result`, `formatted`, `minified`, `tokens`, `trace`, `ast`, `stats`, `healing`, `visual` (limits and truncation flags) |
| `POST /heal-json` | | `healed`, `repairs[]`, `valid`, `strict`, `changed`, `formatted` |
| `POST /generate-types` | `rootName`, `mode` = `auto`/`sample`/`schema` | `types.{typescript,pydantic,go}`, `mode`, `names` |
| `POST /audit-json` | | `audit.{status, checks[], findings[], counts, stats}` |
| `POST /query-json` | `path`, `limit` (default 500, max 5000) | `matches[{path, value, value_json}]`, `count`, `truncated`, `cached` |
| `POST /compress-json` | `mode` = `all`/`compact`/`minified` | `modes.{compact,minified}.{output, stats}` |

Tokens, trace and AST are produced for documents up to 512 KB, each capped at 20,000 entries; the response says when a cap applied. Parsed documents are cached per process by SHA-256, so repeated queries skip re-parsing.

### Programmatic use

```python
from engines import Lexer, Parser, audit_json, generate_types, heal_json, query_json, encode_compact

lexer = Lexer('{"users": [{"id": 1}]}', capture_tokens=True)
parser = Parser(lexer, capture_ast=True, trace=True)
value = parser.parse()                      # {'users': [{'id': 1}]}
lexer.tokens[1]                             # {'type': 'STRING', 'value': 'users', 'line': 1, 'column': 2, ...}
parser.ast["children"][0]["path"]           # '$.users'

healed, repairs = heal_json("{name: 'Ada', tags: ['x',],}")
# healed == '{"name": "Ada", "tags": ["x"]}', repairs[i].kind in {'unquoted-key', 'trailing-comma', ...}

generate_types(value)["typescript"]         # 'export interface RootObject {\n  users: User[];\n}\n...'
audit_json('{"a": 1, "a": 2}').status       # 'CRITICAL'
[m.path for m in query_json(value, "$..id")]  # ['$.users[0].id']
encode_compact(value)                       # 'users[1]{id}:\n 1'
```

`from app import Lexer, Parser, JSONParseError` still works.

## Overview

A hand-written Python JSON lexer/parser with a Flask interface and local tests. The separate browser workbench uses `JSON.parse` for a static, no-upload demo. Neither interface is presented as a security boundary for untrusted data.

## Key Features

### [OK] Comprehensive JSON Support
- **All primitive types**: strings, numbers, booleans, null
- **Complex types**: objects, arrays, nested structures
- **Number formats**: integers, floats, scientific notation (1e5, 1.5e-3)
- **String escapes**: All standard escape sequences + unicode escapes (\uXXXX)
- **Whitespace handling**: Proper whitespace skipping throughout

### [SECURE] Robust Error Handling
- **Detailed error messages** with line and column information
- **Validation for edge cases**: leading zeros, trailing commas, duplicate keys
- **Proper escape sequence validation** with helpful error messages
- **Custom JSONParseError** exception with context
- **File encoding validation** (UTF-8 detection)

### [SCALE] Scalability & Performance
- **Recursion depth limits** to prevent stack overflow (configurable, default 1000)
- **File size limits** to prevent memory exhaustion (10MB default)
- **Efficient tokenization** with proper lookahead
- **Type hints** throughout for better performance analysis

### [LOCK] Security & Validation
- **Input size validation** with configurable limits
- **Number range validation** (64-bit integer bounds)
- **Depth tracking** for nested structures
- **Escape character validation** for safe string parsing

## Technologies Used

- **Python 3.11+**: Core implementation with type hints
- **Flask**: Web application framework
- **HTML/CSS**: User interface
- **JavaScript**: Client-side interactivity
- **unittest**: Python parser and Flask route tests
- **Gunicorn / Render**: Production server and optional deployment configuration

## Installation

To run this project locally, follow these steps:

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/Markkimotho/json-parser.git
   cd json-parser
   ```

2. **Set Up a Virtual Environment**:
    ```bash
    python -m venv venv
    source venv/bin/activate  # On Windows use `venv\Scripts\activate`
    ```

3. **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

4. **Run the Application**:
    ```bash
    python app.py
    ```

5. **Access the App**: Open your web browser and navigate to http://127.0.0.1:5001/python/

## Usage

### Web Interface

1. **Enter JSON Data**: 
   - Paste your JSON directly into the text area
   - Click "Parse" to validate and parse the JSON
   - View results or detailed error messages

2. **Upload JSON File**: 
   - Click the file upload button to select a JSON file
   - The app automatically parses the contents
   - View results or error details with line/column information

### Programmatic Usage

```python
from app import Lexer, Parser, JSONParseError

# Parse JSON string
json_string = '{"name": "John", "age": 30}'

try:
    lexer = Lexer(json_string)
    parser = Parser(lexer)
    result = parser.parse()
    print(result)  # {'name': 'John', 'age': 30}
except JSONParseError as e:
    print(f"Parse error at line {e.line}, column {e.column}: {e.message}")
```

## Supported Use Cases

### 1. Basic Types & Values
- Empty objects and arrays
- Primitive values (strings, numbers, booleans, null)
- Unicode strings and escape sequences
- Scientific notation numbers

```json
{
  "string": "value",
  "number": 42,
  "float": 3.14,
  "scientific": 1.5e-3,
  "boolean": true,
  "null_value": null
}
```

### 2. Complex Nested Structures
- Multi-level nested objects and arrays
- Arrays of objects
- Mixed type arrays
- Deeply nested structures

```json
{
  "users": [
    {
      "id": 1,
      "name": "Alice",
      "tags": ["admin", "developer"],
      "metadata": {
        "created": "2024-01-01",
        "active": true
      }
    }
  ]
}
```

### 3. String Processing
- All escape sequences: \n, \t, \r, \", \\, etc.
- Unicode escape sequences (\uXXXX)
- International characters
- Empty strings

```json
{
  "escaped": "Line1\nLine2\tTabbed",
  "quoted": "She said \"Hello\"",
  "unicode": "\u0048\u0065\u006c\u006c\u006f",
  "international": "你好世界 [EARTH]"
}
```

### 4. Number Handling
- Positive and negative integers
- Floating-point numbers
- Scientific notation (positive and negative exponents)
- Zero in various formats
- Large numbers (64-bit range)

```json
{
  "integer": 42,
  "negative": -100,
  "float": 3.14159,
  "scientific": 1.23e-4,
  "large": 9223372036854775807
}
```

### 5. File Upload & Parsing
- JSON files from user's filesystem
- File size validation (default 10MB limit)
- UTF-8 encoding validation
- Progress handling for large files

### 6. Error Detection & Reporting
- Leading zeros in numbers
- Trailing commas in arrays/objects
- Duplicate object keys
- Unterminated strings
- Invalid escape sequences
- Missing required punctuation (colons, commas)
- Unexpected tokens

**Error Response Example:**
```json
{
  "error": "Trailing comma in array",
  "line": 3,
  "column": 12,
  "valid": false
}
```

### 7. Edge Cases & Validation
- Empty nested structures
- Whitespace-only arrays/objects
- Mixed whitespace (spaces, tabs, newlines)
- Comments detection (not allowed in JSON)
- Invalid number formats
- Non-string keys in objects

## Testing

Two lanes, following the repository's test policy:

```bash
pip install -r requirements-dev.txt

# Gate tests: deterministic, local, under two seconds. The pre-commit hook runs them.
python3 -m unittest discover -s resources/tests
node --test site/logic.test.js static/engine/lib.test.js
git config core.hooksPath .githooks          # enable the hook once per clone

# Coverage (CI fails below 100% line and branch coverage of engines/ and app.py)
python3 -m coverage run --branch --source=engines,app -m unittest discover -s resources/tests
python3 -m coverage report --fail-under=100

# Periodic evals: healing corpus, compression, compile checks, throughput
python3 evals/run_evals.py                   # or: run_evals.py healing types
PORT=5055 python3 app.py & node evals/ui_smoke.mjs http://127.0.0.1:5055   # headless Chrome, 30 checks
```

### Test Coverage

- **Core** (`test_core_instrumentation.py`): token capture, AST invariants on every fixture (ids, parent links, spans, and every AST path resolving through JSONPath), trace balance, duplicate recording, error positions, strictness fixes.
- **Healing** (`test_healing.py`): every repair kind on a case table, the audit trail, unhealable input, and a generalization test that damages every valid fixture and heals it back to the original value.
- **Types** (`test_types.py`): IR rules, sample and schema inference, all three renderers. When the toolchains are present, generated Pydantic models validate the document they came from and generated Go is checked against `gofmt`.
- **Auditor** (`test_auditor.py`), **JSONPath** (`test_query.py`, Goessner bookstore and RFC 9535 semantics), **compressor** (`test_compressor.py`, including 400 seeded random round trips), **document cache** (`test_documents.py`), and **every API route** (`test_api.py`).
- **Evals** (`evals/run_evals.py`): 27/27 healing corpus cases, 100% compact round trips, `tsc --strict`, `go vet` and Pydantic validation over every fixture, throughput thresholds. `evals/calibrate_tokens.py` refits the token estimator against tiktoken.

## Configuration

Parser limits live in `engines/core.py`; request and visualization limits in `app.py`; audit thresholds in `engines/auditor.py`:

```python
MAX_RECURSION_DEPTH = 1000         # engines/core.py: max nesting depth (enforced without RecursionError)
MIN_NUMBER, MAX_NUMBER             # engines/core.py: 64-bit integer bounds
MAX_FILE_SIZE = 10 * 1024 * 1024   # app.py: 10MB request limit
VISUAL_LIMIT_BYTES = 512 * 1024    # app.py: tokens/trace/AST only below this size
TOKEN_LIMIT = TRACE_LIMIT = AST_NODE_LIMIT = 20_000
DEPTH_WARNING, DEPTH_CRITICAL = 25, 64   # engines/auditor.py
```

## Error Handling Examples

### Invalid JSON Detected
```
JSONParseError at line 2, column 15: Trailing comma in array
JSONParseError at line 3, column 8: Expected ':' after object key
JSONParseError at line 1, column 1: Leading zeros not allowed in numbers
```

### File Issues
```
"File size exceeds maximum of 10MB"
"File is not valid UTF-8 encoded"
"No JSON data or file provided"
```

## Deployment

The browser edition is hosted on GitHub Pages. The [Python edition](https://json-parser-python.onrender.com/python/) is hosted on Render using `render.yaml` and Gunicorn. The previous Heroku app URL is no longer live.

## Architecture

All parsing and analysis lives in the `engines/` package, which has no Flask imports. `app.py` only reads requests, enforces limits and shapes responses. The dashboard is `templates/engine.html` plus `static/engine/`, with its pure logic in `lib.js`, which is unit tested. See [ARCHITECTURE.md](ARCHITECTURE.md) for the design, the data flow between modules, and known failure modes.

- **`Lexer`**: tokenizes JSON with strict escape, Unicode and number validation (ASCII digits only; `\uXXXX` needs four hex digits; surrogate pairs combine). Optional token capture.
- **`Parser`**: recursive descent with depth limits, duplicate-key policy, error positions at the offending token, and optional AST/trace/observer instrumentation.
- **Routes**: `GET /engine/`, `/python/`, `/terminal/`, plus the six `POST` endpoints above.

## Contributing

Contributions are welcome! Areas for improvement:
- JSON Schema *validation* (the type generator reads schemas but does not validate documents against them)
- JSON streaming for very large files
- Performance optimization for extremely large documents (see the numbers below)

Please open an issue or submit a pull request.

## Performance Notes

Measured by `evals/run_evals.py performance` on a 3.2 MB document (Apple Silicon, CPython 3.14):

- **Plain parse**: 3 to 5 MB/s across runs, linear in input size. A 100 KB document parses in 20 to 30 ms. The C-accelerated stdlib `json` is roughly 50x faster; this parser optimizes for diagnostics, not raw speed.
- **Fully instrumented parse** (tokens + AST + trace): 5 to 7x slower, which is why visualization is limited to 512 KB documents.
- **Deep nesting**: the documented 1000-level limit is enforced with a clean error. Before this change, Python's recursion limit failed at about 500 levels with a 500 response.

## Security Considerations

- **Input validation**: All inputs are strictly validated
- **Resource limits**: File size and recursion depth bounded
- **Encoding validation**: UTF-8 encoding verified
- **Type safety**: Full type hints for better static analysis

## License

This project is licensed under the MIT License. See the LICENSE file for details.

## Acknowledgments

Thanks to [John Crickett](https://www.linkedin.com/in/johncrickett/) for the [JSON Parser Challenge](https://codingchallenges.fyi/challenges/challenge-json-parser)

## Resources

- [JSON Specification](https://www.json.org/json-en.html)
- [RFC 7159 - JSON Data Interchange Format](https://tools.ietf.org/html/rfc7159)
- [Python Type Hints Guide](https://docs.python.org/3/library/typing.html)

