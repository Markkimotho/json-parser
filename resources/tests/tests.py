"""
Comprehensive test suite for JSON parser
Tests all edge cases and use cases for robustness and scalability
"""

import unittest
from app import Lexer, Parser, JSONParseError, TokenType

class TestLexer(unittest.TestCase):
    """Test lexer tokenization"""
    
    def test_empty_object(self):
        """Test empty object parsing"""
        lexer = Lexer('{}')
        tokens = []
        while True:
            token = lexer.next_token()
            tokens.append(token[0])
            if token[0] == TokenType.EOF:
                break
        self.assertEqual(tokens, [TokenType.LBRACE, TokenType.RBRACE, TokenType.EOF])
    
    def test_empty_array(self):
        """Test empty array parsing"""
        lexer = Lexer('[]')
        tokens = []
        while True:
            token = lexer.next_token()
            tokens.append(token[0])
            if token[0] == TokenType.EOF:
                break
        self.assertEqual(tokens, [TokenType.LBRACKET, TokenType.RBRACKET, TokenType.EOF])
    
    def test_string_with_escapes(self):
        """Test string parsing with escape sequences"""
        test_cases = [
            (r'"\""', '"'),
            (r'"\\"', '\\'),
            (r'"\n"', '\n'),
            (r'"\t"', '\t'),
            (r'"\r"', '\r'),
            (r'"\b"', '\b'),
            (r'"\f"', '\f'),
            (r'"\/"', '/'),
        ]
        for input_str, expected in test_cases:
            lexer = Lexer(input_str)
            token = lexer.next_token()
            self.assertEqual(token[1], expected, f"Failed for {input_str}")
    
    def test_unicode_escape(self):
        """Test unicode escape sequences"""
        lexer = Lexer(r'"\u0041"')  # "A"
        token = lexer.next_token()
        self.assertEqual(token[1], 'A')
        
        lexer = Lexer(r'"\u00e9"')  # "é"
        token = lexer.next_token()
        self.assertEqual(token[1], 'é')
    
    def test_number_integer(self):
        """Test integer number parsing"""
        test_cases = ['0', '123', '-456', '9999999999']
        for num_str in test_cases:
            lexer = Lexer(num_str)
            token = lexer.next_token()
            self.assertEqual(token[0], TokenType.NUMBER)
    
    def test_number_float(self):
        """Test float number parsing"""
        test_cases = ['1.5', '-3.14', '0.001', '123.456']
        for num_str in test_cases:
            lexer = Lexer(num_str)
            token = lexer.next_token()
            self.assertEqual(token[0], TokenType.NUMBER)
    
    def test_number_exponent(self):
        """Test number with exponent parsing"""
        test_cases = ['1e5', '1E5', '1.5e-3', '-2.5E+10']
        for num_str in test_cases:
            lexer = Lexer(num_str)
            token = lexer.next_token()
            self.assertEqual(token[0], TokenType.NUMBER)
    
    def test_invalid_escape(self):
        """Test invalid escape sequences"""
        with self.assertRaises(JSONParseError):
            lexer = Lexer(r'"\x"')
            lexer.next_token()
    
    def test_leading_zeros(self):
        """Test that leading zeros are rejected"""
        with self.assertRaises(JSONParseError):
            lexer = Lexer('01')
            lexer.next_token()
    
    def test_invalid_number_missing_digit_after_dot(self):
        """Test invalid number with missing digit after decimal"""
        with self.assertRaises(JSONParseError):
            lexer = Lexer('1.')
            lexer.next_token()
    
    def test_invalid_number_missing_digit_in_exponent(self):
        """Test invalid number with missing digit in exponent"""
        with self.assertRaises(JSONParseError):
            lexer = Lexer('1e')
            lexer.next_token()
    
    def test_keywords(self):
        """Test keyword tokenization"""
        lexer = Lexer('true')
        token = lexer.next_token()
        self.assertEqual(token[0], TokenType.TRUE)
        
        lexer = Lexer('false')
        token = lexer.next_token()
        self.assertEqual(token[0], TokenType.FALSE)
        
        lexer = Lexer('null')
        token = lexer.next_token()
        self.assertEqual(token[0], TokenType.NULL)
    
    def test_whitespace_handling(self):
        """Test that whitespace is properly skipped"""
        lexer = Lexer('  \n\t{  }  ')
        token1 = lexer.next_token()
        self.assertEqual(token1[0], TokenType.LBRACE)
        token2 = lexer.next_token()
        self.assertEqual(token2[0], TokenType.RBRACE)


