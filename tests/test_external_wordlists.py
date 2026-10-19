import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import import_external_wordlists as importer


class ExternalWordlistImportTest(unittest.TestCase):
    def test_level_and_raw_line_are_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "words.dict"
            src.write_text("龟头=4\n普通词\n", encoding="utf-8")
            result = importer.import_file(src)
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["entries"][0]["word"], "龟头")
        self.assertEqual(result["entries"][0]["level"], 4)
        self.assertEqual(result["entries"][1]["raw"], "普通词")
        self.assertIsNone(result["entries"][1]["level"])

    def test_external_export_has_all_six_files(self):
        path = ROOT / "data" / "external-wordlists.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(data["files"]), 6)
        self.assertEqual(sum(f["count"] for f in data["files"]), 38579)
        self.assertTrue(all(f["sha256"] for f in data["files"]))


if __name__ == "__main__":
    unittest.main()
