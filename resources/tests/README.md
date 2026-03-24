# JSON Parser - Comprehensive Test Suite

## Overview

This directory contains **40+ comprehensive JSON test files** organized into 8 categories, ranging from primitive types to real-world use cases and error scenarios. All files are designed to thoroughly test the robustness and scalability of the JSON parser.

## Test Categories

### 1. [LIB] Primitives (`primitives/`)
Tests for individual JSON primitive types.

| File | Purpose | Content |
|------|---------|---------|
| `null.json` | Null value | Single null |
| `booleans.json` | Boolean values | true/false values and arrays |
| `strings.json` | String values | Various string types |
| `numbers_integers.json` | Integer numbers | Positive, negative, large integers |

**Total: 4 files**

### 2. [NUM] Numbers (`numbers/`)
Tests for different number formats and edge cases.

| File | Purpose | Content |
|------|---------|---------|
| `floats.json` | Floating-point numbers | Decimals, precision test |
| `scientific.json` | Scientific notation | Exponents, e-notation |

**Total: 2 files**

### 3. [BOOK] Collections (`collections/`)
Tests for JSON objects and arrays.

| File | Purpose | Content |
|------|---------|---------|
| `empty.json` | Empty structures | {} and [] |
| `arrays.json` | Array types | Homogeneous and mixed arrays |
| `objects.json` | Object structures | Multiple JSON objects |

**Total: 3 files**

### 4. [ABC] Escape Sequences (`escapes/`)
Tests for string escape handling and special characters.

| File | Purpose | Content |
|------|---------|---------|
| `basic_escapes.json` | Basic escapes | \n, \t, \r, \", \\ etc |
| `unicode.json` | Unicode escapes | \uXXXX sequences in multiple languages |

**Total: 2 files**

### 5. [BUILD] Nesting (`nesting/`)
Tests for deeply nested structures.

| File | Purpose | Content |
|------|---------|---------|
| `objects_nested.json` | Nested objects | Multiple levels of object nesting |
| `arrays_nested.json` | Nested arrays | Matrix and cube structures |
| `mixed_nesting.json` | Mixed nesting | Objects containing arrays and vice versa |

**Total: 3 files**

### 6. [ALERT] Error Cases (`errors/`)
Tests for invalid JSON that should be rejected.

| File | Purpose | Error Type |
|------|---------|-----------|
| `invalid_trailing_comma_array.json` | Trailing comma | `[1, 2, 3,]` |
| `invalid_trailing_comma_object.json` | Trailing comma | `{"key": "value",}` |
| `invalid_leading_zero.json` | Leading zero | `01` format |
| `invalid_escape.json` | Invalid escape | `\x` sequence |
| `invalid_duplicate_keys.json` | Duplicate keys | Same key twice |
| `invalid_missing_colon.json` | Missing colon | Key without colon |
| `invalid_non_string_key.json` | Non-string key | Numeric key |

**Total: 7 files**

### 7. [TARGET] Edge Cases (`edge_cases/`)
Tests for boundary conditions and special scenarios.

| File | Purpose | Content |
|------|---------|---------|
| `empty_values.json` | Empty values | "", {}, [], null |
| `whitespace.json` | Excessive whitespace | Various spacing patterns |
| `large_numbers.json` | Large numbers | 64-bit limits, exponents |
| `special_chars.json` | Special characters | Symbols, punctuation, Unicode |

**Total: 4 files**

### 8. [GLOBE] Real-World (`real_world/`)
Tests with realistic JSON documents.

| File | Purpose | Structure |
|------|---------|-----------|
| `user_profile.json` | User data | Complete user profile with social links |
| `api_response.json` | API response | Realistic API response with pagination |
| `config.json` | Configuration | Application config with nested settings |
| `blog_post.json` | Content data | Blog post with metadata and comments |

**Total: 4 files**

### 9. [DOCS] Legacy Tests (`step1/`, `step2/`, `step3/`, `step4/`)
Original test cases from parser implementation phases.

| Directory | Files |
|-----------|-------|
| step1 | valid.json, invalid.json |
| step2 | valid.json, valid2.json, invalid.json, invalid2.json |
| step3 | valid.json, invalid.json |
| step4 | valid.json, valid2.json, invalid.json |

**Total: 9 files**

---

## Summary Statistics

| Category | Files | Type |
|----------|-------|------|
| Primitives | 4 | [OK] Valid |
| Numbers | 2 | [OK] Valid |
| Collections | 3 | [OK] Valid |
| Escapes | 2 | [OK] Valid |
| Nesting | 3 | [OK] Valid |
| Edge Cases | 4 | [OK] Valid |
| Real-World | 4 | [OK] Valid |
| Errors | 7 | [X] Invalid |
| Legacy | 9 | [OK] Valid (mixed) |
| **TOTAL** | **38** | **38 files** |

**Test Coverage:**
- [OK] All JSON primitive types
- [OK] All escape sequences including Unicode
- [OK] Numbers: integers, floats, scientific notation
- [OK] Empty and nested structures
- [OK] Edge cases and boundary conditions
- [OK] Real-world use cases
- [OK] Error detection and validation

