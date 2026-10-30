"""Create timed ASS subtitles from Quran text and an aligned timeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    from PIL import ImageFont
except ImportError as exc:
    ImageFont = None
    _PIL_IMPORT_ERROR = exc

try:
    from pipeline_common import ConfigError, load_config, resolve_path
except ModuleNotFoundError:
    from scripts.pipeline_common import ConfigError, load_config, resolve_path


class LayoutError(ValueError):
    """Raised when a display unit cannot fit its configured strip."""


def _font(path: Path, size: int):
    if ImageFont is None:
        raise ConfigError(f"Pillow is required for shaped text metrics: {_PIL_IMPORT_ERROR}")
    if not path.is_file():
        raise ConfigError(f"Configured font does not exist: {path}")
    return ImageFont.truetype(str(path), size=size)


def _font_family(path: Path) -> str:
    font = _font(path, 16)
    family = font.getname()[0]
    if not family:
        raise ConfigError(f"Configured font has no family name: {path}")
    return family


def _wrap(text: str, font: Any, width: float, max_lines: int) -> list[str] | None:
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = word if not current else f"{current} {word}"
        if font.getlength(candidate) <= width:
            current = candidate
        else:
            if not current:
                return None
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines if len(lines) <= max_lines else None


def _fit(text: str, box: list[float], font_path: Path, minimum: int, maximum: int,
         max_lines: int) -> tuple[int, list[str]]:
    for size in range(maximum, minimum - 1, -1):
        font = _font(font_path, size)
        lines = _wrap(text, font, box[2], max_lines)
        if lines and size * len(lines) * 1.25 <= box[3]:
            return size, lines
    raise LayoutError("text does not fit at the configured minimum font size")


def _ass_time(seconds: float) -> str:
    centiseconds_total = max(0, int(round(seconds * 100)))
    hours, remainder = divmod(centiseconds_total, 360000)
    minutes, remainder = divmod(remainder, 6000)
    whole, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{centiseconds:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


def _lookup(text: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    if item["kind"] == "bismillah":
        if text.get("bismillah") is None:
            raise LayoutError("Timeline contains Bismillah but text has none")
        return text["bismillah"]
    return next(ayah for ayah in text["ayahs"] if ayah["n"] == item["ayah"])


def _scaled_boxes(config: dict[str, Any]) -> dict[str, list[float]]:
    scale_x = 1920 / 1672
    scale_y = 1080 / 940
    return {
        name: [box[0] * scale_x, box[1] * scale_y, box[2] * scale_x, box[3] * scale_y]
        for name, box in config["display"]["boxes"].items()
    }


def _chunk_ranges(words: list[dict[str, Any]], count: int, font_paths: dict[str, Path],
                  minimum: int) -> list[tuple[int, int]]:
    pause_marks = {chr(codepoint) for codepoint in (0x06D6, 0x06D7, 0x06D8, 0x06D9,
                                                     0x06DA, 0x06DB, 0x06DC, 0x06DD)}
    fonts = {field: _font(font_paths[field], minimum) for field in ("arabic", "translit")}

    def phrase_width(start: int, end: int) -> float:
        return max(
            fonts[field].getlength(" ".join(word[field] for word in words[start:end]))
            for field in ("arabic", "translit")
        )

    ranges = []
    start = 0
    for chunk_index in range(count):
        if chunk_index == count - 1:
            end = len(words)
        else:
            remaining_chunks = count - chunk_index
            remaining_width = phrase_width(start, len(words))
            target = remaining_width / remaining_chunks
            candidates = range(start + 1, len(words) - remaining_chunks + 2)
            pause_candidates = [
                candidate for candidate in candidates
                if any(mark in words[candidate - 1]["arabic"] for mark in pause_marks)
            ]
            suitable_pauses = [
                candidate for candidate in pause_candidates
                if 0.75 * target <= phrase_width(start, candidate) <= 1.25 * target
            ]
            preferred = set(suitable_pauses)
            scored = []
            for candidate in candidates:
                width = phrase_width(start, candidate)
                final_width = phrase_width(candidate, len(words))
                if final_width < target * 0.5 and candidate != len(words) - remaining_chunks + 1:
                    continue
                pause_penalty = 0 if candidate in preferred else 1
                scored.append((pause_penalty, abs(width - target), candidate))
            if not scored:
                scored = [(0 if candidate in preferred else 1,
                           abs(phrase_width(start, candidate) - target), candidate)
                          for candidate in candidates]
            end = min(scored)[2]
        ranges.append((start, end))
        start = end
    return ranges


def _chunk_units(item: dict[str, Any], text: dict[str, Any], config: dict[str, Any],
                 font_paths: dict[str, Path], boxes: dict[str, list[float]]) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    entry = _lookup(text, item)
    words = entry["words"]
    source_words = {word["i"]: word for word in item.get("words", [])}
    for count in range(2, len(words) + 1):
        ranges = _chunk_ranges(
            words, count, font_paths, config["display"]["min_font_size"],
        )
        chunks = []
        valid = True
        for start, end in ranges:
            chunk_words = words[start:end]
            texts = {
                "arabic": " ".join(word["arabic"] for word in chunk_words),
                "translit": " ".join(word["translit"] for word in chunk_words),
                "english": entry["english"],
            }
            try:
                sizes = {
                    field: _fit(texts[field], boxes[field], font_paths[field], config["display"]["min_font_size"],
                                config["display"]["max_font_size"], config["display"]["max_lines"])[0]
                    for field in ("arabic", "translit", "english")
                }
            except LayoutError:
                valid = False
                break
            timed = [source_words.get(word["i"]) for word in chunk_words]
            timed = [word for word in timed if word is not None]
            chunks.append({
                "items": [item],
                "start": timed[0]["start"] if timed else item["start"],
                "end": timed[-1]["end"] if timed else item["end"],
                "texts": texts,
                "sizes": sizes,
            })
        if valid:
            return chunks, None
    return [], {
        "type": "english_overflow" if not _fits_at_minimum(
            entry["english"], boxes["english"], font_paths["english"], config
        ) else "text_overflow",
        "ayah": item["ayah"],
        "severity": "warn",
        "detail": "Ayah cannot be displayed within the configured minimum layout",
    }


def _fits_at_minimum(text: str, box: list[float], font_path: Path, config: dict[str, Any]) -> bool:
    try:
        _fit(text, box, font_path, config["display"]["min_font_size"], config["display"]["min_font_size"],
             config["display"]["max_lines"])
    except LayoutError:
        return False
    return True


def _item_start(item: dict[str, Any]) -> float:
    words = item.get("words") or []
    return float(words[0]["start"]) if words else float(item["start"])


def _make_units(
    text: dict[str, Any], timeline: dict[str, Any], config: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    display = config["display"]
    boxes = _scaled_boxes(config)
    fields = ("arabic", "translit", "english")
    font_paths = {field: resolve_path(display[f"{field}_font"]) for field in fields}
    items = timeline.get("items", [])
    units: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    index = 0
    while index < len(items):
        group = [items[index]]
        if group[0]["kind"] == "ayah":
            while len(group) < display["max_ayahs_per_screen"] and index + len(group) < len(items):
                candidate = items[index + len(group)]
                if candidate["kind"] != "ayah":
                    break
                candidate_group = group + [candidate]
                try:
                    for field in fields:
                        joined = "\n".join(
                            f"[{entry['ayah']}] {_lookup(text, entry)[field]}" for entry in candidate_group
                        )
                        _fit(joined, boxes[field], font_paths[field], display["group_min_font_size"],
                             display["max_font_size"], display["max_lines"])
                except LayoutError:
                    break
                group = candidate_group

        texts = {
            field: "\n".join(
                _lookup(text, entry)[field] if entry["kind"] == "bismillah"
                else f"[{entry['ayah']}] {_lookup(text, entry)[field]}"
                for entry in group
            )
            for field in fields
        }
        try:
            sizes = {
                field: _fit(texts[field], boxes[field], font_paths[field], display["min_font_size"],
                    display["max_font_size"], display["max_lines"])[0]
                for field in fields
            }
        except LayoutError:
            if len(group) > 1:
                group = group[:1]
                texts = {
                    field: f"[{group[0]['ayah']}] {_lookup(text, group[0])[field]}" for field in fields
                }
                try:
                    sizes = {
                        field: _fit(texts[field], boxes[field], font_paths[field], display["min_font_size"],
                                    display["max_font_size"], display["max_lines"])[0]
                        for field in fields
                    }
                except LayoutError:
                    sizes = {}
            else:
                if group[0]["kind"] == "ayah":
                    chunks, flag = _chunk_units(group[0], text, config, font_paths, boxes)
                    if chunks:
                        units.extend(chunks)
                        index += 1
                        continue
                    if flag:
                        flags.append(flag)
                sizes = {}
            if not sizes and not (group[0]["kind"] == "ayah" and len(group) == 1):
                flags.append({
                    "type": "text_overflow",
                    "ayah": group[0].get("ayah"),
                    "severity": "warn",
                    "detail": "Text does not fit at the configured minimum font size",
                })
        units.append({
            "items": group,
            "start": _item_start(group[0]),
            "end": group[-1]["end"],
            "texts": texts,
            "sizes": sizes,
        })
        index += len(group)
    return units, flags


def build_ass(
    text: dict[str, Any], timeline: dict[str, Any], config: dict[str, Any], template: str
) -> tuple[str, list[dict[str, Any]]]:
    units, flags = _make_units(text, timeline, config)
    boxes = _scaled_boxes(config)
    lines = [
        line for line in template.splitlines()
        if not line.startswith("Style:") and not line.startswith("Dialogue:")
    ]
    style_fonts = {
        "Arabic": _font_family(resolve_path(config["display"]["arabic_font"])),
        "Transliteration": _font_family(resolve_path(config["display"]["translit_font"])),
        "English": _font_family(resolve_path(config["display"]["english_font"])),
        "Header": _font_family(resolve_path(config["display"]["reciter_font"])),
    }
    for field, style in (("arabic", "Arabic"), ("translit", "Transliteration"), ("english", "English")):
        font_name = style_fonts[style]
        lines.append(
            f"Style: {style},{font_name},48,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80101010,"
            "0,0,0,0,100,100,0,0,1,2,1,5,20,20,10,1"
        )
    lines.append(
        f"Style: Header,{style_fonts['Header']},30,&H00FFFFFF,&H00FFFFFF,&H00101010,&H80101010,"
        "0,0,0,0,100,100,0,0,1,2,1,5,10,10,10,1"
    )
    duration = float(timeline.get("duration", 0.0))
    if duration <= 0:
        duration = max((float(item.get("end", 0)) for item in timeline.get("items", [])), default=0.0)
    header_box = boxes["reciter"]
    title_box = boxes["title"]
    reciter_position = f"{{\\fs30\\pos({header_box[0] + header_box[2] / 2:.1f},{header_box[1] + header_box[3] / 2:.1f})}}"
    title_position = f"{{\\fs30\\pos({title_box[0] + title_box[2] / 2:.1f},{title_box[1] + title_box[3] / 2:.1f})}}"
    lines.append(
        f"Dialogue: 0,0:00:00.00,{_ass_time(duration)},Header,,0,0,0,,"
        f"{reciter_position}{_escape(config['reciter']['name_ar'])}"
    )
    title = text["title_ar"] if config["display"]["header_title"] == "arabic_only" else (
        f"{text['title_ar']} / {text['title_en']}"
    )
    lines.append(
        f"Dialogue: 0,0:00:00.00,{_ass_time(duration)},Header,,0,0,0,,"
        f"{title_position}{_escape(title)}"
    )
    timeline_duration = float(timeline.get("duration", 0.0))
    visible_starts = [
        max(0.0, unit["start"] - config["display"]["lead_time_s"]) for unit in units
    ]
    for index, unit in enumerate(units):
        start = visible_starts[index]
        end = visible_starts[index + 1] if index + 1 < len(units) else timeline_duration or unit["end"]
        end = max(start, end)
        for field, style in (("arabic", "Arabic"), ("translit", "Transliteration"), ("english", "English")):
            box = boxes[field]
            size, wrapped = _fit(
                unit["texts"][field], box,
                resolve_path(config["display"][f"{field}_font"]),
                config["display"]["min_font_size"],
                unit["sizes"].get(field, config["display"]["max_font_size"]),
                config["display"]["max_lines"],
            )
            position = f"{{\\fs{size}\\pos({box[0] + box[2] / 2:.1f},{box[1] + box[3] / 2:.1f})}}"
            wrapped_text = _escape("\\N".join(wrapped)).replace("\\\\N", "\\N")
            lines.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},{style},,0,0,0,,"
                f"{position}{wrapped_text}"
            )
    return "\n".join(lines) + "\n", flags


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate ASS subtitles from text and timeline JSON.")
    parser.add_argument("--surah", type=int, required=True)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--timeline")
    parser.add_argument("--text")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        work = resolve_path(config["paths"]["work_dir"]) / str(args.surah)
        text_path = Path(args.text) if args.text else work / "text.json"
        timeline_path = Path(args.timeline) if args.timeline else work / "timeline.json"
        output = Path(args.output) if args.output else work / "subtitles.ass"
        text = json.loads(text_path.read_text(encoding="utf-8"))
        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
        template = resolve_path(config["paths"]["template_ass"]).read_text(encoding="utf-8")
        ass, flags = build_ass(text, timeline, config, template)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(ass, encoding="utf-8")
        print(f"generated {output} ({len(flags)} layout flags)")
        return 0
    except (ConfigError, LayoutError, OSError, json.JSONDecodeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
