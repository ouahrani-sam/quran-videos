import argparse
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.run_pipeline import run_pipeline


class PipelineTests(unittest.TestCase):
    def test_review_gate_returns_two(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio = root / "take.wav"
            audio.write_bytes(b"audio")
            config = root / "config.yaml"
            config.write_text((Path("config.yaml").read_text()), encoding="utf-8")
            args = argparse.Namespace(
                surah=114, audio=[str(audio)], start_ayah=1,
                stage="review", force=None, config=str(config),
            )
            with patch("scripts.run_pipeline.load_config") as load:
                load.return_value = {
                    "paths": {"work_dir": str(root / "work"), "log_dir": str(root / "logs")},
                    "api": {}, "models": {}, "matching": {}, "alignment": {},
                }
                with patch("scripts.run_pipeline._read", return_value={"blocked": True}):
                    # The mocked stage output is sufficient to verify the gate path.
                    with patch("scripts.review.write_review", return_value={"blocked": True}):
                        self.assertEqual(run_pipeline(args), 2)


if __name__ == "__main__":
    unittest.main()
