import sys

class JSONLexerError(Exception):
    pass
class JSONParser:
    def parse(self, json_str):
        if not json_str.strip():  # Check if JSON string is empty or contains only whitespace
            print("Invalid JSON: Empty input")
            sys.exit(1)
        
        lexer = JSONLexer(json_str)
        try:
            token = lexer.get_next_token()
            if token.type == 'LBRACE':
                while True:
                    token = lexer.get_next_token()
                    if token.type == 'STRING':
                        key = token.value
                        token = lexer.get_next_token()
                        if token.type == 'COLON':
                            token = lexer.get_next_token()
                            if token.type == 'STRING':
                                # Key-value pair found
                                token = lexer.get_next_token()
                                if token.type == 'RBRACE':
                                    token = lexer.get_next_token()
                                    if token.type == 'EOF':
                                        print("Valid JSON")
                                        sys.exit(0)
                                    elif token.type == 'COMMA':
                                        continue
                                    else:
                                        print("Invalid JSON: Unexpected token after object")
                                        sys.exit(1)
                                elif token.type == 'COMMA':
                                    token = lexer.get_next_token()
                                    if token.type == 'STRING':
                                        continue
                                    else:
                                        print("Invalid JSON: Expected string key after comma")
                                        sys.exit(1)
                                else:
                                    print("Invalid JSON: Expected '}' or ',' after value")
                                    sys.exit(1)
                            else:
                                print("Invalid JSON: Expected string value after ':'")
                                sys.exit(1)
                        else:
                            print("Invalid JSON: Expected ':' after key")
                            sys.exit(1)
                    elif token.type == 'RBRACE':
                        if lexer.get_next_token().type == 'EOF':
                            print("Valid JSON")
                            sys.exit(0)
                        else:
                            print("Invalid JSON: Unexpected token after object")
                            sys.exit(1)
                    else:
                        print("Invalid JSON: Expected string key or '}'")
                        sys.exit(1)
            else:
                print("Invalid JSON: Expected '{'")
                sys.exit(1)
        except JSONLexerError as e:
            print("Invalid JSON:", e)
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

