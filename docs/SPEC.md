# SPEC: single source of truth. Changes only with the user's approval, logged in STATE.md.

## 1. PROJECT
The user records their own Quran recitations (Hafs 'an 'Asim). The tool turns the recordings of ONE surah into ONE finished MP4 for a viewer who does not understand Arabic. The video shows Arabic (Uthmani, full marks), English transliteration and English translation, changing in sync with the recitation, over a still background image with three framed strips, plus surah title and reciter name. The template is styled once and reused for about 30 surahs. Success: one command per surah, the user only looks at flagged spots.

## 2. HOW THE USER RECITES (defines the hard part)
- Continuous recitation, NO forced pause between ayahs.
- Within a take and across takes the user may stop, restart an ayah, or repeat ayahs. A new take may begin by repeating up to 4 ayahs back from where the previous take ended.
- The user NEVER skips ahead. The only backward movement is repetition, never more than 4 ayahs back from the furthest ayah reached.

## 3. CLI
    python scripts/run_pipeline.py --surah N --audio t1.m4a [t2.m4a ...] [--start-ayah 1] [--stage STAGE] [--force STAGE] [--config PATH]
- Audio files are given IN ORDER, one file per take. One run = one surah = one video. Later takes need no start ayah (the tracker carries over).
- Stages in order: fetch, recognize, match, align, review, render. --stage runs only that stage (earlier outputs must exist). --force redoes that stage and everything after it.
- Caching: every stage skips itself when its output exists and its inputs and the config sections it uses are unchanged (hashes in work/<surah>/manifest.json). Recognition is cached per take and per audio file hash.
- Review gate: after the review stage, if review.json contains any flag with severity warn or error that is not accepted in overrides.json, the run stops with exit code 2 and prints the path to review.html. Running again with --stage render renders regardless.
- Console: one line per stage (name, seconds, short result), final summary (ayahs kept, dropped, flagged, output path). Full detail goes to logs/<surah>.log. Errors say what is wrong and how to fix it, never a bare traceback.
- Exit codes: 0 done, 1 error, 2 stopped for review.
- Config is validated at startup (all keys present, files exist, values sane) with clear messages.
- Output: output/<surah 3 digits>_<slug>.mp4.
- scripts/review.py also works as its own command to record decisions:
    python scripts/review.py --surah N --drop SPAN_ID | --shift SPAN_ID START_DELTA END_DELTA | --accept TYPE AYAH
  Each writes work/<surah>/overrides.json (see section 8). A rerun applies it.

## 4. RECOGNITION LOGIC (required; do not substitute something simpler)
1. Keep F = the FURTHEST ayah reached so far (not the current position). Anchor the window to F so repeated going-back can never drag it backward. F persists across takes.
2. Candidates at any moment: ayah F+1 and ayahs F-3 through F. Nothing else. No forward-skip candidates. At the start of a surah the Bismillah segment is an extra candidate (section 6).
3. Do NOT treat one Whisper segment as one ayah: with no forced pauses a segment can span two ayahs or end mid-ayah. Match at WORD level: align the recognized word sequence against the expected word sequence of the candidate ayahs, so ayah boundaries come from the known text.
4. Handle: normal advance; full repeat (keep the last complete take by default; config option to keep the highest-confidence one); partial ayah or abandoned restart (drop it from the audio); anything matching nothing inside the window (do NOT guess: flag it). A repeat in a later take replaces that ayah from an earlier take under the same rule.
5. Do NOT use fuzz.partial_ratio as the decision-maker: it scores a short fragment inside a long ayah as a near-perfect match, dangerous with shared phrases and rhyming endings. Normalized Arabic plus rapidfuzz may be a cheap pre-filter only.
6. After spans are decided, run a CTC forced aligner on each accepted span with the EXACT known text, so word counts never mismatch. Snap ayah start/end to real silence and trim trailing silence on the last word of each ayah.
7. Final audio = accepted spans cut from the takes (repeats and partials removed), joined in ayah order. Timings are generated against that edited audio. Record which take and time range every span came from.
8. Quality gate: review.json flags low match confidence, out-of-window matches, unmatched segments, partial ayahs, missing ayahs (no take covered it), Bismillah problems, low alignment confidence, abnormal ayah durations, text overflow, English overflow, and unit boundaries with low word confidence. Every flag names its take and timestamp. review.html plays ONLY the flagged spots.

