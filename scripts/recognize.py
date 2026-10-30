"""Recognize Arabic audio with word timestamps and per-take caching."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any, Callable

try:
    from normalize import normalize_arabic
    from pipeline_common import ConfigError, file_hash, load_config, resolve_path
except ModuleNotFoundError:
    from scripts.normalize import normalize_arabic
    from scripts.pipeline_common import ConfigError, file_hash, load_config, resolve_path


CHUNK_LENGTH_S = 30
STRIDE_LENGTH_S = 5
FAKE_SCENARIOS = (
    "clean", "repeat_last", "restart_mid", "back_4", "back_5",
    "two_takes", "errors", "no_pause", "no_bismillah",
)


def _device_index(device: str) -> int:
    if device == "cpu":
        return -1
    if device == "cuda":
        return 0
    if device == "auto":
        try:
            import torch
        except ImportError:
            return -1
        return 0 if torch.cuda.is_available() else -1
    raise ConfigError("models.device must be 'auto', 'cpu', or 'cuda'")


def _load_recognizer(model_name: str, device: str) -> Callable[..., dict[str, Any]]:
    try:
        from transformers import pipeline
    except ImportError as exc:
        raise ConfigError("Recognition requires the 'transformers' package") from exc
    try:
        return pipeline(
            "automatic-speech-recognition",
            model=model_name,
            device=_device_index(device),
            chunk_length_s=CHUNK_LENGTH_S,
            stride_length_s=STRIDE_LENGTH_S,
        )
    except Exception as exc:
        raise ConfigError(f"Could not load recognizer model {model_name!r}: {exc}") from exc


def _timestamp(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        return None
    if value[0] is None or value[1] is None:
        return None
    try:
        start, end = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
        return None
    return start, end


def parse_word_chunks(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a Transformers word-timestamp response to the project schema."""
    chunks = result.get("chunks")
    if not isinstance(chunks, list):
        raise ConfigError("Recognizer returned no word timestamps; enable word timestamps")
    words = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        text = chunk.get("text", "").strip()
        timing = _timestamp(chunk.get("timestamp"))
        if not text or timing is None:
            continue
        score = chunk.get("score")
        confidence = None
        if score is not None:
            try:
                confidence = float(score)
            except (TypeError, ValueError):
                raise ConfigError("Recognizer returned a non-numeric word confidence")
            if not math.isfinite(confidence):
                raise ConfigError("Recognizer returned a non-finite word confidence")
            confidence = min(1.0, max(0.0, confidence))
        words.append({
            "text": text,
            "norm": normalize_arabic(text),
            "start": timing[0],
            "end": timing[1],
            "conf": confidence,
        })
    if not words:
        raise ConfigError("Recognizer returned no usable word timestamps")
    return words


def _cached_result(path: Path, audio: Path, take: int, model: str) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    duration = result.get("duration")
    try:
        duration_value = float(duration)
    except (TypeError, ValueError):
        return None
    if (not math.isfinite(duration_value) or duration_value < 0
            or result.get("take") != take
            or result.get("audio") != str(audio)
            or result.get("audio_hash") != file_hash(audio)
            or result.get("model") != model
            or not isinstance(result.get("words"), list)
            or not result["words"]):
        return None
    for word in result["words"]:
        if not isinstance(word, dict) or not word.get("text") or _timestamp(
            (word.get("start"), word.get("end"))
        ) is None:
            return None
        if word.get("norm") != normalize_arabic(word["text"]):
            return None
        if word.get("conf") is not None:
            try:
                confidence = float(word["conf"])
            except (TypeError, ValueError):
                return None
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                return None
    if duration_value < max(float(word["end"]) for word in result["words"]):
        return None
    return result


