# Quran Videos

This pipeline turns one or more ordered recitation recordings for a single
surah into a reviewed, timed video. Quran text is fetched from Quran.com;
recognized speech is used only for matching and timing.

## Requirements

- Python 3.10 or newer
- Dependencies from `requirements.txt`
- FFmpeg on the `PATH` for audio cutting and final rendering
- The configured fonts and background/template files
- A configured recognition model for real audio
- A configured CTC alignment environment for real audio

Install the Python dependencies:

```bash
python -m pip install -r requirements.txt
```

## One-surah command

Pass the surah number and recordings in recitation order. Each recording is
treated as one take:

```bash
python scripts/run_pipeline.py \
  --surah 114 \
  --audio recordings/114_take1.m4a recordings/114_take2.m4a
```

The default configuration is [config.yaml](config.yaml). Use another
configuration with `--config PATH`.

The pipeline runs these stages in order:

1. Fetch Quran.com text.
2. Recognize each take and cache word timestamps.
3. Match recognized words to ayahs.
4. Force-align accepted spans.
5. Write `review.json` and `review.html`.
6. Cut and join accepted audio and write `timeline.json`.

Stage outputs are written under `work/<surah>/`; logs are written under
`logs/<surah>.log`.

## Review workflow

If unresolved warning or error flags remain, the pipeline stops with exit code
`2` and prints the review page path. Record decisions with:

```bash
python scripts/review.py --surah 114 --drop 7
python scripts/review.py --surah 114 --shift 3 -0.10 0.20
python scripts/review.py --surah 114 --accept low_align_conf 5
```

Then render after review:

```bash
python scripts/run_pipeline.py \
  --surah 114 \
  --audio recordings/114_take1.m4a recordings/114_take2.m4a \
  --stage render
```

`--stage STAGE` runs through the selected stage. `--force STAGE` reruns that
stage and later stages. Recognition results are cached per take and audio
hash; other stages use `work/<surah>/manifest.json`.

## Deterministic fake recognition

Laptop tests can generate fixture-based recognition outputs without audio or
model downloads:

```bash
python scripts/recognize.py --fake --scenario clean --surah 114
```

Available scenarios are listed under `tests/scenarios/`. Fake outputs use
`fake://` audio identifiers and are never treated as real audio for CTC
alignment.

## Exit codes

- `0`: completed successfully
- `1`: configuration, input, model, FFmpeg, or other execution error
- `2`: stopped at review because unresolved warning/error flags remain

## Validation

Run the regression suites with:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

Real model inference and FFmpeg rendering require the desktop environment
where those dependencies and recordings are available.