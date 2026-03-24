import os
from flask import Flask, render_template, request, jsonify # type: ignore
import json
from typing import Any, Tuple, Dict, List, Optional
import re

app = Flask(__name__)

# Configuration constants
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_RECURSION_DEPTH = 1000
MIN_NUMBER = -9223372036854775808  # 64-bit min
MAX_NUMBER = 9223372036854775807   # 64-bit max

# Token types for the lexer
class TokenType:
    """Token type enumeration for JSON lexer"""
    LBRACE = 'LBRACE'      # {
    RBRACE = 'RBRACE'      # }
    LBRACKET = 'LBRACKET'  # [
    RBRACKET = 'RBRACKET'  # ]
    COLON = 'COLON'        # :
    COMMA = 'COMMA'        # ,
    STRING = 'STRING'      # "..."
    NUMBER = 'NUMBER'      # 123, 1.5, 1e5
    TRUE = 'TRUE'          # true
    FALSE = 'FALSE'        # false
    NULL = 'NULL'          # null
    EOF = 'EOF'            # End of file

class JSONParseError(Exception):
    """Custom exception for JSON parsing errors with detailed context"""
    def __init__(self, message: str, line: int = 0, column: int = 0):
        self.message = message
        self.line = line
        self.column = column
        super().__init__(f"JSONParseError at line {line}, column {column}: {message}")

# Lexer class
class Lexer:
    """Tokenizes JSON input with proper escape sequence and number handling"""
    
    def __init__(self, input_string: str):
        self.input = input_string
        self.pos = 0
        self.line = 1
        self.column = 1
    
    def _advance(self) -> None:
        """Move position forward, tracking line and column"""
        if self.pos < len(self.input):
            if self.input[self.pos] == '\n':
                self.line += 1
                self.column = 1
            else:
                self.column += 1
            self.pos += 1
    
    def _peek(self, offset: int = 0) -> Optional[str]:
        """Peek at character without consuming it"""
        pos = self.pos + offset
        if pos < len(self.input):
            return self.input[pos]
        return None
    
    def _skip_whitespace(self) -> None:
        """Skip all whitespace characters"""
        while self.pos < len(self.input) and self.input[self.pos] in ' \t\n\r':
            self._advance()
    
    def _parse_string(self) -> str:
        """Parse a JSON string with proper escape sequence handling"""
        result = []
        self._advance()  # Skip opening quote
        
        while self.pos < len(self.input):
            char = self.input[self.pos]
            
            if char == '"':
                self._advance()
                return ''.join(result)
            elif char == '\\':
                self._advance()
                if self.pos >= len(self.input):
                    raise JSONParseError("Unterminated string escape", self.line, self.column)
                
                escape_char = self.input[self.pos]
                if escape_char == '"':
                    result.append('"')
                elif escape_char == '\\':
                    result.append('\\')
                elif escape_char == '/':
                    result.append('/')
                elif escape_char == 'b':
                    result.append('\b')
                elif escape_char == 'f':
                    result.append('\f')
                elif escape_char == 'n':
                    result.append('\n')
                elif escape_char == 'r':
                    result.append('\r')
                elif escape_char == 't':
                    result.append('\t')
                elif escape_char == 'u':
                    # Unicode escape: \uXXXX
                    self._advance()
                    hex_digits = ''
                    for _ in range(4):
                        if self.pos >= len(self.input):
                            raise JSONParseError("Invalid unicode escape", self.line, self.column)
                        hex_digits += self.input[self.pos]
                        self._advance()
                    try:
                        code_point = int(hex_digits, 16)
                        result.append(chr(code_point))
                        continue
                    except ValueError:
                        raise JSONParseError("Invalid unicode escape sequence", self.line, self.column)
                else:
                    raise JSONParseError(f"Invalid escape sequence: \\{escape_char}", self.line, self.column)
                self._advance()
            elif ord(char) < 0x20:
                raise JSONParseError(f"Unescaped control character in string", self.line, self.column)
            else:
                result.append(char)
                self._advance()
        
        raise JSONParseError("Unterminated string", self.line, self.column)
    
    def _parse_number(self) -> str:
        """Parse a JSON number with proper validation"""
        start_pos = self.pos
        
        # Optional minus sign
        if self._peek() == '-':
            self._advance()
        
        # Integer part
        if self._peek() == '0':
            self._advance()
            # Leading zeros not allowed (except lone 0)
            if self._peek() and self._peek().isdigit():
                raise JSONParseError("Leading zeros not allowed in numbers", self.line, self.column)
        elif self._peek() and self._peek().isdigit():
            while self._peek() and self._peek().isdigit():
                self._advance()
        else:
            raise JSONParseError("Invalid number format", self.line, self.column)
        
        # Fractional part
        if self._peek() == '.':
            self._advance()
            if not self._peek() or not self._peek().isdigit():
                raise JSONParseError("Invalid number: digit expected after decimal point", self.line, self.column)
            while self._peek() and self._peek().isdigit():
                self._advance()
        
        # Exponent part
        if self._peek() in ('e', 'E'):
            self._advance()
            if self._peek() in ('+', '-'):
                self._advance()
            if not self._peek() or not self._peek().isdigit():
                raise JSONParseError("Invalid number: digit expected in exponent", self.line, self.column)
            while self._peek() and self._peek().isdigit():
                self._advance()
        
        return self.input[start_pos:self.pos]
    
    def next_token(self) -> Tuple[str, Any]:
        """Get the next token from the input"""
        self._skip_whitespace()
        
        if self.pos >= len(self.input):
            return TokenType.EOF, None
        
        char = self.input[self.pos]
        
        # Single-character tokens
        if char == '{':
            self._advance()
            return TokenType.LBRACE, '{'
        if char == '}':
            self._advance()
            return TokenType.RBRACE, '}'
        if char == '[':
            self._advance()
            return TokenType.LBRACKET, '['
        if char == ']':
            self._advance()
            return TokenType.RBRACKET, ']'
        if char == ':':
            self._advance()
            return TokenType.COLON, ':'
        if char == ',':
            self._advance()
            return TokenType.COMMA, ','
        
        # String
        if char == '"':
            return TokenType.STRING, self._parse_string()
        
        # Number
        if char == '-' or char.isdigit():
            return TokenType.NUMBER, self._parse_number()
        
        # Keywords
        if self.input[self.pos:self.pos+4] == 'true':
            self.pos += 4
            self.column += 4
            return TokenType.TRUE, True
        if self.input[self.pos:self.pos+5] == 'false':
            self.pos += 5
            self.column += 5
            return TokenType.FALSE, False
        if self.input[self.pos:self.pos+4] == 'null':
            self.pos += 4
            self.column += 4
            return TokenType.NULL, None
        
        raise JSONParseError(f"Unexpected character '{char}'", self.line, self.column)

