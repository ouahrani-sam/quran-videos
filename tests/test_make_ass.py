import json
import unittest
from pathlib import Path

from scripts.make_ass import _chunk_ranges, build_ass
from scripts.pipeline_common import load_config


ROOT = Path(__file__).resolve().parents[1]
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


class MakeAssTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(ROOT / "config.yaml")
        for field in ("arabic_font", "translit_font", "english_font"):
            self.config["display"][field] = FONT
        self.config["display"]["reciter_font"] = FONT
        self.text = json.loads((ROOT / "tests/fixtures/surah_114.json").read_text(encoding="utf-8"))
        self.template = (ROOT / "template/template.ass").read_text(encoding="utf-8")

    def _timeline(self):
        items = []
        time = 1.0
        for ayah in self.text["ayahs"][:2]:
            words = []
            for word in ayah["words"]:
                words.append({"i": word["i"], "start": time, "end": time + 0.3, "conf": 0.9})
                time += 0.4
            items.append({"kind": "ayah", "ayah": ayah["n"], "start": time - 0.4,
                          "end": words[-1]["end"], "words": words})
        return {"duration": 12.0, "items": items}

    def test_styles_use_measured_font_family(self):
        ass, _ = build_ass(self.text, self._timeline(), self.config, self.template)
        styles = "\n".join(line for line in ass.splitlines() if line.startswith("Style:"))
        self.assertIn("Style: Arabic,DejaVu Sans,", styles)
        self.assertIn("Style: Transliteration,DejaVu Sans,", styles)
        self.assertIn("Style: English,DejaVu Sans,", styles)
        self.assertIn("Style: Header,DejaVu Sans,", styles)

    def test_events_do_not_overlap_with_lead_time(self):
        self.config["display"]["max_ayahs_per_screen"] = 1
        ass, _ = build_ass(self.text, self._timeline(), self.config, self.template)
        events = [line.split(",") for line in ass.splitlines() if line.startswith("Dialogue: 0,0:")]
        content_events = events[-6:]
        self.assertEqual(len(content_events), 6)
        self.assertLessEqual(content_events[0][2], content_events[3][1])
        self.assertEqual(content_events[-3][2], "0:00:12.00")
        self.assertEqual(content_events[-2][2], "0:00:12.00")
        self.assertEqual(content_events[-1][2], "0:00:12.00")

    def test_pause_ranges_cover_words_and_are_balanced(self):
        pause_a = chr(0x06D6)
        pause_b = chr(0x06D7)
        pause_c = chr(0x06D8)
        words = [
            {"arabic": word, "translit": "word" * width}
            for word, width in (("a" + pause_a, 4), ("b", 3), ("c" + pause_b, 5),
                                ("d", 3), ("e" + pause_c, 4), ("f", 3))
        ]
        paths = {field: Path(FONT) for field in ("arabic", "translit")}
        boxes = {"arabic": [0, 0, 500, 100], "translit": [0, 0, 500, 100]}
        ranges = _chunk_ranges(words, 3, paths, 10)
        self.assertEqual([word for start, end in ranges for word in range(start, end)], list(range(6)))
        self.assertIn((0, 2), ranges)
        self.assertIn((2, 4), ranges)
        self.assertIn((4, 6), ranges)
        from PIL import ImageFont
        font = ImageFont.truetype(FONT, size=10)
        widths = [font.getlength(" ".join(words[i]["translit"] for i in range(start, end)))
                  for start, end in ranges]
        self.assertLess(max(widths) - min(widths), 30)

    def test_pause_hold_and_wrapping_remain_intact(self):
        ass, flags = build_ass(self.text, self._timeline(), self.config, self.template)
        self.assertFalse(flags)
        dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
        self.assertTrue(any(r"\N" in line for line in dialogues))
        self.assertTrue(any(",0:00:12.00," in line for line in dialogues))


if __name__ == "__main__":
    unittest.main()
