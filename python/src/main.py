import sys

class JSONParser:
    def parse(self, json_str):
        if not json_str.strip():  # Check if JSON string is empty or contains only whitespace
            print("Invalid JSON: Empty input")
            sys.exit(1)
        
        lexer = JSONLexer(json_str)
        try:
            token = lexer.get_next_token()
            if token.type == 'LBRACE':
                self.parse_object(lexer)
            else:
                print("Invalid JSON: Expected '{'")
                sys.exit(1)
        except JSONLexerError as e:
            print("Invalid JSON:", e)
            sys.exit(1)

    def parse_object(self, lexer):
        token = lexer.get_next_token()
        while token.type != 'RBRACE':
            if token.type == 'STRING':
                key = token.value
                token = lexer.get_next_token()
                if token.type == 'COLON':
                    token = lexer.get_next_token()
                    if token.type in ['STRING', 'NUMBER', 'BOOLEAN', 'NULL']:
                        # Key-value pair found
                        token = lexer.get_next_token()
                        if token.type == 'RBRACE':
                            print("Valid JSON")
                            sys.exit(0)
                        elif token.type == 'COMMA':
                            token = lexer.get_next_token()
                            if token.type == 'STRING':
                                continue
                            else:
                                print("Invalid JSON: Expected string key after ','")
                                sys.exit(1)
                        else:
                            print("Invalid JSON: Expected '}' or ',' after value")
                            sys.exit(1)
                    elif token.type == 'LBRACE':
                        # Nested object
                        self.parse_object(lexer)
                        token = lexer.get_next_token()
                    elif token.type == 'LBRACKET':
                        # Array
                        self.parse_array(lexer)
                        token = lexer.get_next_token()
                    else:
                        print("Invalid JSON: Expected string, number, boolean, or null value after ':'")
                        sys.exit(1)
                else:
                    print("Invalid JSON: Expected ':' after key")
                    sys.exit(1)
            else:
                print("Invalid JSON: Expected string key")
                sys.exit(1)

    def parse_array(self, lexer):
        token = lexer.get_next_token()
        while token.type != 'RBRACKET':
            if token.type in ['STRING', 'NUMBER', 'BOOLEAN', 'NULL']:
                token = lexer.get_next_token()
                if token.type == 'RBRACKET':
                    print("Valid JSON")
                    sys.exit(0)
                elif token.type == 'COMMA':
                    token = lexer.get_next_token()
                    if token.type in ['STRING', 'NUMBER', 'BOOLEAN', 'NULL']:
                        continue
                    else:
                        print("Invalid JSON: Expected value after ','")
                        sys.exit(1)
                else:
                    print("Invalid JSON: Expected ']' or ',' after value")
                    sys.exit(1)
            elif token.type == 'LBRACE':
                # Nested object
                self.parse_object(lexer)
                token = lexer.get_next_token()
            elif token.type == 'LBRACKET':
                # Nested array
                self.parse_array(lexer)
                token = lexer.get_next_token()
            else:
                print("Invalid JSON: Expected string, number, boolean, null, object, or array value")
                sys.exit(1)

class JSONLexer:
    def __init__(self, text):
        self.text = text
        self.pos = 0
        self.current_char = self.text[self.pos]

    def get_next_token(self):
        while self.pos < len(self.text):
            if self.current_char.isspace():
                self.skip_whitespace()
                continue

            if self.current_char == '{':
                self.advance()
                return Token('LBRACE')
            elif self.current_char == '}':
                self.advance()
                return Token('RBRACE')
            elif self.current_char == ':':
                self.advance()
                return Token('COLON')
            elif self.current_char == ',':
                self.advance()
                return Token('COMMA')
            elif self.current_char == '"':
                return self.read_string()
            elif self.current_char.isdigit() or self.current_char == '-':
                return self.read_number()
            elif self.current_char == 't' or self.current_char == 'f':
                return self.read_boolean()
            elif self.current_char == 'n':
                return self.read_null()
            elif self.current_char == '[':
                self.advance()
                return Token('LBRACKET')
            elif self.current_char == ']':
                self.advance()
                return Token('RBRACKET')
            else:
                self.error()

        return Token('EOF')

    def read_string(self):
        start_pos = self.pos + 1
        self.advance()
        while self.current_char != '"':
            self.advance()
        value = self.text[start_pos:self.pos]
        self.advance()
        return Token('STRING', value)

    def read_number(self):
        start_pos = self.pos
        while self.current_char.isdigit() or self.current_char == '.':
            self.advance()
        value = self.text[start_pos:self.pos]
        return Token('NUMBER', float(value) if '.' in value else int(value))

    def read_boolean(self):
        start_pos = self.pos
        while self.current_char.isalpha():
            self.advance()
        value = self.text[start_pos:self.pos]
        if value == 'true':
            return Token('BOOLEAN', True)
        elif value == 'false':
            return Token('BOOLEAN', False)
        else:
            self.error()

    def read_null(self):
        start_pos = self.pos
        while self.current_char.isalpha():
            self.advance()
        value = self.text[start_pos:self.pos]
        if value == 'null':
            return Token('NULL')
        else:
            self.error()

    def skip_whitespace(self):
        while self.current_char and self.current_char.isspace():
            self.advance()

    def advance(self):
        self.pos += 1
        if self.pos < len(self.text):
            self.current_char = self.text[self.pos]
        else:
            self.current_char = None

    def error(self):
        raise JSONLexerError('Invalid character')

class Token:
    def __init__(self, type, value=None):
        self.type = type
        self.value = value

class JSONLexerError(Exception):
    pass

def read_json_file(file_path):
    try:
        with open(file_path, 'r') as file:
            return file.read()
    except FileNotFoundError:
        print("File not found:", file_path)
        sys.exit(1)

def main():
    if len(sys.argv) != 2:
        print("Usage: python main.py <file_path>")
        sys.exit(1)

    file_path = sys.argv[1]
    json_data = read_json_file(file_path)
    parser = JSONParser()
    parser.parse(json_data)

if __name__ == "__main__":
    main()

