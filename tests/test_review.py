import json
import tempfile
import unittest
from pathlib import Path

from scripts.review import build_review, write_review


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.spans = {
            "surah": 114,
            "spans": [{"id": 1, "kind": "ayah", "ayah": 1, "take": 1, "start": 2.0}],
            "flags": [{"type": "unmatched_segment", "ayah": 1, "severity": "warn", "time": 2.0}],
        }
        self.aligned = {
            "surah": 114,
            "items": [{
                "span_id": 1, "kind": "ayah", "ayah": 1, "take": 1,
                "cut_start": 2.0, "cut_end": 4.0,
                "words": [{"i": 0, "start": 2.2, "end": 2.8, "conf": 0.9}],
            }],
            "flags": [],
        }

    def test_unaccepted_flags_block_review(self):
        result = build_review(self.aligned, self.spans)
        self.assertTrue(result["blocked"])
        self.assertEqual(len(result["flags"]), 1)

    def test_accept_and_shift_are_applied(self):
        result = build_review(
            self.aligned, self.spans,
            {"accept_flags": [{"type": "unmatched_segment", "ayah": 1}],
             "shift": [{"span_id": 1, "cut_start_delta": -1, "cut_end_delta": 1}]},
        )
        self.assertFalse(result["blocked"])
        self.assertEqual(result["items"][0]["cut_start"], 1.0)
        self.assertEqual(result["items"][0]["cut_end"], 5.0)

    def test_drop_creates_missing_flag_and_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "aligned.json").write_text(json.dumps(self.aligned), encoding="utf-8")
            (work / "spans.json").write_text(json.dumps(self.spans), encoding="utf-8")
            (work / "overrides.json").write_text(json.dumps({"drop_spans": [1]}), encoding="utf-8")
            result = write_review(work)
            self.assertEqual(result["items"], [])
            self.assertIn("missing_ayah", {flag["type"] for flag in result["flags"]})
            self.assertTrue((work / "review.json").exists())
            self.assertTrue((work / "review.html").exists())


if __name__ == "__main__":
    unittest.main()
