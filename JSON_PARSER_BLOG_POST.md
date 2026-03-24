---
layout: post
title: "Building a Production-Grade JSON Parser from Scratch"
date: 2026-03-25 14:30:00
description: A robust, scalable JSON parser written in Python with comprehensive error handling, full JSON spec support, and a web interface for real-time validation.
tags: projects json parser parsing open-source python flask
categories: projects
giscus_comments: true
related_posts: true
---

I wanted to understand how parsing actually works. Not the high-level concepts, but the mechanics — how text becomes structured data. How a tokenizer identifies meaningful chunks. How a parser builds objects from those chunks. How errors get detected and reported.

Most developers use the built-in `json` module without thinking about it. I wanted to know what was happening under the hood.

## Building It

The goal was simple: understand tokenization, understand recursive descent parsing, understand the flow from raw text to Python objects. Everything else was secondary.

I built it to:
- Learn how state machines work through a lexer
- Understand recursive descent parsing by implementing it
- See how position tracking enables useful error messages
- Handle the edge cases that make parsing tricky (escape sequences, number formats, nesting depth)
- Do it all in a single pass through the input

Not to build the best parser. Not to optimize it. Just to understand.

## The Architecture

The parser is split into two distinct phases: lexical analysis (the Lexer) and syntax analysis (the Parser).

### Lexer: Tokenization

The Lexer's job is to convert a stream of characters into a stream of meaningful tokens. It reads the input sequentially and maintains a position pointer. At each position, it determines what type of token should be extracted.

**The tokenization process:**

1. Skip whitespace (spaces, tabs, newlines, carriage returns). Track line and column as you skip newlines.
2. Read the next character. Determine what it is:
   - Structural tokens: `{`, `}`, `[`, `]`, `:`, `,` are single-character tokens. Return immediately.
   - Literals: `true`, `false`, `null` are keyword tokens. Read ahead to verify the complete keyword.
   - Strings: See a `"`. Read until the closing `"`, processing escape sequences along the way.
   - Numbers: See a digit or minus sign. Read the complete number, validating format rules.
3. Return the token type and value.

**String handling within the lexer is subtle.** When you encounter a backslash inside a string, you must check the next character:
- `\n` becomes a newline character
- `\t` becomes a tab character
- `\"` becomes a quote character
- `\\` becomes a backslash character
- `\u` followed by exactly 4 hex digits becomes a Unicode character

If you see `\x`, that's invalid — the lexer raises an error at that exact position. If you reach EOF before closing the string, error. If a newline appears inside a string (not escaped), error.

**Number validation is precise.** The JSON spec has specific rules:
- A number starts with an optional minus sign
- Then comes a digit sequence for the integer part
  - `0` is valid by itself
  - `01` is invalid (no leading zeros except for `0`)
  - `123` is valid
- Then optionally a decimal point followed by at least one digit (`1.5`, `0.001`)
  - `1.` is invalid (no trailing decimal)
  - `.5` is invalid (no leading digit before decimal)
- Then optionally an exponent: `e` or `E`, optional `+` or `-`, at least one digit
  - `1e5` is valid
  - `1e+5` is valid
  - `1e` is invalid (no exponent digits)
  - `1e+` is invalid (no exponent digits)

The lexer validates all these rules as it reads. A malformed number is caught immediately.

**Position tracking happens for every character.** The lexer maintains `line` and `column` counters. When you read a regular character, increment `column`. When you read a newline, increment `line` and reset `column` to 0. This is why errors can report exact locations.

### Parser: Recursive Descent

Once the Lexer produces tokens, the Parser consumes them and builds Python objects according to the JSON grammar.

**The grammar (simplified):**
```
JSON      ::= value
value     ::= object | array | string | number | true | false | null
object    ::= '{' (pair (',' pair)*)? '}'
pair      ::= string ':' value
array     ::= '[' (value (',' value)*)? ']'
string    ::= STRING_TOKEN
number    ::= NUMBER_TOKEN
```

**The implementation uses one function per grammar rule.** Here's the conceptual structure:

