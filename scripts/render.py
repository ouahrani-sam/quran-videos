"""Cut accepted take spans and create the edited-audio timeline."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _audio_paths(work_dir: Path) -> dict[int, str]:
    result: dict[int, str] = {}
    for path in work_dir.glob("take_*/words.json"):
        data = _read(path)
        if isinstance(data.get("take"), int) and isinstance(data.get("audio"), str):
            result[data["take"]] = data["audio"]
    return result


def _run_ffmpeg(args: list[str]) -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("FFmpeg is required for rendering audio cuts; install it and retry.")
    try:
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args],
                       check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise RuntimeError(f"FFmpeg failed while rendering audio: {detail}") from exc


def render_surah(work_dir: Path, output_audio: Path | None = None) -> dict[str, Any]:
    """Render accepted review items and return/write timeline.json."""
    review = _read(work_dir / "review.json")
    items = review.get("items")
    if not isinstance(items, list):
        raise ValueError("review.json has no valid items list")
    audio = _audio_paths(work_dir)
    output_audio = output_audio or work_dir / "edited.wav"
    output_audio.parent.mkdir(parents=True, exist_ok=True)

    timeline_items: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="quran-render-", dir=work_dir) as temp:
        clips: list[Path] = []
        offset = 0.0
        for index, item in enumerate(items):
            take = item.get("take")
            source = audio.get(take)
            if not source:
                raise ValueError(f"No source audio metadata for take {take}")
            source_path = Path(source)
            if not source_path.is_absolute():
                source_path = Path.cwd() / source_path
            start = float(item["cut_start"])
            end = float(item["cut_end"])
            if start < 0 or end <= start:
                raise ValueError(f"Invalid cut range for span {item.get('span_id')}")
            clip = Path(temp) / f"clip-{index}.wav"
            _run_ffmpeg([
                "-i", str(source_path), "-ss", f"{start:.6f}", "-to", f"{end:.6f}",
                "-vn", "-acodec", "pcm_s16le", str(clip),
            ])
            clips.append(clip)
            duration = end - start
            timeline_item = {
                "kind": item.get("kind"),
                "ayah": item.get("ayah"),
                "start": round(offset, 6),
                "end": round(offset + duration, 6),
                "words": [
                    {**word, "start": round(offset + float(word["start"]) - start, 6),
                     "end": round(offset + float(word["end"]) - start, 6)}
                    for word in item.get("words", [])
                ],
            }
            timeline_items.append(timeline_item)
            offset += duration

        if not clips:
            raise ValueError("No accepted review items to render")
        concat_list = Path(temp) / "concat.txt"
        concat_list.write_text(
            "".join(f"file '{clip.as_posix()}'\n" for clip in clips), encoding="utf-8"
        )
        _run_ffmpeg([
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy", str(output_audio),
        ])

    timeline = {
        "surah": review.get("surah"),
        "audio": str(output_audio),
        "duration": round(offset, 6),
        "items": timeline_items,
    }
    (work_dir / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return timeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Render reviewed audio and timeline.")
    parser.add_argument("--surah", type=int, required=True)
    parser.add_argument("--work-dir", default="work")
    args = parser.parse_args()
    timeline = render_surah(Path(args.work_dir) / str(args.surah))
    print(f"rendered {timeline['duration']:.2f}s: {timeline['audio']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
