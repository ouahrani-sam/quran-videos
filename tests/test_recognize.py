import json
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.recognize import FAKE_SCENARIOS, generate_fake_scenario


ROOT = Path(__file__).resolve().parents[1]


class FakeRecognitionTests(unittest.TestCase):
    def test_every_fake_scenario_writes_spec_words(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "tests" / "fixtures", root / "tests" / "fixtures")
            for scenario in FAKE_SCENARIOS:
                takes = generate_fake_scenario(114, scenario, root)
                expected_takes = 2 if scenario == "two_takes" else 1
                self.assertEqual(len(takes), expected_takes)
                for take in takes:
                    path = root / "work" / "114" / f"take_{take['take']}" / "words.json"
                    data = json.loads(path.read_text(encoding="utf-8"))
                    self.assertEqual(set(data), {"take", "audio", "duration", "model", "words"})
                    self.assertEqual(data["model"], "fake")
                    self.assertTrue(data["audio"].startswith("fake://"))
                    self.assertGreater(data["duration"], 0)
                    self.assertGreater(len(data["words"]), 0)
                    for word in data["words"]:
                        self.assertEqual(set(word), {"text", "norm", "start", "end", "conf"})
                        self.assertEqual(word["text"], word["norm"])
                        self.assertLessEqual(word["start"], word["end"])
                        self.assertGreaterEqual(word["conf"], 0)
                        self.assertLessEqual(word["conf"], 1)


if __name__ == "__main__":
    unittest.main()
