"""Run the Quran pipeline stages with manifest caching and review gating."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .pipeline_common import (
        ConfigError, configure_logging, file_hash, load_config, load_manifest,
        record_stage, resolve_path, save_manifest, stage_is_current,
    )
except ImportError:
    from pipeline_common import (
        ConfigError, configure_logging, file_hash, load_config, load_manifest,
        record_stage, resolve_path, save_manifest, stage_is_current,
    )


STAGES = ("fetch", "recognize", "match", "align", "review", "render")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build one Quran surah video from ordered audio takes.")
    parser.add_argument("--surah", type=int, required=True)
    parser.add_argument("--audio", nargs="+", required=True)
    parser.add_argument("--start-ayah", type=int, default=1)
    parser.add_argument("--stage", choices=STAGES)
    parser.add_argument("--force", choices=STAGES)
    parser.add_argument("--config", default="config.yaml")
    return parser.parse_args()


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"Cannot read stage output {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"Stage output must be an object: {path}")
    return value


def _stage_range(stage: str | None, force: str | None) -> tuple[int, int]:
    first = STAGES.index(stage) if stage else 0
    if force:
        first = min(first, STAGES.index(force))
    return first, STAGES.index(stage) if stage else len(STAGES) - 1


def run_pipeline(args: argparse.Namespace) -> int:
    if not 1 <= args.surah <= 114:
        raise ConfigError("--surah must be between 1 and 114")
    if args.start_ayah < 1:
        raise ConfigError("--start-ayah must be positive")
    config = load_config(args.config)
    audio = [Path(item).expanduser().resolve() for item in args.audio]
    missing = [str(path) for path in audio if not path.is_file()]
    if missing:
        raise ConfigError(f"Audio file does not exist: {', '.join(missing)}")

    root = Path(config["paths"]["work_dir"]).expanduser()
    work = root / str(args.surah)
    work.mkdir(parents=True, exist_ok=True)
    manifest_path = work / "manifest.json"
    manifest = load_manifest(manifest_path)
    first, last = _stage_range(args.stage, args.force)
    started = time.monotonic()

    def output(name: str, inputs: Any, section: Any, paths: list[Path], action) -> None:
        nonlocal manifest
        if stage_is_current(manifest, name, inputs, section) and all(path.exists() for path in paths):
            print(f"{name} cached")
            return
        action()
        record_stage(manifest, name, inputs, section, [str(path) for path in paths])
        save_manifest(manifest_path, manifest)
        print(f"{name} {time.monotonic() - started:.1f}s complete")

    text_path = work / "text.json"
    if first <= 0 <= last:
        from scripts.fetch_text import fetch_text
        output("fetch", {"surah": args.surah}, config["api"],
               [text_path], lambda: text_path.write_text(
                   json.dumps(fetch_text(args.surah, config), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8"))

    take_outputs = [work / f"take_{i + 1}" / "words.json" for i in range(len(audio))]
    if first <= 1 <= last:
        from scripts.recognize import recognize_take
        for index, (source, target) in enumerate(zip(audio, take_outputs), 1):
            output("recognize", {"take": index, "audio_hash": file_hash(source)},
                   config["models"], [target],
                   lambda source=source, index=index, target=target:
                   recognize_take(source, index, target, config))

    spans_path = work / "spans.json"
    if first <= 2 <= last:
        from scripts.match import run_matching
        takes = [_read(path) for path in take_outputs]
        text = _read(text_path)
        output("match", {"takes": takes, "text": text}, config["matching"], [spans_path],
               lambda: spans_path.write_text(
                   json.dumps(run_matching(text, takes, config), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8"))

    aligned_path = work / "aligned.json"
    if first <= 3 <= last:
        from scripts.align import run_alignment
        text = _read(text_path)
        spans = _read(spans_path)
        output("align", {"spans": spans}, config["alignment"], [aligned_path],
               lambda: aligned_path.write_text(
                   json.dumps(run_alignment(text, spans, config, str(Path.cwd())), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8"))

    review_path = work / "review.json"
    if first <= 4 <= last:
        from scripts.review import write_review
        output("review", {"aligned": _read(aligned_path), "spans": _read(spans_path)},
               {}, [review_path, work / "review.html"], lambda: write_review(work))
        review = _read(review_path)
        if review.get("blocked") and args.stage != "render":
            print(f"review required: {work / 'review.html'}")
            return 2

    if first <= 5 <= last:
        from scripts.render import render_surah
        output("render", {"review": _read(review_path)}, {}, [work / "timeline.json"],
               lambda: render_surah(work))
    print(f"pipeline complete in {time.monotonic() - started:.1f}s")
    return 0


def main() -> int:
    args = parse_args()
    try:
        configure_logging(Path("logs") / f"{args.surah}.log")
        return run_pipeline(args)
    except (ConfigError, OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
