"""Tests for forced alignment logic, silence snapping, and flag emission."""

from __future__ import annotations

import copy
import json
import math
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path

from scripts.align import _detect_silence_offsets, run_alignment

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "tests" / "fixtures" / "surah_114.json"


def _load_surah_114_fixture() -> dict:
    with open(FIXTURE_PATH, encoding="utf-8") as f:
        return json.load(f)


DEFAULT_CFG = {
    "bismillah": True,
    "alignment": {
        "silence_snap_max_s": 0.30,
        "silence_threshold_db": -40,
        "trim_last_word_silence": True,
        "min_word_conf": 0.30,
        "max_ayah_seconds_factor": 3.0,
    },
    "models": {
        "aligner": "MahmoudAshraf/mms-300m-1130-forced-aligner",
        "device": "auto",
    },
}

SAMPLE_SPANS = {
    "surah": 114,
    "bismillah_expected": True,
    "window": 4,
    "spans": [
        {
            "id": 0,
            "take": 1,
            "kind": "bismillah",
            "ayah": None,
            "start": 1.0,
            "end": 4.0,
            "word_range": [0, 3],
            "complete": True,
            "conf": 0.95,
            "superseded": False,
        },
        {
            "id": 1,
            "take": 1,
            "kind": "ayah",
            "ayah": 1,
            "start": 5.0,
            "end": 9.0,
            "word_range": [4, 7],
            "complete": True,
            "conf": 0.92,
            "superseded": False,
        },
    ],
    "kept_ids": [0, 1],
    "dropped": [],
    "flags": [],
}


def _synthetic_test_aligner(audio, start, end, norm_words, target_words, conf=0.95):
    """Synthetic test aligner providing predictable word timings."""
    n = len(target_words)
    dur = max(0.1, end - start) / n
    return [
        {
            "i": w["i"],
            "start": round(start + idx * dur, 2),
            "end": round(start + idx * dur + dur * 0.85, 2),
            "conf": conf,
        }
        for idx, w in enumerate(target_words)
    ]


