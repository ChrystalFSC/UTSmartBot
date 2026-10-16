"""Tests for the per-model search index files (no API key needed).

Run with:  python -m unittest discover tests
"""
import json
import unittest

import numpy as np

from rag import CHUNKS_PATH, index_path


class IndexPathTest(unittest.TestCase):
    def test_one_file_per_model(self):
        self.assertEqual(index_path("gemini-embedding-001").name, "index-gemini-embedding-001.npz")
        self.assertEqual(index_path("intfloat/multilingual-e5-large").name, "index-intfloat-multilingual-e5-large.npz")

    def test_committed_indexes_match_the_chunks(self):
        # Both indexes ship in the repository; Render uses the Gemini one.
        ids = [c["id"] for c in json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))]
        for model in ("intfloat/multilingual-e5-large", "gemini-embedding-001"):
            with self.subTest(model=model):
                index = np.load(index_path(model))
                self.assertEqual(str(index["model"]), model)
                self.assertEqual(list(index["ids"]), ids)
                self.assertEqual(len(index["vectors"]), len(ids))


if __name__ == "__main__":
    unittest.main()
