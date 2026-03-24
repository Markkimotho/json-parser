# JSON Parser - Robust & Scalable

## Overview

A production-grade **JSON Parser** with comprehensive support for all JSON parsing use cases. Built with robustness, scalability, and error handling as first-class concerns. Features a web interface and a powerful underlying parser implementation with extensive test coverage.

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
- **pytest**: Comprehensive test suite (38+ test cases)
- **Heroku**: Deployment platform

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

5. **Access the App**: Open your web browser and navigate to http://127.0.0.1:5001/

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

Run the comprehensive test suite:

```bash
# Run all tests with verbose output
python -m pytest tests.py -v

# Run specific test class
python -m pytest tests.py::TestLexer -v

# Run with coverage
python -m pytest tests.py --cov=app
```

### Test Coverage

The test suite includes **38+ test cases** covering:
- **13 Lexer tests**: Tokenization, escape sequences, unicode, numbers
- **21 Parser tests**: Object/array parsing, nesting, validation, error handling  
- **6 Complex scenario tests**: Real-world JSON, unicode content, whitespace

All tests pass: [OK] 38/38

## Configuration

Edit `app.py` to adjust limits:

```python
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_RECURSION_DEPTH = 1000         # Max nesting depth
MIN_NUMBER = -9223372036854775808  # 64-bit min
MAX_NUMBER = 9223372036854775807   # 64-bit max
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

This application is deployed on Heroku and can be accessed at:
[JSON Parser](https://json-parser-py-e1ec55614d20.herokuapp.com/)

### Deploy Your Own
```bash
# Using Heroku CLI
heroku create your-app-name
git push heroku main
```

## Architecture

### Lexer (`Lexer` class)
- **Tokenization**: Converts JSON text into tokens
- **Escape handling**: Processes all escape sequences including unicode
- **Number validation**: Validates proper number formats
- **Error reporting**: Tracks line/column for error messages

### Parser (`Parser` class)
- **Recursive descent**: Processes tokens into Python objects
- **Depth tracking**: Prevents stack overflow
- **Validation**: Enforces JSON rules (no duplicate keys, trailing commas)
- **Error handling**: Provides detailed context for failures

### API (`Flask` routes)
- **GET `/`**: Web interface
- **POST `/parse-json`**: JSON parsing endpoint
  - Accepts form data (`jsonData`) or file upload (`jsonFile`)
  - Returns parsed result or detailed error message

## Contributing

Contributions are welcome! Areas for improvement:
- Schema validation
- JSON streaming for very large files
- Minification/formatting options
- Performance optimization for extremely large documents

Please open an issue or submit a pull request.

## Performance Notes

- **Small documents** (<1MB): Parsed in <10ms
- **Medium documents** (1-10MB): Linear time complexity
- **Large documents**: File size limits protect against memory exhaustion
- **Deep nesting**: Recursion depth limits prevent stack overflow

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

