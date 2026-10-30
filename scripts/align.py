"""CTC forced alignment implementation owned by SPECIALIST."""

from __future__ import annotations

import argparse
import inspect
import json
import logging
import math
import struct
import subprocess
import wave
from pathlib import Path
from typing import Any, Callable

from scripts.normalize import normalize_arabic

logger = logging.getLogger(__name__)

# Cache decoded PCM audio per audio path: {path: (framerate, tuple_of_samples)}
_AUDIO_DECODE_CACHE: dict[str, tuple[int, tuple[float, ...]]] = {}


def _ctc_forced_align_span(
    audio_path: str,
    cut_start_est: float,
    cut_end_est: float,
    normalized_words: list[str],
    model_name: str = "MahmoudAshraf/mms-300m-1130-forced-aligner",
    device: str = "auto",
) -> list[dict[str, Any]]:
    """Run the documented ctc-forced-aligner pipeline on one audio span.

    TODO-VERIFY: BUILDER to verify against desktop ctc-forced-aligner package version
    and CUDA runtime.
    """
    try:
        from ctc_forced_aligner import (  # type: ignore
            generate_emissions,
            get_alignments,
            get_spans,
            load_alignment_model,
            load_audio,
            postprocess_results,
            preprocess_text,
        )
        import torch  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "ctc-forced-aligner package is not installed in this environment."
        ) from exc

    actual_device = device
    if device == "auto":
        actual_device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if actual_device == "cuda" else torch.float32
    load_kwargs = {"dtype": dtype}
    params = inspect.signature(load_alignment_model).parameters
    if "model_name_or_path" in params:
        load_kwargs["model_name_or_path"] = model_name
    model, tokenizer = load_alignment_model(actual_device, **load_kwargs)
    waveform = load_audio(audio_path, model.dtype, model.device)
    sample_rate = getattr(model, "config", None)
    sample_rate = getattr(sample_rate, "sampling_rate", 16000)
    start_sample = max(0, round(cut_start_est * sample_rate))
    end_sample = min(waveform.shape[-1], round(cut_end_est * sample_rate))
    if end_sample <= start_sample:
        raise ValueError("Alignment span has no audio samples")
    waveform = waveform[..., start_sample:end_sample]
    emissions, stride = generate_emissions(model, waveform)
    tokens, text_starred = preprocess_text(
        " ".join(normalized_words), romanize=True, language="ara"
    )
    segments, scores, blank_token = get_alignments(emissions, tokens, tokenizer)
    spans = get_spans(tokens, segments, blank_token)
    results = postprocess_results(text_starred, spans, stride, scores)
    aligned: list[dict[str, Any]] = []
    for result in results:
        if not isinstance(result, dict):
            raise ValueError("CTC aligner returned a malformed word record")
        start = result.get("start", result.get("start_time"))
        end = result.get("end", result.get("end_time"))
        conf = result.get("score", result.get("confidence", 0.0))
        aligned.append({
            "start": float(start) + cut_start_est,
            "end": float(end) + cut_start_est,
            "conf": float(conf),
        })
    return aligned


