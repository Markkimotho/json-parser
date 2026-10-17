# JSON Parser - Architecture & Implementation Guide

## Overview

This document provides detailed information about the architecture, design decisions, and best practices for the robust JSON parser.

## Architecture Overview

```
┌─────────────────┐
│   Input JSON    │
└────────┬────────┘
         │
         ▼
    ┌─────────┐
    │ Lexer   │ ─── Tokenization (STRING, NUMBER, LBRACE, etc.)
    └────┬────┘
         │
         ▼
    ┌─────────┐
    │ Parser  │ ─── Recursive descent parsing
    └────┬────┘
         │
         ▼
  ┌──────────────┐
  │ Python Object│ (dict, list, str, int, float, bool, None)
  └──────────────┘
```

## Developer Engine (`engines/`)

The parser moved from `app.py` into `engines/core.py`; `app.py` re-exports `Lexer`, `Parser`, `JSONParseError` and `TokenType` for existing callers. Six ecosystem modules sit on the core. None of them imports Flask.

```
                 ┌────────────── engines/ ───────────────────────────────────────┐
 editor text ──► │ core.Lexer ──tokens──► core.Parser ──value──► types / query /  │
                 │   capture_tokens        capture_ast, trace     compressor      │
                 │                         duplicate_keys="record"                │
                 │                         observer(ScalarEvent) ──► auditor      │
                 │ healing: lenient lexer ─► repair parser ─► core.Parser (verify)│
                 │ paths.path_child: one path format for AST, audit, JSONPath     │
                 │ documents.DocumentCache: SHA-256 → parsed value (LRU)          │
                 └────────────────────────────────────────────────────────────────┘
                         ▲ JSON in/out
 app.py (routes, limits) ┘◄──── fetch ──── static/engine/engine.js (+ lib.js, pure)
```

### Design decisions

