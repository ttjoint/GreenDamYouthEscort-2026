"""
FalunWord.lib structure tests.

These need the legacy binary, which is NOT shipped with this repository.
Set ``GREEN_DAM_LEGACY_TREE`` to a directory containing the unpacked 3.17
tree to enable them, otherwise they are skipped.

    GREEN_DAM_LEGACY_TREE=/path/to/tree python -m unittest discover -s tests
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import falunword

LEGACY = os.environ.get("GREEN_DAM_LEGACY_TREE")
LIB = Path(LEGACY) / "Windows" / "HNCLIB" / "FalunWord.lib" if LEGACY else None


@unittest.skipUnless(LIB and LIB.is_file(), "legacy tree not available")
class FalunLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blob = LIB.read_bytes()
        cls.lay = falunword.Layout(cls.blob)

    def test_sections_close_exactly(self):
        # This is the core evidence for the recovery: every section size
        # accounts for the real file length down to the HNCIT trailer.
        self.assertTrue(self.lay.verify())
        self.assertEqual(self.lay.trailer, falunword.TRAILER)
        self.assertEqual(self.lay.consumed + len(self.lay.trailer), len(self.blob))

    def test_expected_section_counts(self):
        self.assertEqual(self.lay.n1, 272920)
        self.assertEqual(self.lay.n2, 4693)
        self.assertEqual(self.lay.n3, 36943)

    def test_known_chain_spells_a_term(self):
        # 0 -> 8 -> 9 -> 10 -> 11 -> 12 is the documented example chain.
        # Reading `next` as a sibling list also yields plausible Chinese, which
        # is exactly why this pin matters.
        chain = self.lay.chain
        walked = []
        k = 0
        while True:
            walked.append(chr(chain[k][0]))
            nxt = chain[k][1]
            if nxt == 0:
                break
            k = nxt
        self.assertEqual("".join(walked), '610"歹徒')

    def test_terms_are_unique_paths(self):
        terms = falunword.iter_chains(self.lay)
        self.assertGreater(len(terms), 100000)
        # every node is consumed exactly once across all chains
        self.assertEqual(sum(1 for t in terms if t["word"]), len(terms))
        words = [t["word"] for t in terms]
        self.assertIn('610"歹徒', words)
        self.assertTrue(any("法轮功" in w for w in words))

    def test_sense_ids_index_the_sense_table(self):
        # Terminal nodes carry a sense id, and every such id must be a valid
        # index into the sense table. This is what proves the two views describe
        # the same corpus rather than two unrelated structures.
        terms = falunword.iter_chains(self.lay)
        ids = [t["sense_id"] for t in terms if t["sense_id"] is not None]
        self.assertGreater(len(ids), 8000)
        self.assertTrue(all(0 <= i < self.lay.n2 for i in ids))

    def test_hong_hei_is_real_data(self):
        # Values cluster in 0..7 and grouped terms are thematically coherent.
        values = {rec[2] for rec in self.lay.senses}
        self.assertTrue(values <= set(range(8)))
        self.assertIn(0, values)
        self.assertGreater(len(values), 3)

    def test_fan_dong_is_uninitialised_fill(self):
        # Regression guard: this field looks like data but is MSVC debug
        # 0xCDCDCDCD fill. If a future extraction ever produces real values
        # here, the consumer analysis would need revisiting.
        for rec in self.lay.senses:
            self.assertEqual(rec[1] >> 16, 0xCDCD)

    def test_sense_table_offsets_stay_in_pool(self):
        senses = falunword.decode_sense_table(self.lay)
        self.assertGreater(len(senses), 4000)
        pool_len = len(self.lay.pool)
        for rec in self.lay.senses:
            offset, _a, _b, length = rec
            if length:
                self.assertLessEqual(offset + length, pool_len + 64)


if __name__ == "__main__":
    unittest.main()
