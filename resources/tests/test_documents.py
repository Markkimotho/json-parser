"""Gate tests for engines/documents.py (parsed-document cache)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from engines.core import JSONParseError  # noqa: E402
from engines.documents import DocumentCache  # noqa: E402


class DocumentCacheTests(unittest.TestCase):
    def test_hits_and_misses(self):
        cache = DocumentCache()
        self.assertEqual(cache.parse('{"a": 1}'), ({"a": 1}, False))
        value, hit = cache.parse('{"a": 1}')
        self.assertEqual((value, hit, cache.hits, cache.misses, len(cache)), ({"a": 1}, True, 1, 1, 1))

    def test_evicts_least_recently_used_by_count(self):
        cache = DocumentCache(max_entries=2)
        cache.parse("[1]")
        cache.parse("[2]")
        cache.parse("[1]")  # touch [1] so [2] is the oldest
        cache.parse("[3]")
        self.assertEqual(len(cache), 2)
        self.assertTrue(cache.parse("[1]")[1])
        self.assertFalse(cache.parse("[2]")[1])

    def test_evicts_by_total_size_and_skips_oversized_documents(self):
        cache = DocumentCache(max_bytes=10)
        cache.parse("[1, 2, 3]")  # 9 chars
        cache.parse("[4]")  # pushes total over 10, evicting the first
        self.assertEqual(len(cache), 1)
        cache.parse("[" + "1," * 20 + "1]")  # larger than the whole cache: not stored
        self.assertEqual(len(cache), 1)

    def test_errors_propagate_and_clear_resets(self):
        cache = DocumentCache()
        with self.assertRaises(JSONParseError):
            cache.parse("{bad")
        cache.parse("[]")
        cache.clear()
        self.assertEqual(len(cache), 0)
        self.assertFalse(cache.parse("[]")[1])

    def test_keys_handle_lone_surrogates(self):
        self.assertEqual(len(DocumentCache.key("\ud800")), 64)


if __name__ == "__main__":
    unittest.main()
