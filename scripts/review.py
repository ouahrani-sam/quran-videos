"""Create review artifacts and persist review decisions for one surah."""

from __future__ import annotations

import argparse
import copy
import html
import json
from pathlib import Path
from typing import Any


def _load(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return copy.deepcopy(default)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _accepted(flag: dict[str, Any], accepted: list[dict[str, Any]]) -> bool:
    return any(
        entry.get("type") == flag.get("type")
        and (entry.get("ayah") is None or entry.get("ayah") == flag.get("ayah"))
        for entry in accepted
    )


def _apply_overrides(aligned: dict[str, Any], spans: dict[str, Any],
                     overrides: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(aligned)
    dropped = {int(item) for item in overrides.get("drop_spans", [])}
    shifts = {
        int(item["span_id"]): item
        for item in overrides.get("shift", [])
        if isinstance(item, dict) and "span_id" in item
    }
    kept = []
    for item in result.get("items", []):
        span_id = int(item["span_id"])
        if span_id in dropped:
            continue
        item = copy.deepcopy(item)
        shift = shifts.get(span_id)
        if shift:
            start_delta = float(shift.get("cut_start_delta", 0.0))
            end_delta = float(shift.get("cut_end_delta", 0.0))
            item["cut_start"] = max(0.0, item["cut_start"] + start_delta)
            item["cut_end"] = max(item["cut_start"], item["cut_end"] + end_delta)
            for word in item.get("words", []):
                word["start"] = max(item["cut_start"], word["start"] + start_delta)
                word["end"] = min(item["cut_end"], word["end"] + end_delta)
                word["start"] = min(word["start"], word["end"])
        kept.append(item)
    result["items"] = kept

    for span_id in dropped:
        span = next((s for s in spans.get("spans", []) if s.get("id") == span_id), None)
        if span and span.get("kind") == "ayah":
            result.setdefault("flags", []).append({
                "type": "missing_ayah", "take": span.get("take"),
                "time": span.get("start"), "ayah": span.get("ayah"),
                "severity": "warn", "detail": "Span dropped by review override.",
            })
    return result


def build_review(aligned: dict[str, Any], spans: dict[str, Any],
                 overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    applied = _apply_overrides(aligned, spans, overrides)
    accepted = overrides.get("accept_flags", [])
    flags = []
    for flag in [*spans.get("flags", []), *applied.get("flags", [])]:
        if isinstance(flag, dict) and not _accepted(flag, accepted):
            flags.append(copy.deepcopy(flag))
    return {
        "surah": aligned.get("surah", spans.get("surah")),
        "items": applied.get("items", []),
        "flags": flags,
        "accepted_flags": copy.deepcopy(accepted),
        "blocked": any(flag.get("severity") in {"warn", "error"} for flag in flags),
    }


def write_review(work_dir: Path) -> dict[str, Any]:
    aligned = _load(work_dir / "aligned.json", {})
    spans = _load(work_dir / "spans.json", {})
    overrides = _load(work_dir / "overrides.json", {})
    result = build_review(aligned, spans, overrides)
    (work_dir / "review.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rows = []
    for flag in result["flags"]:
        rows.append(
            "<li><b>{}</b> ayah={} time={} severity={}: {}</li>".format(
                html.escape(str(flag.get("type"))),
                html.escape(str(flag.get("ayah"))),
                html.escape(str(flag.get("time"))),
                html.escape(str(flag.get("severity"))),
                html.escape(str(flag.get("detail", ""))),
            )
        )
    body = "\n".join(rows) or "<li>No unresolved flags.</li>"
    (work_dir / "review.html").write_text(
        "<!doctype html><meta charset=\"utf-8\"><title>Review</title>"
        "<h1>Review</h1><ul>" + body + "</ul>\n",
        encoding="utf-8",
    )
    return result


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create or update a surah review.")
    parser.add_argument("--surah", type=int, required=True)
    parser.add_argument("--work-dir", default="work")
    parser.add_argument("--drop", type=int, action="append", default=[])
    parser.add_argument("--shift", nargs=3, metavar=("SPAN_ID", "START_DELTA", "END_DELTA"),
                        action="append", default=[])
    parser.add_argument("--accept", nargs=2, metavar=("TYPE", "AYAH"), action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    work_dir = Path(args.work_dir) / str(args.surah)
    overrides = _load(work_dir / "overrides.json", {
        "drop_spans": [], "shift": [], "accept_flags": [],
    })
    overrides["drop_spans"] = sorted(set(overrides.get("drop_spans", [])) | set(args.drop))
    overrides.setdefault("shift", []).extend(
        {"span_id": int(span), "cut_start_delta": float(start), "cut_end_delta": float(end)}
        for span, start, end in args.shift
    )
    overrides.setdefault("accept_flags", []).extend(
        {"type": flag_type, "ayah": None if ayah.lower() == "none" else int(ayah)}
        for flag_type, ayah in args.accept
    )
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "overrides.json").write_text(
        json.dumps(overrides, indent=2) + "\n", encoding="utf-8"
    )
    result = write_review(work_dir)
    print(f"review written: {work_dir / 'review.json'}")
    return 2 if result["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())