# Parser class
class Parser:
    """Parses JSON tokens with validation and error handling"""
    
    def __init__(self, lexer: Lexer, max_depth: int = MAX_RECURSION_DEPTH):
        self.lexer = lexer
        self.current_token: Tuple[str, Any] = lexer.next_token()
        self.max_depth = max_depth
        self.current_depth = 0
    
    def _check_depth(self) -> None:
        """Ensure we don't exceed max recursion depth"""
        if self.current_depth >= self.max_depth:
            raise JSONParseError(
                f"Maximum nesting depth ({self.max_depth}) exceeded",
                self.lexer.line,
                self.lexer.column
            )
    
    def parse(self) -> Any:
        """Parse the JSON input and return the result"""
        result = self.value()
        if self.current_token[0] != TokenType.EOF:
            raise JSONParseError(
                "Unexpected token after JSON value",
                self.lexer.line,
                self.lexer.column
            )
        return result
    
    def value(self) -> Any:
        """Parse any JSON value"""
        token_type = self.current_token[0]
        
        if token_type == TokenType.LBRACE:
            return self.object()
        elif token_type == TokenType.LBRACKET:
            return self.array()
        elif token_type == TokenType.STRING:
            return self.string()
        elif token_type == TokenType.NUMBER:
            return self.number()
        elif token_type == TokenType.TRUE:
            self.current_token = self.lexer.next_token()
            return True
        elif token_type == TokenType.FALSE:
            self.current_token = self.lexer.next_token()
            return False
        elif token_type == TokenType.NULL:
            self.current_token = self.lexer.next_token()
            return None
        
        raise JSONParseError(
            f"Expected JSON value, got {token_type}",
            self.lexer.line,
            self.lexer.column
        )
    
    def object(self) -> Dict[str, Any]:
        """Parse a JSON object"""
        self._check_depth()
        self.current_depth += 1
        
        obj = {}
        self.current_token = self.lexer.next_token()  # Consume '{'
        
        # Empty object
        if self.current_token[0] == TokenType.RBRACE:
            self.current_token = self.lexer.next_token()
            self.current_depth -= 1
            return obj
        
        while True:
            # Key must be a string
            if self.current_token[0] != TokenType.STRING:
                raise JSONParseError(
                    "Expected string key in object",
                    self.lexer.line,
                    self.lexer.column
                )
            key = self.current_token[1]
            self.current_token = self.lexer.next_token()
            
            # Colon after key
            if self.current_token[0] != TokenType.COLON:
                raise JSONParseError(
                    "Expected ':' after object key",
                    self.lexer.line,
                    self.lexer.column
                )
            self.current_token = self.lexer.next_token()  # Consume ':'
            
            # Value
            if key in obj:
                raise JSONParseError(
                    f"Duplicate key '{key}' in object",
                    self.lexer.line,
                    self.lexer.column
                )
            obj[key] = self.value()
            
            # Comma or closing brace
            if self.current_token[0] == TokenType.COMMA:
                self.current_token = self.lexer.next_token()
                # Check for trailing comma
                if self.current_token[0] == TokenType.RBRACE:
                    raise JSONParseError(
                        "Trailing comma in object",
                        self.lexer.line,
                        self.lexer.column
                    )
            elif self.current_token[0] == TokenType.RBRACE:
                break
            else:
                raise JSONParseError(
                    "Expected ',' or '}' in object",
                    self.lexer.line,
                    self.lexer.column
                )
        
        self.current_token = self.lexer.next_token()  # Consume '}'
        self.current_depth -= 1
        return obj
    
    def array(self) -> List[Any]:
        """Parse a JSON array"""
        self._check_depth()
        self.current_depth += 1
        
        arr = []
        self.current_token = self.lexer.next_token()  # Consume '['
        
        # Empty array
        if self.current_token[0] == TokenType.RBRACKET:
            self.current_token = self.lexer.next_token()
            self.current_depth -= 1
            return arr
        
        while True:
            arr.append(self.value())
            
            # Comma or closing bracket
            if self.current_token[0] == TokenType.COMMA:
                self.current_token = self.lexer.next_token()
                # Check for trailing comma
                if self.current_token[0] == TokenType.RBRACKET:
                    raise JSONParseError(
                        "Trailing comma in array",
                        self.lexer.line,
                        self.lexer.column
                    )
            elif self.current_token[0] == TokenType.RBRACKET:
                break
            else:
                raise JSONParseError(
                    "Expected ',' or ']' in array",
                    self.lexer.line,
                    self.lexer.column
                )
        
        self.current_token = self.lexer.next_token()  # Consume ']'
        self.current_depth -= 1
        return arr
    
    def string(self) -> str:
        """Parse a string value"""
        if self.current_token[0] == TokenType.STRING:
            value = self.current_token[1]
            self.current_token = self.lexer.next_token()
            return value
        raise JSONParseError("Expected string", self.lexer.line, self.lexer.column)
    
    def number(self) -> float | int:
        """Parse a number value"""
        if self.current_token[0] == TokenType.NUMBER:
            num_str = self.current_token[1]
            try:
                # Try to parse as integer first
                if '.' not in num_str and 'e' not in num_str.lower():
                    value = int(num_str)
                else:
                    value = float(num_str)
                
                # Validate against reasonable limits
                if isinstance(value, int) and (value < MIN_NUMBER or value > MAX_NUMBER):
                    raise JSONParseError(f"Number out of range: {num_str}", self.lexer.line, self.lexer.column)
                
                self.current_token = self.lexer.next_token()
                return value
            except ValueError:
                raise JSONParseError(f"Invalid number: {num_str}", self.lexer.line, self.lexer.column)
        raise JSONParseError("Expected number", self.lexer.line, self.lexer.column)