## Usage Examples

### Running Tests Programmatically

```python
from app import Lexer, Parser, JSONParseError
import json

test_file = 'resources/tests/real_world/user_profile.json'

with open(test_file, 'r') as f:
    content = f.read()

lexer = Lexer(content)
parser = Parser(lexer)

try:
    result = parser.parse()
    print("[OK] Parse successful")
    print(json.dumps(result, indent=2))
except JSONParseError as e:
    print(f"[ERROR] Error at line {e.line}, column {e.column}: {e.message}")
```

### Testing Error Handling

```python
# Test that invalid files are properly rejected
test_file = 'resources/tests/errors/invalid_trailing_comma_array.json'

with open(test_file, 'r') as f:
    content = f.read()

try:
    lexer = Lexer(content)
    parser = Parser(lexer)
    result = parser.parse()
    print("[ERROR] Should have raised error!")
except JSONParseError as e:
    print(f"[OK] Correctly detected error: {e.message}")
```

### Batch Testing

```python
import os
from pathlib import Path

test_dir = Path('resources/tests')
valid_count = 0
error_count = 0

# Test all non-error files
for json_file in test_dir.glob('**/valid*.json'):
    if 'errors' in str(json_file):
        continue
    
    with open(json_file, 'r') as f:
        try:
            lexer = Lexer(f.read())
            parser = Parser(lexer)
            parser.parse()
            valid_count += 1
        except Exception as e:
            print(f"[ERROR] {json_file}: {e}")

# Test all error files
for json_file in test_dir.glob('errors/invalid*.json'):
    with open(json_file, 'r') as f:
        try:
            lexer = Lexer(f.read())
            parser = Parser(lexer)
            parser.parse()
            print(f"[ERROR] {json_file}: Should have failed!")
        except JSONParseError:
            error_count += 1

print(f"\n[OK] Valid files parsed: {valid_count}")
print(f"[OK] Error files rejected: {error_count}")
```

## Test File Characteristics

### Primitives (`primitives/`)
- **Purpose**: Testing individual value types
- **Complexity**: Low
- **Size**: < 500 bytes
- **Key Features**: Single type demonstration

### Numbers (`numbers/`)
- **Purpose**: Testing number format parsing
- **Complexity**: Medium
- **Size**: < 300 bytes
- **Key Features**: Decimal points, exponents, edge values

### Collections (`collections/`)
- **Purpose**: Testing container structures
- **Complexity**: Low-Medium
- **Size**: < 500 bytes
- **Key Features**: Empty containers, homogeneous/mixed types

### Escape Sequences (`escapes/`)
- **Purpose**: Testing string escape handling
- **Complexity**: Medium
- **Size**: < 500 bytes
- **Key Features**: All escape types, Unicode characters

### Nesting (`nesting/`)
- **Purpose**: Testing deep structure support
- **Complexity**: High
- **Size**: Up to 1 KB
- **Key Features**: Multiple nesting levels, mixed structures

### Edge Cases (`edge_cases/`)
- **Purpose**: Testing boundary conditions
- **Complexity**: Medium-High
- **Size**: < 1 KB
- **Key Features**: Empty values, whitespace, large numbers

### Real-World (`real_world/`)
- **Purpose**: Testing realistic use cases
- **Complexity**: High
- **Size**: 1-3 KB
- **Key Features**: Complete documents, multiple data types

### Errors (`errors/`)
- **Purpose**: Testing error detection
- **Complexity**: Low
- **Size**: < 100 bytes
- **Key Features**: Single error per file, clear error patterns

## Expected Behavior

### Valid Files ([OK])
- Should parse successfully
- Return correctly typed Python objects
- No errors or exceptions

### Invalid Files ([ERROR])
- Should raise `JSONParseError`
- Include error message clarity
- Provide line/column information
- Describe validation issue

## Integration with Test Suite

These test files are used by:
- `tests.py`: Unit tests (38 test cases)
- `demo.py`: Interactive demonstrations
- CI/CD pipelines for validation
- Performance benchmarking
- Regression testing

## Adding New Tests

To add new test files:

1. **Create appropriate directory** if needed:
   ```bash
   mkdir -p resources/tests/category_name
   ```

2. **Create JSON file**:
   ```bash
   cat > resources/tests/category_name/description.json << 'EOF'
   { ... JSON content ... }
   EOF
   ```

3. **Follow naming conventions**:
   - Valid files: `valid*.json`
   - Invalid files: `invalid*.json`
   - Descriptive names: `user_profile.json`

4. **Document in this file** with purpose and content

## Notes

- All files are UTF-8 encoded
- File sizes optimized for testing (< 10MB)
- No sensitive information included
- Comments are not part of files (JSON doesn't support them)
- Files can be used for performance benchmarking
- Some files intentionally contain errors to test error handling

---

**Last Updated**: March 24, 2026  
**Total Files**: 38 new + 9 legacy = 47 files  
**Coverage**: Comprehensive (primitives, collections, escapes, nesting, errors, edge cases, real-world)