## 5. QURAN TEXT RULES (absolute)
- All Arabic, transliteration and translation come from the Quran.com API v4 (api.quran.com). NEVER type or recall Quran text anywhere, including tests and examples. Test data is text fetched by fetch_text.py and cached in tests/fixtures/ (fetch surahs 93, 112, 114 and 55).
- ASR output is evidence for timing and matching only and is never displayed.
- Display the Arabic exactly as the API returns it. Normalized text is for matching and alignment only.
- Resource IDs (Uthmani text, transliteration, English translation) are verified against the live API (translations list endpoint) before use, never trusted from memory. Default English: Sahih International; confirm its ID.
- Riwaya: Hafs for both recitation and text. config riwaya: "hafs". No other riwayat.

## 6. BISMILLAH
- The user recites it at the start of each surah except Surah 9. Config bismillah: true/false (default true); Surah 9 never has one.
- Surah 1: it is verse 1:1, a normal ayah. Every other surah: an opening segment before ayah 1, text fetched from the API (verse 1:1), shown on its own screen (never grouped with ayah 1), handled outside the ayah window logic. The tracker starts at ayah 1 after it.
- Flag it if expected but not recited, or recited but not expected.

## 7. SCREEN LOGIC
- The screen shows one SCREEN UNIT at a time: (a) a GROUP of 1 or more consecutive WHOLE ayahs, or (b) a CHUNK: one piece of a single ayah too long for its strip. A unit never mixes a partial ayah with other ayahs.
- Grouping: in ayah order, greedily add the next ayah to the current group while ALL THREE layers still fit their strips at or above a "group min font size" (readability floor above the absolute minimum) and the group has at most max_ayahs_per_screen ayahs (default 2). Otherwise start a new group. Never group across the Bismillah.
- Splitting: if a single ayah does not fit its strip at the minimum font size (Arabic or transliteration), split it into chunks shown one after another, only at word boundaries, at the SAME words in Arabic and transliteration. Prefer the Quran's pause signs (waqf marks) when present; otherwise balance lengths (no tiny last chunk). Fewest chunks that fit.
- Switching: a unit appears when the user starts the first word of its first ayah or chunk, minus a lead time (default 0.1 s), and stays until the next unit appears. Pauses do not blank the screen.
- English is always ayah-level. For a group, show each ayah's full API translation in order, clearly separated (for example by the ayah number in brackets). For a chunk, show the FULL English of its ayah, unchanged across its chunks. Never split, cut or paraphrase English, never attach English text to chunks. If the English of a unit does not fit at the minimum size: do not shrink further or overflow; split a group into smaller groups (down to single ayahs); if one ayah's English still does not fit, flag it.
- If the API's word data has the end-of-ayah marker (verify), show it after each ayah's last Arabic word inside a group.
- Fit: per layer, the largest font size (up to a configured max) at which the text fits, down to the configured minimum, up to the configured max lines. Fit is measured with real shaped-text metrics, not character counts.

