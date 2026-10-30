"""Word-level Quran recitation matching implementation owned by SPECIALIST."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.normalize import normalize_arabic

try:
    from rapidfuzz.distance import Levenshtein

    def _word_similarity(w1: str, w2: str) -> float:
        if w1 == w2:
            return 1.0
        return float(Levenshtein.normalized_similarity(w1, w2))
except ImportError:
    import difflib

    def _word_similarity(w1: str, w2: str) -> float:
        if w1 == w2:
            return 1.0
        return difflib.SequenceMatcher(None, w1, w2).ratio()


def _align_dp(exp_words: list[str], rec_words: list[str]) -> tuple[float, int]:
    """Align expected words against a prefix of recognized words with insertion/deletion support.

    Returns (score, consumed_length).
    """
    M = len(exp_words)
    N = len(rec_words)
    if M == 0 or N == 0:
        return 0.0, 0

    gap_penalty = -0.25
    dp = [[-1e9] * (N + 1) for _ in range(M + 1)]
    dp[0][0] = 0.0

    for i in range(1, M + 1):
        dp[i][0] = dp[i - 1][0] + gap_penalty

    for i in range(1, M + 1):
        ew = exp_words[i - 1]
        for j in range(1, N + 1):
            rw = rec_words[j - 1]
            sim = _word_similarity(ew, rw)
            match_score = (sim - 0.4) / 0.6 if sim >= 0.4 else -0.6

            s_match = dp[i - 1][j - 1] + match_score
            s_del = dp[i - 1][j] + gap_penalty
            s_ins = dp[i][j - 1] + gap_penalty
            dp[i][j] = max(s_match, s_del, s_ins)

    delta = max(1, min(3, M // 3))
    min_j = max(1, M - delta)
    max_j = min(N, M + delta)

    best_j = 0
    best_raw = -1e9
    for j in range(min_j, max_j + 1):
        if dp[M][j] > best_raw:
            best_raw = dp[M][j]
            best_j = j

    if best_j == 0:
        return 0.0, 0

    # Traceback to sum matching word similarities
    curr_i, curr_j = M, best_j
    sim_sum = 0.0
    while curr_i > 0 or curr_j > 0:
        if curr_i > 0 and curr_j > 0:
            ew = exp_words[curr_i - 1]
            rw = rec_words[curr_j - 1]
            sim = _word_similarity(ew, rw)
            match_score = (sim - 0.4) / 0.6 if sim >= 0.4 else -0.6
            if abs(dp[curr_i][curr_j] - (dp[curr_i - 1][curr_j - 1] + match_score)) < 1e-6:
                if sim >= 0.4:
                    sim_sum += sim
                curr_i -= 1
                curr_j -= 1
                continue
        if curr_i > 0 and abs(dp[curr_i][curr_j] - (dp[curr_i - 1][curr_j] + gap_penalty)) < 1e-6:
            curr_i -= 1
            continue
        if curr_j > 0 and abs(dp[curr_i][curr_j] - (dp[curr_i][curr_j - 1] + gap_penalty)) < 1e-6:
            curr_j -= 1
            continue
        break

    score = sim_sum / max(M, best_j)
    return score, best_j


def _find_prefix_match(rec_words: list[str], exp_words: list[str]) -> tuple[float, int]:
    """Check if rec_words starts with a prefix of exp_words (abandoned restart)."""
    max_k = min(len(rec_words), len(exp_words) - 1)
    best_score = 0.0
    best_len = 0
    for k in range(1, max_k + 1):
        score = sum(_word_similarity(rec_words[i], exp_words[i]) for i in range(k)) / k
        if score >= 0.70 and score > best_score:
            best_score = score
            best_len = k
    return best_score, best_len


def run_matching(
    text: dict[str, Any], takes: list[dict[str, Any]], cfg: dict[str, Any]
) -> dict[str, Any]:
    """Execute word-level matching of recitation takes against Quranic text."""
    surah_num = text.get("surah")
    matching_cfg = cfg.get("matching", {})
    repeat_window = matching_cfg.get("repeat_window", 4)
    repeat_mode = matching_cfg.get("repeat_mode", "last")
    min_match_score = matching_cfg.get("min_match_score", 0.75)
    low_conf_score = matching_cfg.get("low_conf_score", 0.85)

    bismillah_cfg = cfg.get("bismillah", True)
    bismillah_expected = bool(
        bismillah_cfg and text.get("bismillah") is not None and surah_num not in (1, 9)
    )

    bismillah_exp_words: list[str] = []
    if text.get("bismillah"):
        bismillah_exp_words = [
            w.get("norm", normalize_arabic(w["arabic"]))
            for w in text["bismillah"].get("words", [])
        ]

    ayah_dict: dict[int, list[str]] = {}
    for ayah_entry in text.get("ayahs", []):
        n = ayah_entry["n"]
        words = [
            w.get("norm", normalize_arabic(w["arabic"]))
            for w in ayah_entry.get("words", [])
        ]
        ayah_dict[n] = words

    all_ayah_nums = sorted(ayah_dict.keys())

    spans: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []

    f_furthest = 0
    bismillah_done = False
    next_span_id = 0

    for take_idx, take in enumerate(takes):
        take_num = take.get("take", take_idx + 1)
        rec_words_data = take.get("words", [])
        if not rec_words_data:
            continue

        norm_rec_words = [
            w.get("norm", normalize_arabic(w.get("text", "")))
            for w in rec_words_data
        ]
        n_words = len(norm_rec_words)
        idx = 0

        while idx < n_words:
            # Valid candidates in window: F+1 and F-(repeat_window-1) .. F
            candidates: list[tuple[str, int | None, list[str]]] = []
            if bismillah_exp_words and not bismillah_done and f_furthest == 0:
                candidates.append(("bismillah", None, bismillah_exp_words))

            if f_furthest == 0:
                if 1 in ayah_dict:
                    candidates.append(("ayah", 1, ayah_dict[1]))
            else:
                next_a = f_furthest + 1
                if next_a in ayah_dict:
                    candidates.append(("ayah", next_a, ayah_dict[next_a]))
                min_rep = max(1, f_furthest - (repeat_window - 1))
                for a in range(f_furthest, min_rep - 1, -1):
                    if a in ayah_dict and not any(
                        c[0] == "ayah" and c[1] == a for c in candidates
                    ):
                        candidates.append(("ayah", a, ayah_dict[a]))

            # Check for abandoned prefix followed immediately by restart
            restart_detected = False
            for kind, a_num, exp_w in candidates:
                max_k = min(len(norm_rec_words) - idx, len(exp_w) - 1)
                for k in range(1, max_k + 1):
                    pref_score = sum(
                        _word_similarity(norm_rec_words[idx + p], exp_w[p]) for p in range(k)
                    ) / k
                    if pref_score >= 0.70:
                        next_idx = idx + k
                        if next_idx < n_words:
                            # Verify if next_idx actually restarts this candidate or another candidate
                            restarts_candidate = any(
                                _word_similarity(norm_rec_words[next_idx], cand_exp[0]) >= 0.80
                                for _, _, cand_exp in candidates
                            )
                            if restarts_candidate:
                                w_first = rec_words_data[idx]
                                w_last = rec_words_data[idx + k - 1]
                                dropped.append({
                                    "take": take_num,
                                    "start": round(w_first["start"], 2),
                                    "end": round(w_last["end"], 2),
                                    "reason": "abandoned_partial",
                                    "ayah": a_num,
                                })
                                flags.append({
                                    "type": "partial_ayah",
                                    "take": take_num,
                                    "time": round(w_first["start"], 2),
                                    "ayah": a_num,
                                    "severity": "warn",
                                    "detail": f"Partial abandoned recitation detected for Ayah {a_num}.",
                                })
                                idx += k
                                restart_detected = True
                                break
                if restart_detected:
                    break

            if restart_detected:
                continue

            # 1. DP sequence alignment across candidate units (handles insertions/deletions)
            best_cand = None
            best_cand_score = 0.0
            best_cand_len = 0

            for kind, a_num, exp_w in candidates:
                score, consumed_len = _align_dp(exp_w, norm_rec_words[idx:])
                if score > best_cand_score:
                    best_cand_score = score
                    best_cand = (kind, a_num, exp_w)
                    best_cand_len = consumed_len

            # 2. If a candidate matched with score >= min_match_score
            if (
                best_cand is not None
                and best_cand_score >= min_match_score
                and best_cand_len > 0
            ):
                kind, a_num, exp_w = best_cand
                consumed_len = best_cand_len
                w_first = rec_words_data[idx]
                w_last = rec_words_data[idx + consumed_len - 1]

                span_id = next_span_id
                next_span_id += 1

                span_record = {
                    "id": span_id,
                    "take": take_num,
                    "kind": kind,
                    "ayah": a_num,
                    "start": round(w_first["start"], 2),
                    "end": round(w_last["end"], 2),
                    "word_range": [idx, idx + consumed_len - 1],
                    "complete": True,
                    "conf": round(best_cand_score, 2),
                    "superseded": False,
                }
                spans.append(span_record)

                if best_cand_score < low_conf_score:
                    flags.append({
                        "type": "low_match_conf",
                        "take": take_num,
                        "time": round(w_first["start"], 2),
                        "ayah": a_num,
                        "severity": "warn",
                        "detail": f"Match score {best_cand_score:.2f} is below low_conf_score {low_conf_score}.",
                    })

                if kind == "bismillah":
                    bismillah_done = True
                elif kind == "ayah" and a_num is not None:
                    if a_num > f_furthest:
                        f_furthest = a_num

                idx += consumed_len
                continue

            # 3. Check for out-of-window backwards repeat (strictly beyond window)
            out_of_window_match = None
            out_of_window_score = 0.0
            out_of_window_len = 0
            if f_furthest > repeat_window:
                min_rep = f_furthest - (repeat_window - 1)
                for a in range(1, min_rep):
                    exp_w = ayah_dict[a]
                    score, consumed_len = _align_dp(exp_w, norm_rec_words[idx:])
                    if score >= min_match_score and score > out_of_window_score:
                        out_of_window_score = score
                        out_of_window_match = a
                        out_of_window_len = consumed_len

            if out_of_window_match is not None and out_of_window_len > 0:
                w_start = rec_words_data[idx]
                end_pos = idx + out_of_window_len - 1
                w_end = rec_words_data[end_pos]
                dropped.append({
                    "take": take_num,
                    "start": round(w_start["start"], 2),
                    "end": round(w_end["end"], 2),
                    "reason": "unmatched",
                    "ayah": out_of_window_match,
                })
                flags.append({
                    "type": "out_of_window",
                    "take": take_num,
                    "time": round(w_start["start"], 2),
                    "ayah": out_of_window_match,
                    "severity": "warn",
                    "detail": f"Match for Ayah {out_of_window_match} was outside the allowed repeat window from furthest {f_furthest}.",
                })
                idx += out_of_window_len
                continue

            # 4. Check for standalone abandoned partial of any current candidate (e.g. take ending mid-ayah)
            prefix_found = False
            for kind, a_num, exp_w in candidates:
                pref_score, pref_len = _find_prefix_match(norm_rec_words[idx:], exp_w)
                if pref_score >= 0.70 and pref_len >= 1:
                    w_first = rec_words_data[idx]
                    w_last = rec_words_data[idx + pref_len - 1]
                    dropped.append({
                        "take": take_num,
                        "start": round(w_first["start"], 2),
                        "end": round(w_last["end"], 2),
                        "reason": "abandoned_partial",
                        "ayah": a_num,
                    })
                    flags.append({
                        "type": "partial_ayah",
                        "take": take_num,
                        "time": round(w_first["start"], 2),
                        "ayah": a_num,
                        "severity": "warn",
                        "detail": f"Partial abandoned recitation detected for Ayah {a_num}.",
                    })
                    idx += pref_len
                    prefix_found = True
                    break

            if prefix_found:
                continue

            # 5. Unmatched word
            w_cur = rec_words_data[idx]
            dropped.append({
                "take": take_num,
                "start": round(w_cur["start"], 2),
                "end": round(w_cur["end"], 2),
                "reason": "unmatched",
                "ayah": None,
            })
            flags.append({
                "type": "unmatched_segment",
                "take": take_num,
                "time": round(w_cur["start"], 2),
                "ayah": None,
                "severity": "warn",
                "detail": f"Unmatched word '{w_cur.get('text', '')}' at take {take_num} timestamp {w_cur['start']:.2f}s.",
            })
            idx += 1

    # Resolve repeats / superseding
    unit_spans: dict[tuple[str, int | None], list[dict[str, Any]]] = {}
    for sp in spans:
        if sp["complete"]:
            k = (sp["kind"], sp["ayah"])
            unit_spans.setdefault(k, []).append(sp)

    kept_ids: list[int] = []

    # Bismillah resolution
    if bismillah_expected:
        b_spans = unit_spans.get(("bismillah", None), [])
        if not b_spans:
            flags.append({
                "type": "bismillah_missing",
                "take": None,
                "time": 0.0,
                "ayah": None,
                "severity": "error",
                "detail": "Bismillah was expected for this surah but was not found in recitation.",
            })
        else:
            chosen = (
                b_spans[-1]
                if repeat_mode == "last"
                else max(b_spans, key=lambda s: s["conf"])
            )
            for sp in b_spans:
                if sp["id"] != chosen["id"]:
                    sp["superseded"] = True
                    dropped.append({
                        "take": sp["take"],
                        "start": sp["start"],
                        "end": sp["end"],
                        "reason": "superseded_repeat",
                        "ayah": None,
                    })
            kept_ids.append(chosen["id"])
    else:
        # Bismillah not expected: any recited Bismillah span is dropped and flagged
        b_spans = unit_spans.get(("bismillah", None), [])
        if b_spans:
            for sp in b_spans:
                sp["superseded"] = True
                dropped.append({
                    "take": sp["take"],
                    "start": sp["start"],
                    "end": sp["end"],
                    "reason": "unmatched",
                    "ayah": None,
                })
                flags.append({
                    "type": "bismillah_unexpected",
                    "take": sp["take"],
                    "time": sp["start"],
                    "ayah": None,
                    "severity": "warn",
                    "detail": "Bismillah was recited but not expected for this surah.",
                })

    # Ayahs resolution
    for a in all_ayah_nums:
        a_spans = unit_spans.get(("ayah", a), [])
        if not a_spans:
            flags.append({
                "type": "missing_ayah",
                "take": None,
                "time": 0.0,
                "ayah": a,
                "severity": "error",
                "detail": f"Ayah {a} was not covered by any valid recitation take.",
            })
        else:
            chosen = (
                a_spans[-1]
                if repeat_mode == "last"
                else max(a_spans, key=lambda s: s["conf"])
            )
            for sp in a_spans:
                if sp["id"] != chosen["id"]:
                    sp["superseded"] = True
                    dropped.append({
                        "take": sp["take"],
                        "start": sp["start"],
                        "end": sp["end"],
                        "reason": "superseded_repeat",
                        "ayah": a,
                    })
            kept_ids.append(chosen["id"])

    dropped.sort(key=lambda d: (d["take"], d["start"]))
    flags.sort(key=lambda f: (f["take"] if f["take"] is not None else 0, f["time"]))

    return {
        "surah": surah_num,
        "bismillah_expected": bismillah_expected,
        "window": repeat_window,
        "spans": spans,
        "kept_ids": kept_ids,
        "dropped": dropped,
        "flags": flags,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Match Quranic recitation takes against expected text."
    )
    parser.add_argument("--surah", type=int, required=True, help="Surah number")
    parser.add_argument(
        "--config", type=str, default="config.yaml", help="Path to configuration file"
    )
    args = parser.parse_args()

    work_dir = Path("work") / str(args.surah)
    text_path = work_dir / "text.json"
    if not text_path.exists():
        raise FileNotFoundError(f"Text fixture {text_path} does not exist.")

    with open(text_path, encoding="utf-8") as f:
        text_data = json.load(f)

    cfg: dict[str, Any] = {}
    cfg_path = Path(args.config)
    if cfg_path.exists():
        import yaml

        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}

    takes: list[dict[str, Any]] = []
    take_dirs = sorted(
        work_dir.glob("take_*"),
        key=lambda p: int(p.name.split("_")[-1])
        if p.name.split("_")[-1].isdigit()
        else 0,
    )
    for td in take_dirs:
        w_file = td / "words.json"
        if w_file.exists():
            with open(w_file, encoding="utf-8") as f:
                takes.append(json.load(f))

    result = run_matching(text_data, takes, cfg)
    out_path = work_dir / "spans.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()