```python
def parse():
    # Entry point. Parse a complete JSON value.
    result = self.value()
    # Must be at EOF now. If there's extra tokens, error.
    if self.current_token != EOF:
        raise JSONParseError("Unexpected token after value")
    return result

def value():
    # Try to parse any valid JSON value.
    if matches('{'):
        return self.object()
    elif matches('['):
        return self.array()
    elif matches(STRING):
        return self.string()
    elif matches(NUMBER):
        return self.number()
    elif matches('true'):
        return True
    elif matches('false'):
        return False
    elif matches('null'):
        return None
    else:
        raise JSONParseError("Expected a JSON value")

def object():
    # Parse { pair, pair, ... }
    consume('{')
    result = {}
    if not matches('}'):
        # At least one pair
        while True:
            key = self.string()  # Key must be a string
            consume(':')
            val = self.value()   # Value can be anything
            
            # Check for duplicate keys
            if key in result:
                raise JSONParseError(f"Duplicate key '{key}'")
            result[key] = val
            
            if not matches(','):
                break
            consume(',')
            # Check for trailing comma
            if matches('}'):
                raise JSONParseError("Trailing comma in object")
    consume('}')
    return result

def array():
    # Parse [ value, value, ... ]
    consume('[')
    result = []
    if not matches(']'):
        # At least one value
        while True:
            result.append(self.value())
            if not matches(','):
                break
            consume(',')
            # Check for trailing comma
            if matches(']'):
                raise JSONParseError("Trailing comma in array")
    consume(']')
    return result
```

The key insight: each function reads tokens, calls other functions for sub-expressions, checks for valid structure, and returns the built object.

**Depth tracking prevents stack overflow.** The parser maintains a `current_depth` counter:
- Increment when entering an object or array
- Decrement when exiting
- Raise an error if depth exceeds the limit (default 1000)

This guards against malicious or accidental deeply nested structures that would exhaust the call stack.