def recognize_take(
    audio: Path,
    take: int,
    output: Path,
    config: dict[str, Any],
    recognizer: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not audio.is_file():
        raise ConfigError(f"Audio file does not exist: {audio}")
    model = config["models"]["recognizer"]
    cached = _cached_result(output, audio, take, model)
    if cached is not None:
        return cached
    recognizer = recognizer or _load_recognizer(model, config["models"]["device"])
    try:
        result = recognizer(
            str(audio),
            return_timestamps="word",
            chunk_length_s=CHUNK_LENGTH_S,
            stride_length_s=STRIDE_LENGTH_S,
        )
    except Exception as exc:
        raise ConfigError(f"Recognition failed for take {take} ({audio}): {exc}") from exc
    words = parse_word_chunks(result)
    output_data = {
        "take": take,
        "audio": str(audio),
        "audio_hash": file_hash(audio),
        "duration": max(word["end"] for word in words),
        "model": model,
        "words": words,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(output_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_data


def _fixture_words(fixture: dict[str, Any], include_bismillah: bool) -> list[tuple[int | None, dict[str, Any]]]:
    result = []
    if include_bismillah and fixture.get("bismillah"):
        result.extend((None, word) for word in fixture["bismillah"]["words"])
    for ayah in fixture["ayahs"]:
        result.extend((ayah["n"], word) for word in ayah["words"])
    return result


def _fake_take(
    fixture: dict[str, Any], take: int, scenario: str, rng: random.Random
) -> dict[str, Any]:
    include_bismillah = scenario != "no_bismillah"
    source = _fixture_words(fixture, include_bismillah)
    if scenario == "repeat_last":
        source = source[: len(source) // 2] + source[max(0, len(source) // 2 - 3):]
    elif scenario == "restart_mid":
        source = source[: max(1, len(source) // 3)] + source[max(0, len(source) // 3 - 2):]
    elif scenario == "back_4":
        source = source[: max(1, len(source) - 8)] + source[max(1, len(source) - 20):]
    elif scenario == "back_5":
        source = source[: max(1, len(source) - 10)] + source[max(1, len(source) - 25):]
    elif scenario == "errors":
        source = source[:]
        if len(source) > 5:
            source.pop(3)
            source.insert(5, (None, {"norm": "inserted", "arabic": "inserted", "translit": "inserted"}))
            source[7] = (source[7][0], {**source[7][1], "norm": "substituted", "arabic": "substituted"})
    words = []
    current = 0.0
    for index, (_, source_word) in enumerate(source):
        gap = 0.05 if scenario == "no_pause" and index else rng.uniform(0.18, 0.42)
        current += gap
        duration = rng.uniform(0.28, 0.55)
        text = source_word["norm"]
        words.append({
            "text": text,
            "norm": normalize_arabic(text),
            "start": round(current, 3),
            "end": round(current + duration, 3),
            "conf": round(rng.uniform(0.55, 0.99), 3),
        })
        current += duration
    return {
        "take": take,
        "audio": f"fake://{scenario}/take_{take}",
        "duration": round(current, 3),
        "model": "fake",
        "words": words,
    }


def generate_fake_scenario(surah: int, scenario: str, project_root: Path = Path(".")) -> list[dict[str, Any]]:
    if scenario not in FAKE_SCENARIOS:
        raise ConfigError(f"Unknown fake scenario {scenario!r}; choose from {', '.join(FAKE_SCENARIOS)}")
    fixture_path = project_root / "tests" / "fixtures" / f"surah_{surah}.json"
    if not fixture_path.is_file():
        raise ConfigError(f"Fake scenarios require fixture {fixture_path}")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    rng = random.Random(20261030 + surah + FAKE_SCENARIOS.index(scenario))
    count = 2 if scenario == "two_takes" else 1
    takes = [_fake_take(fixture, take, scenario, rng) for take in range(1, count + 1)]
    work = project_root / "work" / str(surah)
    for take_data in takes:
        output = work / f"take_{take_data['take']}" / "words.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(take_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return takes


def main() -> int:
    parser = argparse.ArgumentParser(description="Recognize one ordered audio take.")
    parser.add_argument("--surah", type=int, required=True)
    parser.add_argument("--audio")
    parser.add_argument("--take", type=int)
    parser.add_argument("--fake", action="store_true")
    parser.add_argument("--scenario", choices=FAKE_SCENARIOS)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        if not 1 <= args.surah <= 114:
            raise ConfigError("surah must be 1-114")
        config = load_config(args.config)
        if args.fake:
            if not args.scenario:
                raise ConfigError("--scenario is required with --fake")
            takes = generate_fake_scenario(args.surah, args.scenario, Path(__file__).resolve().parents[1])
            print(f"generated fake scenario {args.scenario}: {len(takes)} take(s)")
            return 0
        if not args.audio or not args.take or args.take < 1:
            raise ConfigError("--audio and a positive --take are required without --fake")
        audio = Path(args.audio).expanduser().resolve()
        default_output = (
            resolve_path(config["paths"]["work_dir"]) / str(args.surah)
            / f"take_{args.take}" / "words.json"
        )
        output = Path(args.output).expanduser().resolve() if args.output else default_output
        data = recognize_take(audio, args.take, output, config)
        print(f"recognized take {args.take}: {len(data['words'])} words -> {output}")
        return 0
    except (ConfigError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print("Check the audio path, model configuration, and word-timestamp support.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
