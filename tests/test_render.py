import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.render import render_surah


class RenderTests(unittest.TestCase):
    def test_builds_timeline_and_calls_ffmpeg_for_each_cut_and_concat(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            take = work / "take_1"
            take.mkdir()
            (take / "words.json").write_text(json.dumps({
                "take": 1, "audio": "recording.m4a",
                "words": [],
            }), encoding="utf-8")
            (work / "review.json").write_text(json.dumps({
                "surah": 114, "items": [{
                    "kind": "ayah", "ayah": 1, "take": 1,
                    "cut_start": 2.0, "cut_end": 4.0,
                    "words": [{"i": 0, "start": 2.2, "end": 2.8, "conf": 0.9}],
                }],
            }), encoding="utf-8")
            calls = []

            def fake_ffmpeg(args):
                calls.append(args)

            with patch("scripts.render._run_ffmpeg", side_effect=fake_ffmpeg):
                result = render_surah(work, work / "out.wav")
            self.assertEqual(result["duration"], 2.0)
            self.assertEqual(result["items"][0]["words"][0]["start"], 0.2)
            self.assertEqual(len(calls), 2)
            self.assertTrue((work / "timeline.json").exists())

    def test_missing_ffmpeg_is_explicit(self):
        with patch("scripts.render.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "FFmpeg"):
                from scripts.render import _run_ffmpeg
                _run_ffmpeg(["-version"])


if __name__ == "__main__":
    unittest.main()