class TestParser(unittest.TestCase):
    """Test parser functionality"""
    
    def test_parse_empty_object(self):
        """Test parsing empty object"""
        lexer = Lexer('{}')
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(result, {})
    
    def test_parse_empty_array(self):
        """Test parsing empty array"""
        lexer = Lexer('[]')
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(result, [])
    
    def test_parse_simple_object(self):
        """Test parsing simple object"""
        lexer = Lexer('{"key": "value"}')
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(result, {"key": "value"})
    
    def test_parse_simple_array(self):
        """Test parsing simple array"""
        lexer = Lexer('[1, 2, 3]')
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(result, [1, 2, 3])
    
    def test_parse_nested_object(self):
        """Test parsing nested objects"""
        lexer = Lexer('{"outer": {"inner": "value"}}')
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(result, {"outer": {"inner": "value"}})
    
    def test_parse_nested_array(self):
        """Test parsing nested arrays"""
        lexer = Lexer('[[1, 2], [3, 4]]')
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(result, [[1, 2], [3, 4]])
    
    def test_parse_mixed_types(self):
        """Test parsing mixed data types"""
        json_str = '{"str": "value", "num": 42, "bool": true, "null": null, "arr": [1, 2]}'
        lexer = Lexer(json_str)
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(result['str'], 'value')
        self.assertEqual(result['num'], 42)
        self.assertEqual(result['bool'], True)
        self.assertIsNone(result['null'])
        self.assertEqual(result['arr'], [1, 2])
    
    def test_parse_numbers_various_formats(self):
        """Test parsing numbers in various formats"""
        json_str = '[0, -123, 1.5, -3.14, 1e5, 1.5e-3]'
        lexer = Lexer(json_str)
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1], -123)
        self.assertEqual(result[2], 1.5)
        self.assertEqual(result[3], -3.14)
        self.assertEqual(result[4], 100000.0)
        self.assertAlmostEqual(result[5], 0.0015, places=4)
    
    def test_parse_string_with_escapes(self):
        """Test parsing strings with escape sequences"""
        json_str = r'{"msg": "Line1\nLine2\tTabbed\"Quoted\\"}'
        lexer = Lexer(json_str)
        parser = Parser(lexer)
        result = parser.parse()
        self.assertIn('\n', result['msg'])
        self.assertIn('\t', result['msg'])
        self.assertIn('"', result['msg'])
        self.assertIn('\\', result['msg'])
    
    def test_trailing_comma_in_array(self):
        """Test that trailing comma in array is rejected"""
        with self.assertRaises(JSONParseError):
            lexer = Lexer('[1, 2,]')
            parser = Parser(lexer)
            parser.parse()
    
    def test_trailing_comma_in_object(self):
        """Test that trailing comma in object is rejected"""
        with self.assertRaises(JSONParseError):
            lexer = Lexer('{"key": "value",}')
            parser = Parser(lexer)
            parser.parse()
    
    def test_duplicate_keys(self):
        """Test that duplicate keys in object are detected"""
        with self.assertRaises(JSONParseError):
            lexer = Lexer('{"key": "value1", "key": "value2"}')
            parser = Parser(lexer)
            parser.parse()
    
    def test_missing_colon(self):
        """Test that missing colon in object is detected"""
        with self.assertRaises(JSONParseError):
            lexer = Lexer('{"key" "value"}')
            parser = Parser(lexer)
            parser.parse()
    
    def test_non_string_key(self):
        """Test that non-string keys are rejected"""
        with self.assertRaises(JSONParseError):
            lexer = Lexer('{123: "value"}')
            parser = Parser(lexer)
            parser.parse()
    
    def test_unexpected_token_after_value(self):
        """Test that unexpected tokens after JSON value cause error"""
        with self.assertRaises(JSONParseError):
            lexer = Lexer('{"key": "value"} extra')
            parser = Parser(lexer)
            parser.parse()
    
    def test_max_recursion_depth(self):
        """Test that max recursion depth is enforced"""
        # Create deeply nested JSON
        depth = 105
        json_str = '[' * depth + '1' + ']' * depth
        lexer = Lexer(json_str)
        parser = Parser(lexer, max_depth=100)
        with self.assertRaises(JSONParseError):
            parser.parse()
    
    def test_large_numbers(self):
        """Test handling of large numbers"""
        json_str = '[9223372036854775807, -9223372036854775808]'
        lexer = Lexer(json_str)
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(result[0], 9223372036854775807)
        self.assertEqual(result[1], -9223372036854775808)
    
    def test_float_precision(self):
        """Test float number precision"""
        json_str = '[0.1, 0.2, 0.3]'
        lexer = Lexer(json_str)
        parser = Parser(lexer)
        result = parser.parse()
        self.assertAlmostEqual(result[0], 0.1, places=10)
        self.assertAlmostEqual(result[1], 0.2, places=10)
        self.assertAlmostEqual(result[2], 0.3, places=10)
    
    def test_error_reporting_line_column(self):
        """Test that error reporting includes line and column info"""
        json_str = '{\n  "key": invalid\n}'
        lexer = Lexer(json_str)
        parser = Parser(lexer)
        try:
            parser.parse()
            self.fail("Should have raised JSONParseError")
        except JSONParseError as e:
            self.assertGreater(e.line, 0)
            self.assertGreater(e.column, 0)


