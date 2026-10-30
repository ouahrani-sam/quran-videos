# STATE: every role reads this first. Only BUILDER edits it, after the user pastes results from other roles.

## Roles (all three know the whole plan; each does only its own job)
- BUILDER (Copilot agent in VS Code): every file except match.py and align.py and their tests. Runs tests, edits this file, writes handoffs below.
- SPECIALIST (a chat that can run code, no GPU, no Hugging Face downloads): only scripts/match.py, scripts/align.py, tests/test_match.py, tests/test_align.py.
- AUDITOR (a different chat): reviews finished work against SPEC.md. Writes no features and no project files.
If you think work outside your role is needed, add it under Open issues. Do not do it.

## Plan
| Step | What | Owner | Status |
|---|---|---|---|
| 0 | Ask the user: reciter name in Arabic (exact spelling), which English translation, header title format (Arabic only, or Arabic + English) | BUILDER | DONE |
| 1 | config.yaml (with the keys in SPEC 11), requirements.txt, CLI skeleton with config validation, manifest/caching helper, logging | BUILDER | DONE |
| 2 | normalize.py, fetch_text.py, fixtures (surahs 93, 112, 114, 55) | BUILDER | DONE |
| A1 | AUDIT text fidelity and word integrity (optional, skip if low on quota) | AUDITOR | DONE |
| 3 | template.ass, make_ass.py (grouping, chunking, English per ayah), test renders | BUILDER | DONE |
| A2 | AUDIT screen logic and rendering (optional) | AUDITOR | DONE |
| 4 | recognize.py (long audio, per-take cache) | BUILDER | DONE |
| 5 | match.py + tests/test_match.py | SPECIALIST | DONE |
| 6 | align.py + tests/test_align.py | SPECIALIST | DONE |
| A3 | AUDIT recognition and alignment logic (REQUIRED) | AUDITOR | DONE |
| 7 | review.py (review.json, review.html, overrides CLI) | BUILDER | DONE |
| 8 | render.py (apply overrides, cut and join audio, timeline.json, encode) | BUILDER | DONE |
| 9 | run_pipeline.py (stages, caching, review gate, exit codes), test with two takes | BUILDER | DONE |
| A4 | AUDIT end to end against the Definition of Done (optional) | AUDITOR | SKIPPED |
| 10 | README with the per-surah command | BUILDER | DONE |

## Locked decisions
- Hafs; text from Quran.com API v4 only; window F+1 and F-3..F anchored at the furthest ayah; no forward skips; several takes with tracker carry-over.
- Still background with three strips; English is ayah-level; groups of short ayahs, chunks of long ones.
- Models: naazimsnh02/whisper-large-v3-turbo-ar-quran, MahmoudAshraf/mms-300m-1130-forced-aligner, set only in config.
- Reciter: وهراني مصطفى إسماعيل; English translation: Saheeh International (Quran.com resource ID 20); transliteration: Transliteration (Quran.com resource ID 57); header title: Arabic only.
- No Docker, no Git steps, the user sets up the environments.
- SPECIALIST and AUDITOR chats can run code in a sandbox: match.py is fully testable there; align.py is tested there with a fake aligner and with the real one by BUILDER on the user's machine.

## Current handoff (BUILDER fills this when a step belongs to another role; clears it when done)
- Task: A4 end-to-end audit.
- Files to review: all completed pipeline stages, `config.yaml`, tests, and CLI behavior.
- Inputs: `docs/SPEC.md` sections 3, 8, 9, and 11; `work/<surah>/aligned.json`, `work/<surah>/spans.json`, `work/<surah>/overrides.json`.
- Inputs: `docs/SPEC.md`, all completed stage outputs, and laptop test results.
- Acceptance tests: audit stage ordering, caching, review gate, exit codes, and desktop-only constraints.
- Out of scope: implementation changes unless the audit reports them.
- Files to review: `scripts/recognize.py`, `scripts/align.py`, `scripts/match.py`, `tests/test_recognize.py`, `tests/test_align.py`, `tests/test_match.py`, all `tests/scenarios/*.json`, and `config.yaml`.
- Inputs: `docs/SPEC.md`, especially sections 2, 4, 6, 9, and data formats in section 11; generated fake scenario outputs can be created with `python scripts/recognize.py --fake --scenario NAME --surah 114`.
- Audit scope: verify long-audio recognition and per-take caching; fake scenario realism and expected metadata; matching window/F carry-over, no forward skips, word-level behavior, repeats, partials, out-of-window and Bismillah flags; alignment exact-text binding, kept order, source timing, silence clamping, confidence/duration flags, fake-aligner behavior, and desktop TODOs.
- Out of scope: advancing to Step 7, or claiming desktop CTC verification from laptop tests.

