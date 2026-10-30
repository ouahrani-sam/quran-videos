"""Tests for word-level matching logic against scenarios and fixtures."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from scripts.match import run_matching

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "surah_114.json"


def _load_surah_114_fixture() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


DEFAULT_CFG = {
    "bismillah": True,
    "matching": {
        "repeat_window": 4,
        "repeat_mode": "last",
        "min_match_score": 0.75,
        "low_conf_score": 0.85,
    },
}


def _make_take_from_units(
    fixture: dict,
    unit_keys: list[str | int],
    take_num: int = 1,
    start_time: float = 0.0,
) -> dict:
    """Helper to synthesize words stream directly from loaded fixture without hardcoded text."""
    words: list[dict] = []
    t = start_time
    for u in unit_keys:
        if u == "bismillah":
            source_words = fixture["bismillah"]["words"]
        else:
            ayah_entry = next(a for a in fixture["ayahs"] if a["n"] == u)
            source_words = ayah_entry["words"]

        for w in source_words:
            words.append({
                "text": w["norm"],
                "norm": w["norm"],
                "start": round(t, 2),
                "end": round(t + 0.4, 2),
                "conf": 0.95,
            })
            t += 0.5
    return {
        "take": take_num,
        "audio": f"fake://take_{take_num}.m4a",
        "duration": round(t, 2),
        "model": "fake",
        "words": words,
    }


class TestMatching(unittest.TestCase):
    def setUp(self):
        self.fixture = _load_surah_114_fixture()

    def test_clean_recitation(self):
        """Case 1: Normal advance (clean continuous recitation of Bismillah and ayahs 1..6)."""
        units = ["bismillah", 1, 2, 3, 4, 5, 6]
        take1 = _make_take_from_units(self.fixture, units)
        result = run_matching(self.fixture, [take1], DEFAULT_CFG)

        self.assertEqual(len(result["dropped"]), 0)
        self.assertEqual(len(result["flags"]), 0)
        kept_ayahs = [
            s["ayah"]
            for sid in result["kept_ids"]
            for s in result["spans"]
            if s["id"] == sid and s["kind"] == "ayah"
        ]
        self.assertEqual(kept_ayahs, [1, 2, 3, 4, 5, 6])

    def test_repeat_last_ayah(self):
        """Case 2: Repeat of the last ayah (ayah 6 recited twice)."""
        units = ["bismillah", 1, 2, 3, 4, 5, 6, 6]
        take1 = _make_take_from_units(self.fixture, units)
        result = run_matching(self.fixture, [take1], DEFAULT_CFG)

        self.assertEqual(len(result["flags"]), 0)
        self.assertEqual(len(result["dropped"]), 1)
        self.assertEqual(result["dropped"][0]["reason"], "superseded_repeat")
        self.assertEqual(result["dropped"][0]["ayah"], 6)

    def test_back_4_repeat(self):
        """Case 3: Repeat from 4 back within repeat window 4."""
        units = ["bismillah", 1, 2, 3, 4, 1, 2, 3, 4, 5, 6]
        take1 = _make_take_from_units(self.fixture, units)
        result = run_matching(self.fixture, [take1], DEFAULT_CFG)

        reasons = [d["reason"] for d in result["dropped"]]
        self.assertTrue(all(r == "superseded_repeat" for r in reasons))
        kept_ayahs = [
            s["ayah"]
            for sid in result["kept_ids"]
            for s in result["spans"]
            if s["id"] == sid and s["kind"] == "ayah"
        ]
        self.assertEqual(kept_ayahs, [1, 2, 3, 4, 5, 6])

    def test_restart_mid_abandoned_partial(self):
        """Case 4: Restart mid-ayah (partial dropped, complete kept)."""
        take = _make_take_from_units(self.fixture, ["bismillah", 1])
        t = take["duration"]

        # Partial ayah 2: only word 0 from fixture
        w0 = self.fixture["ayahs"][1]["words"][0]
        take["words"].append({
            "text": w0["norm"],
            "norm": w0["norm"],
            "start": round(t, 2),
            "end": round(t + 0.4, 2),
            "conf": 0.95,
        })

        # Complete restart from ayah 2 through 6
        rest_take = _make_take_from_units(self.fixture, [2, 3, 4, 5, 6], start_time=t + 1.0)
        take["words"].extend(rest_take["words"])
        take["duration"] = rest_take["duration"]

        result = run_matching(self.fixture, [take], DEFAULT_CFG)
        flag_types = [f["type"] for f in result["flags"]]
        self.assertIn("partial_ayah", flag_types)
        drop_reasons = [d["reason"] for d in result["dropped"]]
        self.assertIn("abandoned_partial", drop_reasons)

        kept_ayahs = [
            s["ayah"]
            for sid in result["kept_ids"]
            for s in result["spans"]
            if s["id"] == sid and s["kind"] == "ayah"
        ]
        self.assertEqual(kept_ayahs, [1, 2, 3, 4, 5, 6])

    def test_back_5_flagged(self):
        """Case 5: Jump back of 5 (outside repeat window 4) is flagged out_of_window."""
        units = ["bismillah", 1, 2, 3, 4, 5, 6, 1]
        take1 = _make_take_from_units(self.fixture, units)
        result = run_matching(self.fixture, [take1], DEFAULT_CFG)

        flag_types = [f["type"] for f in result["flags"]]
        self.assertIn("out_of_window", flag_types)
        drop_reasons = [d["reason"] for d in result["dropped"]]
        self.assertIn("unmatched", drop_reasons)

    def test_unmatched_segment_flagged(self):
        """Case 6: Unmatched segment flagged with unmatched_segment."""
        take = _make_take_from_units(self.fixture, ["bismillah", 1, 2])
        t = take["duration"]

        # Simulated unrecognized noise token
        take["words"].append({
            "text": "unrecognized_token",
            "norm": "unrecognized_token",
            "start": round(t, 2),
            "end": round(t + 0.4, 2),
            "conf": 0.90,
        })
        t += 0.5

        rest_take = _make_take_from_units(self.fixture, [3, 4, 5, 6], start_time=t)
        take["words"].extend(rest_take["words"])
        take["duration"] = rest_take["duration"]

        result = run_matching(self.fixture, [take], DEFAULT_CFG)
        flag_types = [f["type"] for f in result["flags"]]
        self.assertIn("unmatched_segment", flag_types)

    def test_second_take_repeats_3_back(self):
        """Case 7: Take 2 starts by repeating 3 ayahs back from where take 1 ended."""
        # Take 1 ends at ayah 4; Take 2 starts at ayah 2 (F=4; 4 - 2 = 2, within repeat window 4)
        take1 = _make_take_from_units(self.fixture, ["bismillah", 1, 2, 3, 4], take_num=1)
        take2 = _make_take_from_units(self.fixture, [2, 3, 4, 5, 6], take_num=2)

        result = run_matching(self.fixture, [take1, take2], DEFAULT_CFG)
        kept_ayahs = [
            s["ayah"]
            for sid in result["kept_ids"]
            for s in result["spans"]
            if s["id"] == sid and s["kind"] == "ayah"
        ]
        self.assertEqual(kept_ayahs, [1, 2, 3, 4, 5, 6])
        reasons = [d["reason"] for d in result["dropped"]]
        self.assertIn("superseded_repeat", reasons)

    def test_repeat_in_take_2_replacing_take_1(self):
        """Case 8: Repeat in take 2 replaces an ayah from take 1."""
        take1 = _make_take_from_units(self.fixture, ["bismillah", 1, 2], take_num=1)
        take2 = _make_take_from_units(self.fixture, [2, 3, 4, 5, 6], take_num=2)

        result = run_matching(self.fixture, [take1, take2], DEFAULT_CFG)
        kept_spans = [s for sid in result["kept_ids"] for s in result["spans"] if s["id"] == sid]
        ayah_2_kept = next(s for s in kept_spans if s["kind"] == "ayah" and s["ayah"] == 2)
        # Take 2's recitation replaced take 1's under default repeat_mode=last
        self.assertEqual(ayah_2_kept["take"], 2)

    def test_bismillah_expected_and_present(self):
        """Case 9a: Bismillah present when expected."""
        take = _make_take_from_units(self.fixture, ["bismillah", 1, 2, 3, 4, 5, 6])
        result = run_matching(self.fixture, [take], DEFAULT_CFG)
        self.assertTrue(result["bismillah_expected"])
        self.assertNotIn("bismillah_missing", [f["type"] for f in result["flags"]])
        self.assertNotIn("bismillah_unexpected", [f["type"] for f in result["flags"]])

    def test_bismillah_expected_but_missing(self):
        """Case 9b: Bismillah missing when expected flags bismillah_missing."""
        take = _make_take_from_units(self.fixture, [1, 2, 3, 4, 5, 6])
        result = run_matching(self.fixture, [take], DEFAULT_CFG)
        flag_types = [f["type"] for f in result["flags"]]
        self.assertIn("bismillah_missing", flag_types)

    def test_bismillah_unexpected_flagged(self):
        """Case 9c: Bismillah recited when unexpected flags bismillah_unexpected."""
        cfg = copy.deepcopy(DEFAULT_CFG)
        cfg["bismillah"] = False
        take = _make_take_from_units(self.fixture, ["bismillah", 1, 2, 3, 4, 5, 6])
        result = run_matching(self.fixture, [take], cfg)

        self.assertFalse(result["bismillah_expected"])
        flag_types = [f["type"] for f in result["flags"]]
        self.assertIn("bismillah_unexpected", flag_types)
        reasons = [d["reason"] for d in result["dropped"]]
        self.assertIn("unmatched", reasons)

    def test_surah_1_and_9_rules(self):
        """Case 9d: Surah 1 and Surah 9 bismillah_expected rules."""
        s1_fixture = copy.deepcopy(self.fixture)
        s1_fixture["surah"] = 1
        s1_fixture["bismillah"] = None
        res1 = run_matching(s1_fixture, [_make_take_from_units(self.fixture, [1, 2])], DEFAULT_CFG)
        self.assertFalse(res1["bismillah_expected"])

        s9_fixture = copy.deepcopy(self.fixture)
        s9_fixture["surah"] = 9
        s9_fixture["bismillah"] = None
        res9 = run_matching(s9_fixture, [_make_take_from_units(self.fixture, [1, 2])], DEFAULT_CFG)
        self.assertFalse(res9["bismillah_expected"])

    def test_take_ending_mid_ayah(self):
        """Case 10: Take ending mid-ayah drops partial and flags partial_ayah."""
        take = _make_take_from_units(self.fixture, ["bismillah", 1])
        t = take["duration"]

        # Take ends after only word 0 of ayah 2
        w0 = self.fixture["ayahs"][1]["words"][0]
        take["words"].append({
            "text": w0["norm"],
            "norm": w0["norm"],
            "start": round(t, 2),
            "end": round(t + 0.4, 2),
            "conf": 0.95,
        })
        take["duration"] = round(t + 0.5, 2)

        result = run_matching(self.fixture, [take], DEFAULT_CFG)
        flag_types = [f["type"] for f in result["flags"]]
        self.assertIn("partial_ayah", flag_types)
        self.assertIn("missing_ayah", flag_types)  # Ayahs 2..6 missing
        reasons = [d["reason"] for d in result["dropped"]]
        self.assertIn("abandoned_partial", reasons)

    def test_shared_endings_distinction(self):
        """Case 11: Ayahs with shared endings are cleanly distinguished at word boundaries."""
        units = [1, 2, 3]
        take1 = _make_take_from_units(self.fixture, units)
        result = run_matching(self.fixture, [take1], DEFAULT_CFG)

        ayah_spans = [s for s in result["spans"] if s["kind"] == "ayah"]
        self.assertEqual(len(ayah_spans), 3)
        self.assertEqual([s["ayah"] for s in ayah_spans], [1, 2, 3])
        self.assertEqual(ayah_spans[0]["word_range"], [0, 3])
        self.assertEqual(ayah_spans[1]["word_range"], [4, 5])
        self.assertEqual(ayah_spans[2]["word_range"], [6, 7])

    def test_recognizer_error_substituted_word(self):
        """Case 12a: Recognizer error with substituted word matches candidate."""
        take = _make_take_from_units(self.fixture, ["bismillah"])
        t = take["duration"]

        # Ayah 1 words with 1 substitution (e.g. simulated minor misrecognition)
        a1_words = self.fixture["ayahs"][0]["words"]
        for idx, w in enumerate(a1_words):
            text_norm = w["norm"] if idx != 1 else (w["norm"] + "x")
            take["words"].append({
                "text": text_norm,
                "norm": text_norm,
                "start": round(t, 2),
                "end": round(t + 0.4, 2),
                "conf": 0.85,
            })
            t += 0.5

        rest_take = _make_take_from_units(self.fixture, [2, 3, 4, 5, 6], start_time=t)
        take["words"].extend(rest_take["words"])
        take["duration"] = rest_take["duration"]

        result = run_matching(self.fixture, [take], DEFAULT_CFG)
        kept_ayahs = [
            s["ayah"]
            for sid in result["kept_ids"]
            for s in result["spans"]
            if s["id"] == sid and s["kind"] == "ayah"
        ]
        self.assertEqual(kept_ayahs, [1, 2, 3, 4, 5, 6])

    def test_recognizer_error_dropped_word(self):
        """Case 12b: Recognizer error with dropped word (deletion) matches candidate."""
        take = _make_take_from_units(self.fixture, ["bismillah"])
        t = take["duration"]

        # Ayah 1 (4 words) with word 1 dropped by recognizer (3 recognized words)
        a1_words = self.fixture["ayahs"][0]["words"]
        for idx, w in enumerate(a1_words):
            if idx == 1:
                continue
            take["words"].append({
                "text": w["norm"],
                "norm": w["norm"],
                "start": round(t, 2),
                "end": round(t + 0.4, 2),
                "conf": 0.85,
            })
            t += 0.5

        rest_take = _make_take_from_units(self.fixture, [2, 3, 4, 5, 6], start_time=t)
        take["words"].extend(rest_take["words"])
        take["duration"] = rest_take["duration"]

        result = run_matching(self.fixture, [take], DEFAULT_CFG)
        kept_ayahs = [
            s["ayah"]
            for sid in result["kept_ids"]
            for s in result["spans"]
            if s["id"] == sid and s["kind"] == "ayah"
        ]
        self.assertEqual(kept_ayahs, [1, 2, 3, 4, 5, 6])

    def test_recognizer_error_inserted_word(self):
        """Case 12c: Recognizer error with inserted word (insertion) matches candidate."""
        take = _make_take_from_units(self.fixture, ["bismillah"])
        t = take["duration"]

        # Ayah 1 (4 words) with extra inserted token
        a1_words = self.fixture["ayahs"][0]["words"]
        for idx, w in enumerate(a1_words):
            take["words"].append({
                "text": w["norm"],
                "norm": w["norm"],
                "start": round(t, 2),
                "end": round(t + 0.4, 2),
                "conf": 0.85,
            })
            t += 0.5
            if idx == 0:
                take["words"].append({
                    "text": "extra_token",
                    "norm": "extra_token",
                    "start": round(t, 2),
                    "end": round(t + 0.4, 2),
                    "conf": 0.70,
                })
                t += 0.5

        rest_take = _make_take_from_units(self.fixture, [2, 3, 4, 5, 6], start_time=t)
        take["words"].extend(rest_take["words"])
        take["duration"] = rest_take["duration"]

        result = run_matching(self.fixture, [take], DEFAULT_CFG)
        kept_ayahs = [
            s["ayah"]
            for sid in result["kept_ids"]
            for s in result["spans"]
            if s["id"] == sid and s["kind"] == "ayah"
        ]
        self.assertEqual(kept_ayahs, [1, 2, 3, 4, 5, 6])

    def test_segment_spanning_two_ayahs_no_pause(self):
        """Case 13: Continuous stream spanning two ayahs without pause."""
        take = _make_take_from_units(self.fixture, ["bismillah", 1, 2, 3, 4, 5, 6])
        result = run_matching(self.fixture, [take], DEFAULT_CFG)
        self.assertEqual(len(result["dropped"]), 0)
        self.assertEqual(len(result["flags"]), 0)
        self.assertEqual(len(result["spans"]), 7)


if __name__ == "__main__":
    unittest.main()