## 8. RENDERING
- .ass subtitles (libass) burned with FFmpeg over template/background.png (scale to 1920x1080), H.264 + AAC, CPU libx264.
- Three framed strips, top to bottom: 1 Arabic, 2 transliteration, 3 English; each layer centered in its strip. Rough positions in the 1672x941 original: strip 1 x 190-1480 y 380-500; strip 2 x 40-1630 y 520-622; strip 3 x 60-1610 y 636-745 (scale by 1920/1672). These are estimates: measure the real inner edges, store them in config with safe padding.
- Reciter name: shown in ARABIC script, top-left of the sky area, to the right of the left lantern (roughly x 265-735, y 35-125 at 1920x1080; measure and store the box in config), in a decorative Arabic font set in config (reciter_font, default Aref Ruqaa; the user places the font file in template/fonts/). The name text comes from config (reciter_name_ar), typed by the user, never by the tool. Soft shadow or outline so it reads over the clouds. Keep it clear of the lantern, the arch and the minarets.
- Surah title: small, top-center of the sky area, clear of the moon and the reciter name. Propose the exact spot in the first test render. The title text comes from the API data (title_ar, title_en).
- Test early with one very long ayah (shaping, diacritics, wrapping, chunking, English fit) and two short consecutive ayahs (grouping). If libass fails, propose an HTML/headless-browser fallback and explain why before switching.

## 9. MODELS
- Set in config, pulled from Hugging Face on first run; switching changes only the config name. Default recognizer: naazimsnh02/whisper-large-v3-turbo-ar-quran. Default aligner: MahmoudAshraf/mms-300m-1130-forced-aligner via the ctc-forced-aligner package (language "ara"; align on normalized text, display the original Uthmani).
- Check each repo exists and report its exact name and license. No benchmarks or comparisons. Recognition and alignment run locally; the Quran.com API is for text only. recognize.py must handle recordings longer than the model's 30-second window.

## 10. ENVIRONMENT AND CONSTRAINTS
- The user sets up Python, venv, FFmpeg with libass, the font (in template/fonts/) and PyTorch/CUDA. No setup instructions for those; if a script needs an extra package, name it exactly.
- Code is written on a laptop (WSL Ubuntu, AMD GPU not usable, no real model runs there) and run on a desktop (Windows + WSL2, NVIDIA RTX 5070 12 GB, 32 GB RAM). Do not try to run the recognition or alignment models on the laptop. Everything that does not need a model must be testable on the laptop: provide a --fake mode for recognize.py (writes a words.json derived from a text fixture, with simulated errors and repeats) and a fake-aligner option for align.py. Models run only on the desktop (device "auto" = cuda if available, else cpu).
- No Docker. No Git steps. All paths relative to the project root; machine-specific settings in optional config.local.yaml overriding config.yaml.
- match.py is pure Python plus rapidfuzz: no torch, no audio.

## 11. DATA FORMATS (contracts; change only with user approval)
Everyone imports normalize_arabic(text: str) -> str from scripts/normalize.py (strips marks and tatweel, unifies alif/ya/ta-marbuta variants); nobody redefines it.

### work/<surah>/text.json (fetch_text.py)
    { "surah": 114, "riwaya": "hafs", "title_ar": "...", "title_en": "...",
      "bismillah": null | { "arabic": "...", "translit": "...", "english": "...", "words": [Word] },
      "ayahs": [ { "n": 1, "arabic": "<full Uthmani>", "translit": "<joined>", "english": "<API translation>", "words": [Word] } ] }
    Word = { "i": 0, "arabic": "<Uthmani word>", "norm": "<normalize_arabic(arabic)>", "translit": "..." }
Words joined in order must equal the ayah's Uthmani text (report any difference). Translit = join of per-word translit. bismillah is null for Surah 1 and 9. No per-word English.

### work/<surah>/take_<n>/words.json (recognize.py)
    { "take": 1, "audio": "audio/raw/114_t1.m4a", "duration": 61.2, "model": "<name>",
      "words": [ { "text": "...", "norm": "<normalize_arabic(text)>", "start": 0.00, "end": 0.42, "conf": 0.93 } ] }
Seconds from the start of that take's file; conf 0..1 or null.

### work/<surah>/spans.json (match.py)
    { "surah": 114, "bismillah_expected": true, "window": 4,
      "spans": [ { "id": 0, "take": 1, "kind": "bismillah" | "ayah", "ayah": 3 | null, "start": 12.30, "end": 18.90,
                   "word_range": [first, last], "complete": true, "conf": 0.91, "superseded": false } ],
      "kept_ids": [0, 1, 2],
      "dropped": [ { "take": 1, "start": 20.0, "end": 24.1, "reason": "abandoned_partial" | "superseded_repeat" | "unmatched", "ayah": 3 | null } ],
      "flags": [Flag] }
