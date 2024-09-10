import os
from flask import Flask, render_template, request, jsonify # type: ignore
import json

app = Flask(__name__)

# Token types for the lexer
class TokenType:
    LBRACE = 'LBRACE'  # {
    RBRACE = 'RBRACE'  # }
    LBRACKET = 'LBRACKET'  # [
    RBRACKET = 'RBRACKET'  # ]
    COLON = 'COLON'  # :
    COMMA = 'COMMA'  # ,
    STRING = 'STRING'  # "..."
    NUMBER = 'NUMBER'  # 123
    TRUE = 'TRUE'  # true
    FALSE = 'FALSE'  # false
    NULL = 'NULL'  # null
    EOF = 'EOF'  # End of file

# Lexer class
class Lexer:
    def __init__(self, input_string):
        self.input = input_string
        self.pos = 0

    def next_token(self):
        while self.pos < len(self.input):
            char = self.input[self.pos]
            if char.isspace():
                self.pos += 1
                continue
            if char == '{':
                self.pos += 1
                return TokenType.LBRACE, '{'
            if char == '}':
                self.pos += 1
                return TokenType.RBRACE, '}'
            if char == '[':
                self.pos += 1
                return TokenType.LBRACKET, '['
            if char == ']':
                self.pos += 1
                return TokenType.RBRACKET, ']'
            if char == ':':
                self.pos += 1
                return TokenType.COLON, ':'
            if char == ',':
                self.pos += 1
                return TokenType.COMMA, ','
            if char == '"':
                start = self.pos + 1
                self.pos += 1
                while self.input[self.pos] != '"':
                    self.pos += 1
                self.pos += 1
                return TokenType.STRING, self.input[start:self.pos-1]
            if char.isdigit() or (char == '-' and self.input[self.pos + 1].isdigit()):
                start = self.pos
                while self.pos < len(self.input) and (self.input[self.pos].isdigit() or self.input[self.pos] in '.-'):
                    self.pos += 1
                return TokenType.NUMBER, self.input[start:self.pos]
            if self.input.startswith('true', self.pos):
                self.pos += 4
                return TokenType.TRUE, True
            if self.input.startswith('false', self.pos):
                self.pos += 5
                return TokenType.FALSE, False
            if self.input.startswith('null', self.pos):
                self.pos += 4
                return TokenType.NULL, None
            raise Exception(f"Invalid character: {char}")
        return TokenType.EOF, None

# Parser class
class Parser:
    def __init__(self, lexer):
        self.lexer = lexer
        self.current_token = lexer.next_token()

    def parse(self):
        return self.value()

    def value(self):
        if self.current_token[0] == TokenType.LBRACE:
            return self.object()
        elif self.current_token[0] == TokenType.LBRACKET:
            return self.array()
        elif self.current_token[0] == TokenType.STRING:
            return self.string()
        elif self.current_token[0] == TokenType.NUMBER:
            return self.number()
        elif self.current_token[0] == TokenType.TRUE:
            self.current_token = self.lexer.next_token()
            return True
        elif self.current_token[0] == TokenType.FALSE:
            self.current_token = self.lexer.next_token()
            return False
        elif self.current_token[0] == TokenType.NULL:
            self.current_token = self.lexer.next_token()
            return None
        raise Exception("Invalid JSON value")

    def object(self):
        obj = {}
        self.current_token = self.lexer.next_token()  # Consume '{'
        while self.current_token[0] != TokenType.RBRACE:
            key = self.string()
            if self.current_token[0] != TokenType.COLON:
                raise Exception("Expected ':'")
            self.current_token = self.lexer.next_token()  # Consume ':'
            value = self.value()
            obj[key] = value
            
            if self.current_token[0] == TokenType.COMMA:
                self.current_token = self.lexer.next_token()  # Consume ','
            elif self.current_token[0] != TokenType.RBRACE:
                raise Exception("Expected ',' or '}'")
        
        self.current_token = self.lexer.next_token()  # Consume '}'
        return obj

    def array(self):
        arr = []
        self.current_token = self.lexer.next_token()  # Consume '['
        while self.current_token[0] != TokenType.RBRACKET:
            value = self.value()
            arr.append(value)
            
            if self.current_token[0] == TokenType.COMMA:
                self.current_token = self.lexer.next_token()  # Consume ','
            elif self.current_token[0] != TokenType.RBRACKET:
                raise Exception("Expected ',' or ']'")
        
        self.current_token = self.lexer.next_token()  # Consume ']'
        return arr

    def string(self):
        if self.current_token[0] == TokenType.STRING:
            value = self.current_token[1]
            self.current_token = self.lexer.next_token()  # Consume string
            return value
        raise Exception("Expected string")
    
    def number(self):
        if self.current_token[0] == TokenType.NUMBER:
            value = float(self.current_token[1]) if '.' in self.current_token[1] else int(self.current_token[1])
            self.current_token = self.lexer.next_token()  # Consume number
            return value
        raise Exception("Expected number")

# Route for the home page
@app.route('/')
def index():
    return render_template('index.html')

# Route for parsing JSON
@app.route('/parse-json', methods=['POST'])
def parse_json():
    if 'jsonData' in request.form:
        json_data = request.form['jsonData']
    elif 'jsonFile' in request.files:
        json_file = request.files['jsonFile']
        json_data = json_file.read().decode('utf-8')

    lexer = Lexer(json_data)
    parser = Parser(lexer)

    try:
        parsed_data = parser.parse()
        return jsonify(result=parsed_data)
    except Exception as e:
        return jsonify(error=str(e)), 400

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)