**Error reporting includes context.** When parsing fails, the error includes:
- The error message (what was wrong)
- The line number (from the lexer's position tracking)
- The column number (from the lexer's position tracking)
- The token that caused the failure

This makes debugging large JSON files practical.

### The Flow: From Text to Object

Let's trace an example: `{"name": "Alice", "age": 30}`

**Lexer output (tokens):**
```
{ (LBRACE)
"name" (STRING: "name")
: (COLON)
"Alice" (STRING: "Alice")
, (COMMA)
"age" (STRING: "age")
: (COLON)
30 (NUMBER: 30)
} (RBRACE)
```

**Parser execution:**
1. `parse()` calls `value()`
2. `value()` sees `{`, calls `object()`
3. `object()` consumes `{`
4. Sees `"name"` (not `}`), so enter the pair loop
5. `string()` returns `"name"`
6. Consumes `:`
7. `value()` returns `"Alice"`
8. Add to result: `{"name": "Alice"}`
9. Sees `,`, consume it
10. Loop continues
11. `string()` returns `"age"`
12. Consumes `:`
13. `value()` returns `30`
14. Add to result: `{"name": "Alice", "age": 30}`
15. Sees `}` (not `,`), exit loop
16. Consumes `}`
17. Return `{"name": "Alice", "age": 30}`
18. Back in `parse()`, check if at EOF. Yes. Return result.

**Python object:** `{'name': 'Alice', 'age': 30}`

### Error Cases

**Example 1: Duplicate key**

Input: `{"key": 1, "key": 2}`

Parser reaches the second `"key"`. In the `object()` function's loop, it checks `if key in result:` and finds it already exists. Raises `JSONParseError("Duplicate key 'key'")`

**Example 2: Trailing comma**

Input: `[1, 2, 3,]`

Parser in `array()` loop. After parsing `3`, sees `,` and consumes it. Next iteration, `value()` checks if next token is `]`. It is. But the rule says after a comma there must be another value, not the closing bracket. Raises `JSONParseError("Trailing comma in array")`

**Example 3: Invalid escape**

Input: `"hello\xworld"`

Lexer in string parsing. Sees `\`, checks next character. It's `x`, which is not a valid escape character. Raises `JSONParseError("Invalid escape sequence: \x")` at the exact location of the `\x`.

**Example 4: Leading zero**

Input: `{"count": 01}`

Lexer reading the number. Sees `0`. Checks next character. It's `1`, not `.` or `e` or a structural token. This means `01` which violates the no-leading-zeros rule. Raises `JSONParseError("Invalid number format")`

## What It Supports

The parser handles all JSON according to spec:

- **Primitives**: null, booleans, numbers, strings
- **Collections**: objects and arrays, with arbitrary nesting
- **Numbers**: integers, floats, scientific notation (1e5, 1.5e-3)
- **Escapes**: All standard escape sequences including `\uXXXX` for Unicode
- **Validation**: Detects leading zeros, duplicate keys, invalid escapes, trailing commas
- **Limits**: Recursion depth limiting and file size validation to prevent pathological cases

The interesting part was not the feature list but *how* it works.

## What I Learned

**Tokenization is meticulous.** Splitting text into tokens seems simple until you try it. You need to handle:
- Whitespace across lines (tracking line/column for every character)
- Strings with embedded escapes (checking that `\uXXXX` is exactly 4 hex digits, not `\u000G`)
- Numbers with optional components (the integer part is required, but decimal and exponent are optional)
- Keywords that must be exact (it's `null`, not `nul` or `NULL`)

One edge case I didn't expect: what counts as whitespace? JSON allows `\t`, `\n`, `\r`, and space. But not all Unicode whitespace. The spec is specific. If you're lenient, you silently accept invalid JSON.

**Recursive descent parsing is beautifully self-documenting.** The code *is* the grammar. When someone reads `def object():` they see how objects are structured without referring to documentation. The recursive calls (`key = self.string()`, `val = self.value()`) showed the exact nesting. But it requires discipline: every grammar rule must be a function, and every function must follow the same pattern: consume expected tokens, handle the payload, validate structure.

**Position tracking is not optional.** I first built the parser without it. Errors said "invalid JSON" somewhere. Useless. Adding line/column tracking meant modifying every `consume()` call, every token-reading operation, and maintaining `line`/`column` during whitespace skipping. But then errors became "line 42, column 5: duplicate key". That's the difference between "something is wrong" and "here's the problem."

**Duplicate key detection needs O(n) lookups.** For each pair added to an object, checking if the key already exists requires looking it up. With n pairs, that's n lookups, each O(1) in a dict. Total O(n) which is acceptable. But I had to decide: should the parser allow duplicates and let the caller decide? Or reject them? The JSON spec says objects are unordered collections of key-value pairs, implying uniqueness. I made the decision to reject duplicates immediately.

**Number parsing is surprisingly complex for such a simple-looking problem.** Python's `int()` and `float()` handle many formats, but the JSON spec is strict. `01` is invalid. `1.` is invalid. `e5` is invalid. You could try parsing and catching exceptions, but that mixes concerns. Better to validate the format before parsing, so you can report the exact error.

**The call stack is a resource.** Recursive descent parsing builds a call stack as deep as the nesting depth. For JSON with objects containing arrays containing objects containing arrays... the stack grows. The default recursion limit in Python is ~1000. A pathological JSON file with 10000 levels of nesting would crash. But most real-world JSON is shallower. Still, tracking depth and failing fast when a limit is hit is more graceful than a stack overflow.

**Trailing commas are a special case.** They're syntactically simple to detect: after you parse a value and consume a comma, check if the next token is the closing bracket/brace. If so, error. If not, continue parsing the next value. But I had to remember to check in both arrays and objects. A helper function for this would be cleaner, but the duplication made the logic explicit.

**Tests revealed assumptions I made wrong.** Building the parser, I assumed certain things about valid JSON:
- Empty objects `{}` are valid (yes)
- Empty arrays `[]` are valid (yes)
- Objects can have zero pairs (yes, the optional part of the grammar)
- Whitespace before/after values doesn't matter (yes, and the lexer must skip it)
- `null` is different from empty string `""` (yes, very different)

Each assumption got tested. Most were correct. The few that weren't got fixed. That iterative refinement is where the real learning happened.

## Validation

I tested it thoroughly to make sure it actually works. 38 unit tests cover the lexer (tokenization), the parser (grammar and structure validation), and error handling. Another 40 JSON test files across different categories ensure edge cases are handled.

All tests pass. The parser correctly accepts all valid JSON and correctly rejects all invalid JSON.

Test coverage includes:
- Lexer: All token types, escape sequences, number formats, whitespace
- Parser: Objects, arrays, nesting, mixed types, error cases
- Edge cases: Deep nesting, large numbers, special characters, trailing commas, duplicate keys

The testing revealed the gaps in my thinking. A test would fail, I'd add handling for that case, and suddenly the parser got more robust. That's the learning in action.

## Wrap Up

I understand parsing now. Not as a black box. Not as "the JSON library handles it." But as a concrete process: read characters, group them into tokens, use grammar rules to build structure, validate as you go.

The parser works. It's about 400 lines of Python. It handles the JSON spec. It's tested.

But the real point was the understanding. Once you build one, you see parsing everywhere. Configuration files, markup languages, DSLs — they all follow the same pattern. Tokenize, parse, validate.

The code is on GitHub if you want to see it. But more importantly: if you want to understand parsing, consider building one yourself. Don't use an existing parser. Build it. You'll learn more from the failures and edge cases than from reading about it.
