import unittest

from app import app


class FlaskWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    def test_python_workbench_and_legacy_terminal(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        page = self.client.get('/python/')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Python parser', page.data)
        page.close()
        asset = self.client.get('/python/app.js')
        self.assertEqual(asset.status_code, 200)
        asset.close()
        self.assertEqual(self.client.get('/terminal/').status_code, 200)

    def test_python_parser_returns_formatted_source_without_integer_loss(self):
        response = self.client.post('/parse-json', data={'jsonData': '{"number":9007199254740993}'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('9007199254740993', response.json['formatted'])
        self.assertEqual(response.json['minified'], '{"number":9007199254740993}')

    def test_python_parser_rejects_duplicate_keys_and_nonfinite_numbers(self):
        duplicate = self.client.post('/parse-json', data={'jsonData': '{"a":1,"a":2}'})
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn('Duplicate key', duplicate.json['error'])
        huge_exponent = self.client.post('/parse-json', data={'jsonData': '1e999'})
        self.assertEqual(huge_exponent.status_code, 400)


if __name__ == '__main__':
    unittest.main()