class TestComplexScenarios(unittest.TestCase):
    """Test complex, real-world scenarios"""
    
    def test_full_json_document(self):
        """Test parsing a complete JSON document"""
        json_str = """{
            "name": "John Doe",
            "age": 30,
            "active": true,
            "tags": ["dev", "python", "json"],
            "address": {
                "street": "123 Main St",
                "city": "Anytown",
                "country": "USA"
            },
            "metadata": {
                "created": "2024-01-01",
                "updated": "2024-03-24"
            }
        }"""
        lexer = Lexer(json_str)
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(result['name'], "John Doe")
        self.assertEqual(result['age'], 30)
        self.assertTrue(result['active'])
        self.assertEqual(len(result['tags']), 3)
        self.assertEqual(result['address']['city'], "Anytown")
    
    def test_array_of_objects(self):
        """Test parsing array of objects"""
        json_str = '[{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]'
        lexer = Lexer(json_str)
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['id'], 1)
        self.assertEqual(result[1]['name'], "B")
    
    def test_unicode_content(self):
        """Test parsing JSON with unicode content"""
        json_str = r'{"emoji": "\ud83d\ude00", "text": "Hello 世界"}'
        lexer = Lexer(json_str)
        parser = Parser(lexer)
        result = parser.parse()
        self.assertIn('emoji', result)
        self.assertIn('text', result)
    
    def test_whitespace_variations(self):
        """Test parsing with various whitespace"""
        json_str = '''{
            
            "key1"  :  "value1"  ,
            
            "key2"  :  [  1  ,  2  ,  3  ]
            
        }'''
        lexer = Lexer(json_str)
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(result['key1'], "value1")
        self.assertEqual(result['key2'], [1, 2, 3])
    
    def test_empty_strings(self):
        """Test parsing empty strings"""
        json_str = '{"a": "", "b": ""}'
        lexer = Lexer(json_str)
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(result['a'], '')
        self.assertEqual(result['b'], '')
    
    def test_zero_values(self):
        """Test parsing zero values"""
        json_str = '[0, 0.0, -0, 0e10]'
        lexer = Lexer(json_str)
        parser = Parser(lexer)
        result = parser.parse()
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1], 0.0)


if __name__ == '__main__':
    unittest.main()
