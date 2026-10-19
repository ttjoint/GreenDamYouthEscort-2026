import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from green_dam.wordlists import WordlistIndex


class WordlistIndexTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = WordlistIndex.from_external_json(
            ROOT / "data" / "external-wordlists.json")

    def test_provenance_and_level_are_merged(self):
        hit = self.index.get("黄色网站")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertIn("words.dict", hit.sources)
        self.assertIn(4, hit.levels)
        self.assertEqual(hit.max_level, 4)

    def test_longest_match(self):
        hit = self.index.longest_at("黄色网站很好", 0)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.word, "黄色网站")

    def test_plain_words_are_not_assigned_fake_level(self):
        hit = self.index.get("习近平")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.levels, ())
        self.assertIsNone(hit.max_level)

    def test_scan_is_longest_and_non_overlapping(self):
        hits = self.index.scan("这是黄色网站和成人网站")
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0][1].word, "黄色网站")


if __name__ == "__main__":
    unittest.main()