word_range indexes into that take's words.json. kept_ids = final order: bismillah first, then ayahs ascending, one complete non-superseded span per ayah. An ayah with no kept span gets a missing_ayah flag.

### work/<surah>/aligned.json (align.py)
    { "surah": 114,
      "items": [ { "span_id": 0, "kind": "bismillah" | "ayah", "ayah": 3 | null, "take": 1,
                   "cut_start": 12.20, "cut_end": 19.05,
                   "words": [ { "i": 0, "start": 12.30, "end": 12.80, "conf": 0.88 } ], "conf": 0.90 } ],
      "flags": [Flag] }
Items follow kept_ids order. Times are source-take seconds; cut points are snapped to silence; word times lie inside [cut_start, cut_end]; i = Word.i of text.json.

### work/<surah>/overrides.json (review.py, user decisions; may be absent)
    { "drop_spans": [3], "shift": [ { "span_id": 2, "cut_start_delta": -0.10, "cut_end_delta": 0.20 } ],
      "accept_flags": [ { "type": "low_align_conf", "ayah": 5 } ] }
Applied to aligned.json after the align stage, before render: dropped spans make their ayah missing (flagged), shifts adjust cuts (and clamp word times), accepted flags no longer stop the run.

### work/<surah>/timeline.json (render.py, read by make_ass.py)
    { "surah": 114, "audio": "audio/cut/114.wav", "duration": 98.4,
      "items": [ { "kind": "bismillah" | "ayah", "ayah": 3 | null, "start": 5.0, "end": 11.2,
                   "words": [ { "i": 0, "start": 5.1, "end": 5.6, "conf": 0.88 } ] } ] }
Times in the EDITED audio: t_final = t_source - cut_start + item offset.

### Flag (spans, aligned, review.json)
    { "type": "...", "take": 1 | null, "time": 12.3, "ayah": 3 | null, "severity": "info" | "warn" | "error", "detail": "..." }
Types: low_match_conf, out_of_window, unmatched_segment, partial_ayah, missing_ayah, bismillah_missing, bismillah_unexpected, low_align_conf, abnormal_duration, low_word_conf_boundary, text_overflow, english_overflow. "time" = source-take seconds for match/align flags, final-audio seconds for review-stage flags.

### Signatures
    scripts/match.py: run_matching(text: dict, takes: list[dict], cfg: dict) -> dict   # spans.json structure; takes = parsed words.json in take order
    scripts/align.py: run_alignment(text: dict, spans: dict, cfg: dict, project_root: str) -> dict   # aligned.json structure
Both also have a CLI: python scripts/match.py --surah N, python scripts/align.py --surah N (read/write the work files). Thresholds come only from cfg.

### Config keys the SPECIALIST modules read (BUILDER creates them in Step 1; values are starting guesses)
    bismillah: true
    matching: { repeat_window: 4, repeat_mode: last, min_match_score: 0.75, low_conf_score: 0.85 }
    alignment: { silence_snap_max_s: 0.30, silence_threshold_db: -40, trim_last_word_silence: true, min_word_conf: 0.30, max_ayah_seconds_factor: 3.0 }
    models: { aligner: "MahmoudAshraf/mms-300m-1130-forced-aligner", device: "auto" }

## 12. DEFINITION OF DONE
- Text in the video is identical to Quran.com's for every ayah shown, full Uthmani marks.
- Short ayahs share a screen when all three layers fit readably, long ayahs split into chunks, English is always the full ayah-level translation.
- Screen changes land within a fraction of a second of the voice.
- Repeats and abandoned partials are gone from the final audio; no ayah is shown that was not completed.
- Several takes become one continuous video in ayah order.
- The user only looks at flagged spots, and their review decisions survive reruns.
- A new surah needs one command and no code changes.