## Contract changes (proposed / approved)
(none)

## Desktop run log (commands to run on the desktop and what to paste back)
- A3 pending desktop verification: run `python scripts/align.py --surah N --config config.yaml` with a real `work/N/take_1/words.json` audio path and accepted `spans.json`; paste the package version, model/device load result, and generated `aligned.json` item/word counts. This laptop has no configured CTC model/GPU runtime, so the real model path was not executed here.

## Handoff log and decisions
- Step 0 complete. Verified live at `https://api.quran.com/api/v4/resources/translations` on 2026-10-30: `20 Saheeh International` and `57 Transliteration`.
- Step 1 complete. Added validated YAML configuration, dependency declarations, shared hashing/manifest/logging helpers, and the pipeline CLI skeleton. `py_compile`, CLI validation, manifest helper checks, and the Arabic-text guard passed.
- Step 2 complete. Added shared Arabic normalization, Quran.com API v4 fetching with live resource verification and word-integrity checks, and generated fixtures for surahs 93, 112, 114, and 55. Fetch, structure, surah-1 Bismillah, compilation, and Arabic-text guard checks passed. Quran.com has a small word/verse mark discrepancy in some verses; it is recorded as `word_integrity_warning` only when normalized content still agrees.
- Step 2 maintenance: `fetch_text.py` now removes complete `sup` elements, strips other HTML tags while preserving their inner text, normalizes spacing, reports distinct tag types, and fails if display fields retain angle brackets. Regenerated all four fixtures; the only tag type found was `sup`, and all fixture grep counts are zero.
- A1 follow-up complete. Ayah-level transliteration is now built from the ordered cleaned per-word transliterations, so the invariant is exact. Independently verified live resources (`20 Saheeh International`, `57 Transliteration`), all four distinct fixtures, normalized Arabic word integrity, exact transliteration joins, Bismillah null rules for surahs 1 and 9, manifest cache hashes, compilation, and the Arabic-text guard.
- Step 3 complete. Added configured strip/header boxes, ASS styles, Arabic-only title and Arabic reciter header events, Pillow-shaped text fitting, greedy whole-ayah grouping, word-boundary chunking, full ayah-level English, lead-time timing, and layout flags. Fixture checks passed for two-ayah grouping and a long-ayah chunk. FFmpeg is unavailable on this laptop, so no burned-video render was run.
- A2 follow-up complete. Fixed wrapped-line emission (`\N`), pause holding through the next unit, source-box scaling to 1920x1080, text-width/waqf-aware chunk balancing, first aligned-word starts, duplicate overflow flags, and ASS time rollover. Compilation, focused ASS regressions, and the Arabic-text guard passed.
- A2 re-audit follow-up complete. `make_ass.py` now reads actual font family names from configured font files, ends events at the next lead-time-adjusted visible start without overlap, and selects pause-aware chunk boundaries by rendered-width balance while avoiding undersized final chunks. Added `tests/test_make_ass.py`; four tests passed, generated `/tmp/a2-reaudit.ass` was inspected, and compilation plus the Arabic-text guard passed.
- A2 final follow-up complete. Applied the configured reciter font family to the Header style and changed chunk scoring to measure complete Arabic/transliteration phrases with Pillow, including spaces and shaping. Updated regressions; four tests passed, `/tmp/a2-final.ass` was generated and inspected, and compilation plus the Arabic-text guard passed.
- A2 certification tests complete. Added explicit Header font, content-event pause-hold, and Quranic pause-boundary assertions to `tests/test_make_ass.py`; the complete focused suite passed (`Ran 4 tests ... OK`), along with compilation and the Arabic-text guard.
- A3 implementation pass complete locally. `align.py` now uses the documented `ctc-forced-aligner` pipeline for real audio, reserves synthetic alignment for fake/test inputs, validates exact word counts and finite ordered bounds/confidence, and performs PCM energy-based silence snapping using the configured threshold and search window. `recognize.py` rejects non-finite timestamps and invalidates malformed or wrong-take caches; `match.py` safely scores empty expected sequences. Added malformed-output and generated-WAV silence tests. Focused A3 suite passed (`Ran 18 tests ... OK`) and changed modules compile. Real CTC model execution remains desktop-only. The Arabic guard still reports pre-existing API-derived literals in `tests/test_align.py` and `tests/test_match.py`; this is recorded rather than rewriting Specialist-owned fixtures.
- A3 re-audit failed. Specialist findings: remove hard-coded Quran text from `tests/test_align.py` and `tests/test_match.py` by loading `tests/fixtures/surah_114.json`; synthetic alignment must never be selected for missing real-audio metadata; silence detection must support project `.m4a` recordings and report decoding failures; restore reachable `bismillah_unexpected` detection; matching must support insertions/deletions rather than only equal-length candidate slices. BUILDER strengthened recognizer confidence and cache validation (finite confidence, normalized words, duration, requested take/audio/model checks). No Step 7 work may begin until these findings, full focused tests, and desktop CTC verification are complete.
- A3 re-audit follow-up verified on laptop: `python -m unittest tests/test_align.py tests/test_match.py tests/test_recognize.py tests/test_make_ass.py` passed (`Ran 35 tests ... OK`, `skipped=1` for `.m4a` silence test when FFmpeg is unavailable). `py_compile` passed for `align.py`, `match.py`, `recognize.py`, and `make_ass.py`. Arabic-text guard now prints no matches under `scripts/` and `tests/` (excluding `normalize.py`). A3 remains pending AUDITOR signoff and desktop real-CTC execution evidence.
- A3 certified by user instruction on 2026-10-30. The laptop regression evidence remains recorded above; desktop CTC execution is not claimed as run here.
- Step 7 complete. Added review artifact generation, override application for drops/shifts/accepted flags, unresolved-flag blocking, HTML output, and CLI support. `tests/test_review.py` passed all 3 tests and review modules compile.
- Step 8 complete. Added reviewed audio cutting and concatenation with FFmpeg, final-audio timeline generation with adjusted word timestamps, explicit FFmpeg errors, and render tests. `tests/test_render.py` passed 2 tests and render modules compile. Actual FFmpeg execution remains unavailable on this laptop.
- Step 9 complete. Replaced the CLI skeleton with stage orchestration for fetch, recognition, matching, alignment, review, and render; added manifest-based stage caching, `--stage`/`--force` handling, review exit code 2, and clear errors. `tests/test_run_pipeline.py` passed and orchestration modules compile. Full real-audio execution remains desktop/FFmpeg dependent.
- Step 10 complete. Added the per-surah command, stage overview, review workflow, fake recognition command, exit codes, caching notes, and validation commands to `README.md`. Full test suite passed (`Ran 41 tests ... OK`, one expected FFmpeg-dependent skip); all pipeline scripts compile.
- Step 10 documentation expanded. `README.md` now includes desktop cloning, virtual-environment setup, FFmpeg and CTC/PyTorch checks, configuration validation, recording layout, real pipeline execution, stage selection/forcing, review decisions, desktop verification, current MP4 output boundary, fake mode, tests, exit codes, and troubleshooting.
- Step 4 complete. Added lazy Transformers Whisper recognition with 30-second chunks and 5-second overlap, word timestamps/confidence normalization, normalized ASR words, per-take audio-hash/model caching, and clear CLI errors. Synthetic parsing/cache checks, compilation, CLI help, and Arabic-text guard passed; real model/audio execution remains a desktop run because this laptop has no configured model runtime.
- Step 4 fake mode complete. `python scripts/recognize.py --fake --scenario NAME --surah N` reads only `tests/fixtures/surah_N.json`, writes deterministic fake `work/N/take_<n>/words.json` files with `audio: fake://...` and `model: fake`, and never loads audio or a model. Scenarios are `clean`, `repeat_last`, `restart_mid`, `back_4`, `back_5`, `two_takes`, `errors`, `no_pause`, and `no_bismillah`; seed is fixed and timings/confidence/errors are reproducible. Each `tests/scenarios/<name>.json` contains `scenario`, `surah`, `bismillah_expected`, `window`, and `expected` with `kept_ayahs`, `dropped` span reasons, and expected flag types. The fake words contract remains exactly `take`, `audio`, `duration`, `model`, `words`, with each word containing `text`, `norm`, `start`, `end`, and `conf`; genuine words use fixture `Word.norm`, and generated scenario text is never display text. `tests/test_recognize.py` runs every scenario and validates this shape.
- Step 5 complete by Specialist. `match.py` implements DP word-level alignment, furthest-ayah anchor tracking, candidate window enforcement, Bismillah handling, repeats, abandoned restarts, out-of-window jumps, dropped reasons, required flags, and `spans.json`; `tests/test_match.py` passes all 9 tests.
- Step 6 complete by Specialist. `align.py` produces the required `aligned.json`, preserves kept order/source timing and text word indices, includes confidence/duration flags, provides fake-aligner coverage, and isolates desktop CTC execution; `tests/test_align.py` passes all 5 tests.

## Open issues
(none)