# Route for the home page
@app.route('/')
def index():
    return render_template('index.html')

# Route for parsing JSON
@app.route('/parse-json', methods=['POST'])
def parse_json():
    """Parse JSON from form data or file upload with comprehensive error handling"""
    try:
        json_data = None
        source = None
        
        print(f"[PARSE] Form keys: {list(request.form.keys())}, File keys: {list(request.files.keys())}")
        
        # Get JSON from form data
        if 'jsonData' in request.form:
            json_data = request.form['jsonData']
            source = "form"
        # Get JSON from file upload
        elif 'jsonFile' in request.files:
            json_file = request.files['jsonFile']
            
            # Validate file
            if not json_file.filename:
                return jsonify(error="No file selected"), 400
            
            print(f"[PARSE] File: {json_file.filename}")
            
            # Read file content first, then check size
            file_content = json_file.read()
            file_size = len(file_content)
            
            print(f"[PARSE] File size: {file_size} bytes")
            
            if file_size == 0:
                return jsonify(error="File is empty"), 400
            
            if file_size > MAX_FILE_SIZE:
                return jsonify(error=f"File size exceeds maximum of {MAX_FILE_SIZE / 1024 / 1024:.0f}MB"), 400
            
            # Decode file content
            try:
                json_data = file_content.decode('utf-8')
                source = "file"
                print(f"[PARSE] Decoded {len(json_data)} chars from file")
            except UnicodeDecodeError:
                return jsonify(error="File is not valid UTF-8 encoded"), 400
        else:
            print(f"[PARSE] ERROR: No jsonData or jsonFile found")
            return jsonify(error="No JSON data or file provided"), 400
        
        # Validate input
        if not json_data or not json_data.strip():
            print(f"[PARSE] ERROR: json_data is empty")
            return jsonify(error="JSON input is empty"), 400
        
        print(f"[PARSE] Parsing {len(json_data)} chars from {source}")
        
        # Parse JSON
        lexer = Lexer(json_data.strip())
        parser = Parser(lexer, max_depth=MAX_RECURSION_DEPTH)
        
        parsed_data = parser.parse()
        
        print(f"[PARSE] Success!")
        
        return jsonify(
            result=parsed_data,
            source=source,
            valid=True
        )
    
    except JSONParseError as e:
        print(f"[PARSE] JSONParseError: {e.message}")
        return jsonify(
            error=e.message,
            line=e.line,
            column=e.column,
            valid=False
        ), 400
    except Exception as e:
        print(f"[PARSE] Exception: {str(e)}")
        return jsonify(error=f"Unexpected error: {str(e)}", valid=False), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port)