def _load_audio_pcm_samples(audio_path: str) -> tuple[int, tuple[float, ...]]:
    """Decode audio file (PCM WAV or M4A/AAC) to 16-bit mono samples.

    Raises FileNotFoundError if file is missing, and RuntimeError on decoding failure.
    """
    if audio_path in _AUDIO_DECODE_CACHE:
        return _AUDIO_DECODE_CACHE[audio_path]

    p = Path(audio_path)
    if not p.is_file():
        raise FileNotFoundError(f"Audio file '{audio_path}' not found.")

    # Try standard wave module first for WAV files
    if p.suffix.lower() == ".wav":
        try:
            with wave.open(str(p), "rb") as wf:
                n_channels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                framerate = wf.getframerate()
                if sampwidth == 2:
                    frames = wf.readframes(wf.getnframes())
                    values = struct.unpack("<" + "h" * (len(frames) // 2), frames)
                    if n_channels > 1:
                        values = tuple(
                            sum(values[i:i + n_channels]) / n_channels
                            for i in range(0, len(values), n_channels)
                        )
                    _AUDIO_DECODE_CACHE[audio_path] = (framerate, values)
                    return framerate, values
        except Exception as exc:
            logger.warning(
                "Python wave module failed on %s (%s); falling back to FFmpeg",
                audio_path,
                exc,
            )

    # Use FFmpeg to decode .m4a or any audio format to 16-bit mono 16kHz PCM
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-threads",
        "1",
        "-i",
        str(p),
        "-f",
        "s16le",
        "-ac",
        "1",
        "-ar",
        "16000",
        "pipe:1",
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as exc:
        logger.error("FFmpeg executable not found on system path: %s", exc)
        raise RuntimeError(
            "FFmpeg is required to decode audio recordings (.m4a)."
        ) from exc

    if proc.returncode != 0:
        err_msg = proc.stderr.decode("utf-8", errors="replace").strip()
        logger.error("Failed to decode audio '%s' with FFmpeg: %s", audio_path, err_msg)
        raise RuntimeError(f"Failed to decode audio file '{audio_path}': {err_msg}")

    raw = proc.stdout
    framerate = 16000
    if len(raw) % 2 != 0:
        raw = raw[:-1]
    values = struct.unpack("<" + "h" * (len(raw) // 2), raw)
    _AUDIO_DECODE_CACHE[audio_path] = (framerate, values)
    return framerate, values


def _detect_silence_offsets(
    audio_path: str,
    t_start: float,
    t_end: float,
    snap_max_s: float,
    threshold_db: float,
) -> tuple[float, float]:
    """Find the nearest low-energy boundary around each proposed cut."""
    if not audio_path or audio_path.startswith("fake://"):
        return t_start, t_end

    try:
        framerate, values = _load_audio_pcm_samples(audio_path)
    except Exception as exc:
        logger.error("Silence detection failed for '%s': %s", audio_path, exc)
        raise

    if not values:
        return t_start, t_end

    window = max(1, round(framerate * 0.02))
    energies: list[float] = []
    for pos in range(0, len(values), window):
        block = values[pos:pos + window]
        rms = math.sqrt(sum(x * x for x in block) / len(block)) if block else 0.0
        energies.append(20.0 * math.log10(max(rms / 32768.0, 1e-12)))

    def nearest_silence(target: float, left: bool) -> float:
        lo = max(0.0, target - snap_max_s)
        hi = target if left else target + snap_max_s
        candidates = [
            (abs((i * window / framerate) - target), i * window / framerate)
            for i, db in enumerate(energies)
            if lo <= i * window / framerate <= hi and db <= threshold_db
        ]
        return min(candidates)[1] if candidates else target

    return nearest_silence(t_start, True), nearest_silence(t_end, False)


def _validate_aligned_words(
    aligned: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    span_start: float,
    span_end: float,
) -> None:
    if len(aligned) != len(expected):
        raise ValueError(
            f"Aligner returned {len(aligned)} words for {len(expected)} expected words"
        )
    previous_end = span_start
    for index, word in enumerate(aligned):
        try:
            start = float(word["start"])
            end = float(word["end"])
            conf = float(word["conf"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Malformed alignment record at word {index}") from exc
        if not all(math.isfinite(v) for v in (start, end, conf)):
            raise ValueError(f"Non-finite alignment value at word {index}")
        if not 0 <= conf <= 1 or start < span_start or end > span_end or end <= start:
            raise ValueError(f"Invalid alignment bounds/confidence at word {index}")
        if start < previous_end:
            raise ValueError(f"Alignment words are out of order at word {index}")
        previous_end = end


def _generate_synthetic_aligned_words(
    expected_words: list[dict[str, Any]],
    start_time: float,
    end_time: float,
    conf_override: float | None = None,
) -> list[dict[str, Any]]:
    """Synthesize evenly distributed word timestamps for testing and fake mode."""
    n = len(expected_words)
    if n == 0:
        return []

    total_duration = max(0.1, end_time - start_time)
    word_dur = total_duration / n
    aligned: list[dict[str, Any]] = []

    for idx, w in enumerate(expected_words):
        w_start = start_time + (idx * word_dur)
        w_end = w_start + (word_dur * 0.85)  # leave slight gap
        aligned.append({
            "i": w["i"],
            "start": round(w_start, 2),
            "end": round(w_end, 2),
            "conf": conf_override if conf_override is not None else 0.95,
        })
    return aligned


def run_alignment(
    text: dict[str, Any],
    spans: dict[str, Any],
    cfg: dict[str, Any],
    project_root: str,
    aligner_fn: Callable[..., list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Execute forced alignment across accepted spans, enforcing SPEC.md contracts."""
    surah_num = text.get("surah")
    align_cfg = cfg.get("alignment", {})
    silence_snap_max_s = align_cfg.get("silence_snap_max_s", 0.30)
    silence_threshold_db = align_cfg.get("silence_threshold_db", -40)
    trim_last_word = align_cfg.get("trim_last_word_silence", True)
    min_word_conf = align_cfg.get("min_word_conf", 0.30)
    max_duration_factor = align_cfg.get("max_ayah_seconds_factor", 3.0)

    models_cfg = cfg.get("models", {})
    aligner_model = models_cfg.get(
        "aligner", "MahmoudAshraf/mms-300m-1130-forced-aligner"
    )
    device = models_cfg.get("device", "auto")

    # Index expected text words
    bismillah_words: list[dict[str, Any]] = []
    if text.get("bismillah"):
        bismillah_words = text["bismillah"].get("words", [])

    ayah_words_map: dict[int, list[dict[str, Any]]] = {}
    for a in text.get("ayahs", []):
        ayah_words_map[a["n"]] = a.get("words", [])

    # Index spans by id
    spans_by_id = {s["id"]: s for s in spans.get("spans", [])}
    kept_ids = spans.get("kept_ids", [])

    # Read take audio metadata if available
    take_audio_map: dict[int, str] = {}
    work_surah_dir = Path(project_root) / "work" / str(surah_num)
    if work_surah_dir.exists():
        for td in work_surah_dir.glob("take_*"):
            w_file = td / "words.json"
            if w_file.exists():
                try:
                    with open(w_file, encoding="utf-8") as wf:
                        data = json.load(wf)
                        take_audio_map[data.get("take", 1)] = data.get("audio", "")
                except Exception:
                    pass

    items: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []

    for span_id in kept_ids:
        sp = spans_by_id.get(span_id)
        if not sp:
            continue

        take_num = sp["take"]
        kind = sp["kind"]
        ayah_num = sp.get("ayah")
        span_start = sp["start"]
        span_end = sp["end"]

        # Fetch authoritative words from text.json
        if kind == "bismillah":
            target_words = bismillah_words
        else:
            target_words = ayah_words_map.get(ayah_num, [])

        if not target_words:
            continue

        norm_words = [
            w.get("norm", normalize_arabic(w["arabic"]))
            for w in target_words
        ]

        audio_src = take_audio_map.get(take_num, "")

        # Strict routing: never fallback to synthetic alignment if real audio path is missing
        aligned_words: list[dict[str, Any]] = []
        if aligner_fn is not None:
            aligned_words = aligner_fn(
                audio_src,
                span_start,
                span_end,
                norm_words,
                target_words,
            )
        elif audio_src.startswith("fake://"):
            aligned_words = _generate_synthetic_aligned_words(
                target_words,
                span_start,
                span_end,
                conf_override=sp.get("conf", 0.95),
            )
        elif not audio_src:
            raise ValueError(
                f"Missing audio path metadata for take {take_num} in work/{surah_num}/take_{take_num}/words.json. "
                "Synthetic alignment is forbidden for missing real audio metadata."
            )
        else:
            aligned_words = _ctc_forced_align_span(
                audio_src,
                span_start,
                span_end,
                norm_words,
                model_name=aligner_model,
                device=device,
            )

        _validate_aligned_words(aligned_words, target_words, span_start, span_end)

        # Ensure word times strictly use text.json Word.i
        for idx, aw in enumerate(aligned_words):
            aw["i"] = target_words[idx]["i"]

        first_w = aligned_words[0]
        last_w = aligned_words[-1]

        # Calculate silence snapping
        raw_cut_start = max(0.0, first_w["start"] - 0.10)
        raw_cut_end = last_w["end"] + 0.10

        # Snap to silence within silence_snap_max_s
        snap_start, snap_end = _detect_silence_offsets(
            audio_src,
            raw_cut_start,
            raw_cut_end,
            silence_snap_max_s,
            silence_threshold_db,
        )

        cut_start = min(raw_cut_start, snap_start)
        # Invariant: word times lie strictly inside [cut_start, cut_end]
        cut_start = min(cut_start, first_w["start"])

        cut_end = max(raw_cut_end, snap_end)
        if trim_last_word:
            # Trim trailing silence: do not extend beyond last_word.end + snap_max_s
            cut_end = min(cut_end, last_w["end"] + min(0.08, silence_snap_max_s))
        cut_end = max(cut_end, last_w["end"])

        cut_start = round(cut_start, 2)
        cut_end = round(cut_end, 2)

        # Item confidence: average of word confidences
        confs = [w.get("conf", 1.0) for w in aligned_words]
        item_conf = round(sum(confs) / len(confs), 2) if confs else 0.0

        item_record = {
            "span_id": span_id,
            "kind": kind,
            "ayah": ayah_num,
            "take": take_num,
            "cut_start": cut_start,
            "cut_end": cut_end,
            "words": aligned_words,
            "conf": item_conf,
        }
        items.append(item_record)

        # Flag checks:
        # 1. low_align_conf
        if item_conf < min_word_conf:
            flags.append({
                "type": "low_align_conf",
                "take": take_num,
                "time": cut_start,
                "ayah": ayah_num,
                "severity": "warn",
                "detail": f"Overall alignment confidence {item_conf:.2f} is below threshold {min_word_conf}.",
            })

        # 2. low_word_conf_boundary
        if first_w.get("conf", 1.0) < min_word_conf:
            flags.append({
                "type": "low_word_conf_boundary",
                "take": take_num,
                "time": first_w["start"],
                "ayah": ayah_num,
                "severity": "warn",
                "detail": f"Initial word (i={first_w['i']}) has low confidence {first_w.get('conf', 0.0):.2f}.",
            })
        if len(aligned_words) > 1 and last_w.get("conf", 1.0) < min_word_conf:
            flags.append({
                "type": "low_word_conf_boundary",
                "take": take_num,
                "time": last_w["start"],
                "ayah": ayah_num,
                "severity": "warn",
                "detail": f"Terminal word (i={last_w['i']}) has low confidence {last_w.get('conf', 0.0):.2f}.",
            })

        # 3. abnormal_duration
        duration = cut_end - cut_start
        expected_dur = len(target_words) * 1.5
        if duration > (expected_dur * max_duration_factor) or duration < (
            len(target_words) * 0.35
        ):
            flags.append({
                "type": "abnormal_duration",
                "take": take_num,
                "time": cut_start,
                "ayah": ayah_num,
                "severity": "warn",
                "detail": f"Duration {duration:.2f}s is abnormal for {len(target_words)} words (expected ~{expected_dur:.1f}s).",
            })

    flags.sort(key=lambda f: (f["take"] if f["take"] is not None else 0, f["time"]))

    return {
        "surah": surah_num,
        "items": items,
        "flags": flags,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Force align Quran recitation takes.")
    parser.add_argument("--surah", type=int, required=True, help="Surah number")
    parser.add_argument(
        "--config", type=str, default="config.yaml", help="Path to config file"
    )
    args = parser.parse_args()

    project_root = Path.cwd()
    work_dir = project_root / "work" / str(args.surah)
    text_path = work_dir / "text.json"
    spans_path = work_dir / "spans.json"

    if not text_path.exists():
        raise FileNotFoundError(f"Text fixture {text_path} does not exist.")
    if not spans_path.exists():
        raise FileNotFoundError(f"Spans file {spans_path} does not exist.")

    with open(text_path, encoding="utf-8") as f:
        text_data = json.load(f)
    with open(spans_path, encoding="utf-8") as f:
        spans_data = json.load(f)

    cfg: dict[str, Any] = {}
    cfg_path = project_root / args.config
    if cfg_path.exists():
        import yaml

        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    result = run_alignment(text_data, spans_data, cfg, str(project_root))
    out_path = work_dir / "aligned.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()