1. **Instrumentation is opt-in and adds little cost when off.** Token capture, AST building, tracing, path tracking and observers each sit behind a boolean. The uninstrumented path also got faster: whitespace and plain string runs are matched with one regex each instead of character by character.
2. **The AST is built with an explicit node stack**, not by changing return values. `parse_object`/`parse_array` keep returning plain Python values, so `parser.parse()` is unchanged for every existing caller. Nodes point to their parent by `id`, so the tree serializes without cycles.
3. **One path format everywhere** (`engines/paths.py`). AST nodes, audit findings, duplicate-key reports and JSONPath matches all format paths with `path_child`. A gate test asserts that every AST path resolves through the JSONPath engine to exactly that node.
4. **The auditor reads source, not values.** Duplicate keys and the exact digits of `9007199254740993` are lost once a document becomes a Python `dict`. The auditor runs the core parser with `duplicate_keys="record"` and an observer that receives raw number text. The dashboard audits the *original* editor text first, so duplicates that healing would remove are still reported; it falls back to the healed text only when the original has syntax errors.
5. **Healing emits pieces, not regex substitutions.** Regex rewriting corrupts string contents, for example a `//` inside a URL or a `'` inside a word. The lenient lexer understands strings, so every repair applies at token level. Output is a list of pieces (each token's original whitespace plus its text), so removing an earlier duplicate member is just blanking a range. Each repair is recorded with its source position, and the strict parser re-verifies the result.
6. **Ambiguous damage is resolved by balance, not guesswork.** At a closer of the wrong kind (`[{"a": 1], …]`), the repairer checks which reading leaves the rest of the document balanced, using one suffix sum computed up front. That decides between "this `]` was a typo for `}`" and "the object is missing its `}`".
7. **Types go through an IR.** Sample inference and JSON Schema both produce `Prim | ArrayOf | MapOf | Named | Enum | UnionOf` plus object definitions. Renderers only print IR, so the optional/nullable/union rules are written once. Python output is ordered children-first, and `model_rebuild()` is emitted only for real reference cycles.
8. **The compact LLM format was chosen by measurement** (`evals/calibrate_tokens.py`, tiktoken cl100k/o200k): two-space indentation with `": "` saved 5% against minified JSON; one-space indentation with `":"` saves 13%. `decode_compact` exists so losslessness is proven by round-trip tests rather than assumed.
9. **Recursion headroom instead of an iterative rewrite.** Each nesting level costs two Python frames, so the documented 1000-level limit used to fail at about 500 with `RecursionError`. `ensure_recursion_headroom` raises the interpreter limit, monotonically and only as far as needed. Since Python 3.11, Python-to-Python calls do not use the C stack, so this is safe. JSONPath descendant search and the auditor are iterative.

### Known limits and failure modes

- **Throughput** is 3 to 5 MB/s plain and 5 to 7x slower fully instrumented, hence the 512 KB visualization limit and the 20,000-entry caps on tokens, trace and AST nodes. Above the caps the API returns flags; it does not truncate silently.
- **Healing is heuristic.** It fixes the patterns in the case table and the eval corpus. It cannot recover intent that was never written down: `'it's'` with an unescaped apostrophe, or two words with no separator. When it cannot produce valid JSON it says so (`valid: false` with the strict error) and never returns broken output as success. Numbers JSON cannot hold (`1e999`) are reported, not clamped.
- **Lenient labels.** `word: {…}` with nothing after the container is read as a label in front of the document (LLM output like `Output: {...}`). With a following `, key: …` it is read as a brace-less object.
- **Sample type inference only knows what it saw**: an array that is empty in the sample becomes `unknown[]`/`List[Any]`/`[]any`. Schema mode supports local `$ref` only (`#/$defs/...`, `#/definitions/...`, `#`) and raises `SchemaError` for remote refs.
- **JSONPath `match()`/`search()`** use Python `re`, which cannot time out. Patterns over 200 characters, nested quantifiers and backreferences are rejected, and subjects are truncated to 10,000 characters to bound backtracking.
- **The token estimator** has about 7% mean error and up to 40% on texts under 30 tokens. Exact counts depend on the target model's tokenizer.
- **The document cache** is per process. Under gunicorn with several workers a query may miss and re-parse, which costs time but never gives a wrong result.
- **Offsets.** The engine reports code-point offsets; browsers index UTF-16. `static/engine/lib.js#offsetMapper` converts them, so selection is correct for emoji and other astral characters.

## Component Details

### 1. Lexer (Tokenizer)

**Purpose**: Convert raw JSON text into meaningful tokens

**Key Responsibilities**:

- Skip whitespace efficiently
- Parse strings with full escape sequence support
- Parse numbers with proper validation
- Recognize keywords (true, false, null)
- Track line/column position for error reporting

**Key Methods**:

```python
def next_token() -> Tuple[str, Any]
```

Returns the next token and its value.

**Escape Sequence Handling**:

- Standard escapes: `\n`, `\t`, `\r`, `\b`, `\f`, `\"`, `\\`, `\/`
- Unicode escapes: `\uXXXX` (converts to actual character)
- Validation: Rejects invalid escape sequences

**Number Parsing**:

- Integer part: Must have digits (no leading zeros except standalone "0")
- Fractional part: Optional, requires digits after decimal
- Exponent part: Optional, allows ±, requires digits

**Example Valid Numbers**:

- `0`, `123`, `-456`
- `1.5`, `-3.14`, `0.001`
- `1e5`, `1.5e-3`, `-2.5E+10`

**Example Invalid Numbers**:

- `01` (leading zero)
- `1.` (missing fractional digits)
- `1e` (missing exponent digits)

### 2. Parser

**Purpose**: Convert token stream into Python objects

**Key Responsibilities**:

- Implement recursive descent parsing
- Validate JSON structure rules
- Track recursion depth to prevent stack overflow
- Provide detailed error messages with context

**Parsing Functions**:

```python
def parse() -> Any                    # Parse complete JSON value
def value() -> Any                    # Parse any JSON value
def object() -> Dict[str, Any]        # Parse { ... }
def array() -> List[Any]              # Parse [ ... ]
def string() -> str                   # Parse string
def number() -> Union[int, float]     # Parse number
```

**Validation Rules**:

- Object keys must be strings
- Objects cannot have duplicate keys
- No trailing commas in arrays or objects
- Colons required after object keys
- Commas required between values
- Final token must be EOF (no extra content)

### 3. Error Handling

**JSONParseError Exception**:

```python
class JSONParseError(Exception):
    def __init__(self, message: str, line: int = 0, column: int = 0):
        self.message = message
        self.line = line
        self.column = column
```

**Error Examples**:

- Syntax errors: "Expected ':', after object key"
- Validation errors: "Duplicate key 'id' in object"
- Format errors: "Invalid escape sequence: \x"
- Range errors: "Maximum nesting depth (1000) exceeded"

**Error Response Format** (JSON API):

```json
{
    "error": "Trailing comma in array",
    "line": 3,
    "column": 12,
    "valid": false
}
```

## Design Decisions

### 1. Recursive Descent Parsing

**Why?**

- Simple to understand and implement
- Easy to debug
- Natural fit for JSON's structure
- Good error reporting with context

**How?**

- One function per grammar rule
- Calls lower-level functions to parse sub-expressions
- Tracks current token and advances when consumed

### 2. Depth Limiting

**Configuration**:

```python
MAX_RECURSION_DEPTH = 1000  # Prevent stack overflow
```

**Implementation**:

- Track `current_depth` in parser
- Increment on entering object/array
- Decrement on exiting
- Raise error if limit exceeded

**Rationale**: Prevents stack overflow on pathologically nested JSON

### 3. Proper Number Handling

**Why Custom Implementation?**

- Need to validate format (no leading zeros)
- Need to support scientific notation
- Need to distinguish int from float
- Python's int/float can handle edge cases

**Approach**:

1. Parse string representation
2. Validate format with regex-like logic
3. Convert to int or float
4. Validate range (64-bit bounds)

### 4. Escape Sequence Handling

**Why Full Support?**

- JSON spec requires all escape sequences
- Unicode support essential for internationalization
- Many real-world APIs use these features

**Implementation**:

- Detect backslash
- Read next character
- Map to escaped character
- For \uXXXX, read 4 hex digits, convert to Unicode

### 5. Position Tracking

**Why Track Line/Column?**

- Users need to know where error occurred
- Helpful for debugging large JSON files
- Industry standard in language tooling

**Implementation**:

- Update on every character consumed
- Reset column at newline
- Increment both for regular characters

## Performance Characteristics

### Time Complexity

- Single-pass tokenization: `O(n)` where n = string length
- Single-pass parsing: `O(n)`
- Total: `O(n)`

### Space Complexity

- Token buffer: `O(1)` (only current token stored)
- Output object: `O(n)` (must store all parsed data)
- Call stack: `O(d)` where d = nesting depth

### Optimization Opportunities

1. **Streaming Parser**: For large files, don't build complete object
2. **Lazy Evaluation**: Parse values only when accessed
3. **Memory Pool**: Reuse token objects
4. **SIMD**: Use SIMD for string scanning

## Testing Strategy

### Test Categories

1. **Lexer Tests** (13 tests)

   - Basic tokens: {}, [], :, ,
   - Strings: escapes, unicode, empty
   - Numbers: int, float, exponent, invalid formats
   - Keywords: true, false, null
   - Whitespace variations
2. **Parser Tests** (21 tests)

   - Empty structures
   - Simple objects and arrays
   - Nested structures
   - Mixed data types
   - Error detection: trailing commas, duplicate keys, missing colons
   - Recursion depth limits
   - Large number handling
3. **Complex Scenarios** (6 tests)

   - Full JSON documents
   - Arrays of objects
   - Unicode content
   - Whitespace variations
   - Edge cases: empty strings, zero values

### Coverage

- **38 test cases** total
- **100% pass rate**
- Covers both happy path and error cases
- Real-world JSON samples included

## Best Practices for JSON Parsing

### Do's [OK]

1. **Validate input size** before processing
2. **Use depth limits** for nested structures
3. **Provide detailed errors** with position info
4. **Sanitize string escapes** on output
5. **Type-hint** all functions
6. **Test edge cases** explicitly
7. **Document assumptions** about number range
8. **Catch/handle all exceptions**

### Don'ts [ERROR]

1. **Don't use eval()** or exec() for parsing
2. **Don't assume input format** without validation
3. **Don't process unbounded input**
4. **Don't ignore escape sequences**
5. **Don't use weak type checking**
6. **Don't hardcode limits** - make configurable
7. **Don't skip whitespace**
8. **Don't assume UTF-8** - validate encoding

## Scalability Considerations

### Handling Large Files

**Current Approach**:

- File size limit: 10MB (configurable)
- Prevents DoS from huge files

**For Larger Files**:

1. **Implement streaming parser**:

   ```python
   class StreamingParser:
       def parse_values(self) -> Iterator[Any]:
           """Yield values as they're parsed"""
   ```
2. **Add pagination**:

   ```python
   def parse_page(size: int, offset: int) -> List[Any]:
       """Parse specific portion of file"""
   ```
3. **Implement SAX-style parsing**:

   ```python
   class SAXParser:
       def on_object_start(self): pass
       def on_key(self, key: str): pass
       def on_value(self, value: Any): pass
   ```

### Handling Deep Nesting

**Current Approach**:

- Depth limit: 1000 (configurable)
- Prevents stack overflow

**For Deeper Nesting**:

1. **Implement iterative parsing** with explicit stack
2. **Use trampoline pattern** for tail calls
3. **Increase available stack** (platform-dependent)

### Handling Many Keys

**Current Approach**:

- No specific optimization

**Optimizations**:

1. **Use interning** for duplicate keys:

   ```python
   keys = {}  # key -> interned_key
   ```
2. **Use hash arrays** for large objects
3. **Implement key indexing** for fast lookup

## API Design

### Parse from String

```python
json_str = '{"key": "value"}'
lexer = Lexer(json_str)
parser = Parser(lexer)
result = parser.parse()
```

### Parse from File

```python
with open('data.json', 'rb') as f:
    content = f.read().decode('utf-8')
    lexer = Lexer(content)
    parser = Parser(lexer)
    result = parser.parse()
```

### Web API

```
POST /parse-json
Content-Type: application/x-www-form-urlencoded

jsonData={...}
# or
jsonFile=@data.json

Response:
{
    "result": {...},
    "source": "form" | "file",
    "valid": true
}
```

## Future Enhancements

Already delivered by the Developer Engine: JSON Schema reading (types), pretty printing, parse-time and depth stats, comment handling (healing), and result caching.

1. **Schema Validation**: validate documents against a JSON Schema, not just generate types from one
2. **Streaming Support**: handle arbitrarily large files
3. **Incremental Parsing**: re-parse only the edited region for live compile on large documents
4. **Iterative core parser**: remove the recursion-limit dependency entirely

## References

- [JSON Specification](https://www.json.org/)
- [RFC 7159](https://tools.ietf.org/html/rfc7159)
- [Unicode Standard](https://www.unicode.org/)
- [Python AST Module](https://docs.python.org/3/library/ast.html)