class TestAlign(unittest.TestCase):
    def setUp(self):
        self.fixture = _load_surah_114_fixture()

    def _run_with_aligner(self, aligner):
        return run_alignment(
            self.fixture,
            SAMPLE_SPANS,
            DEFAULT_CFG,
            project_root=str(ROOT),
            aligner_fn=aligner,
        )

    def test_basic_contract_and_order(self):
        """Verifies exact contract shape, kept_ids ordering, and Word.i alignment."""
        res = self._run_with_aligner(_synthetic_test_aligner)
        self.assertEqual(res["surah"], 114)
        self.assertEqual(len(res["items"]), 2)

        # Check item ordering matches kept_ids
        self.assertEqual(res["items"][0]["span_id"], 0)
        self.assertEqual(res["items"][0]["kind"], "bismillah")
        self.assertIsNone(res["items"][0]["ayah"])

        self.assertEqual(res["items"][1]["span_id"], 1)
        self.assertEqual(res["items"][1]["kind"], "ayah")
        self.assertEqual(res["items"][1]["ayah"], 1)

        # Verify Word.i mapping exactly mirrors text.json
        for item in res["items"]:
            for idx, w in enumerate(item["words"]):
                self.assertEqual(w["i"], idx)

    def test_word_times_inside_cut_range_and_trimming(self):
        """Ensures word start/end lie inside [cut_start, cut_end] and trailing silence is trimmed."""
        res = self._run_with_aligner(_synthetic_test_aligner)
        for item in res["items"]:
            c_start = item["cut_start"]
            c_end = item["cut_end"]
            self.assertLess(c_start, c_end)

            for w in item["words"]:
                self.assertGreaterEqual(w["start"], c_start)
                self.assertLessEqual(w["end"], c_end)

            # Trailing silence on final word must not exceed silence_snap_max_s
            last_w = item["words"][-1]
            self.assertLessEqual(
                c_end - last_w["end"],
                DEFAULT_CFG["alignment"]["silence_snap_max_s"] + 0.05,
            )

    def test_low_align_conf_flag(self):
        """Emits low_align_conf when item confidence drops below min_word_conf."""
        def low_conf_aligner(audio, start, end, norm_words, target_words):
            return _synthetic_test_aligner(audio, start, end, norm_words, target_words, conf=0.20)

        res = self._run_with_aligner(low_conf_aligner)
        flag_types = [f["type"] for f in res["flags"]]
        self.assertIn("low_align_conf", flag_types)

    def test_low_word_conf_boundary_flag(self):
        """Emits low_word_conf_boundary when first or last word has low confidence."""
        def boundary_low_conf_aligner(audio, start, end, norm_words, target_words):
            words = _synthetic_test_aligner(audio, start, end, norm_words, target_words, conf=0.90)
            words[0]["conf"] = 0.20
            return words

        res = self._run_with_aligner(boundary_low_conf_aligner)
        flag_types = [f["type"] for f in res["flags"]]
        self.assertIn("low_word_conf_boundary", flag_types)

    def test_abnormal_duration_flag(self):
        """Emits abnormal_duration flag if span duration is abnormally stretched."""
        stretched_spans = copy.deepcopy(SAMPLE_SPANS)
        stretched_spans["spans"][1]["end"] = 35.0

        res = run_alignment(
            self.fixture,
            stretched_spans,
            DEFAULT_CFG,
            project_root=str(ROOT),
            aligner_fn=_synthetic_test_aligner,
        )
        flag_types = [f["type"] for f in res["flags"]]
        self.assertIn("abnormal_duration", flag_types)

    def test_rejects_missing_audio_metadata_in_real_mode(self):
        """Synthetic alignment must never be selected for missing real-audio metadata."""
        with self.assertRaisesRegex(ValueError, "Missing audio path metadata"):
            run_alignment(
                self.fixture,
                SAMPLE_SPANS,
                DEFAULT_CFG,
                project_root=str(ROOT),
                aligner_fn=None,
            )

    def test_rejects_mismatched_alignment_count(self):
        with self.assertRaisesRegex(ValueError, "returned"):
            self._run_with_aligner(lambda *args: [])

    def test_rejects_invalid_alignment_values(self):
        def malformed(*args):
            words = _synthetic_test_aligner(*args)
            words[0]["conf"] = math.nan
            return words

        with self.assertRaisesRegex(ValueError, "Non-finite|Invalid"):
            self._run_with_aligner(malformed)

    def test_detects_real_wav_silence_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "silence.wav"
            rate = 1000
            quiet = b"\0\0" * 250
            loud = struct.pack("<" + "h" * 500, *([12000] * 500))
            with wave.open(str(path), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(rate)
                stream.writeframes(quiet + loud + quiet)
            start, end = _detect_silence_offsets(str(path), 0.30, 0.70, 0.30, -40)
            self.assertLess(start, 0.30)
            self.assertGreater(end, 0.70)

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg not installed on this system")
    def test_detects_m4a_silence_boundaries(self):
        with tempfile.TemporaryDirectory() as directory:
            wav_path = Path(directory) / "test.wav"
            m4a_path = Path(directory) / "test.m4a"
            rate = 16000
            quiet = b"\0\0" * 4800  # 0.3s silence (0.0 to 0.3s)
            loud = struct.pack("<" + "h" * 8000, *([12000] * 8000))  # 0.5s sound (0.3s to 0.8s)
            quiet2 = b"\0\0" * 4800  # 0.3s silence (0.8s to 1.1s)
            with wave.open(str(wav_path), "wb") as stream:
                stream.setnchannels(1)
                stream.setsampwidth(2)
                stream.setframerate(rate)
                stream.writeframes(quiet + loud + quiet2)

            # Transcode to M4A using FFmpeg
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_path), "-c:a", "aac", str(m4a_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Probe around cut points inside the loud sound region (0.35s and 0.75s)
            start, end = _detect_silence_offsets(str(m4a_path), 0.35, 0.75, 0.30, -40)
            self.assertLess(start, 0.35)
            self.assertGreater(end, 0.75)

    def test_silence_detection_reports_decoding_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            bad_path = Path(directory) / "corrupt.m4a"
            bad_path.write_bytes(b"not an audio file at all")
            with self.assertRaises(RuntimeError):
                _detect_silence_offsets(str(bad_path), 0.1, 0.9, 0.3, -40)

    def test_empty_or_odd_input_handling(self):
        empty_spans = {
            "surah": 114,
            "bismillah_expected": True,
            "window": 4,
            "spans": [],
            "kept_ids": [],
            "dropped": [],
            "flags": [],
        }
        res = run_alignment(
            self.fixture,
            empty_spans,
            DEFAULT_CFG,
            project_root=str(ROOT),
            aligner_fn=_synthetic_test_aligner,
        )
        self.assertEqual(res["items"], [])
        self.assertEqual(res["flags"], [])


if __name__ == "__main__":
    unittest.main()