# Quran Videos

This project turns ordered recitation recordings for one Quran surah into
reviewed, timed output. Quran text is fetched from Quran.com API v4.
Recognition output is used only as evidence for matching and timing; it is
never displayed.

The normal workflow is:

```text
clone → configure desktop → install dependencies → verify → run pipeline
      → inspect review flags → record decisions → render
```

## 1. Desktop prerequisites

Use the desktop for real recordings, model inference, CTC alignment, and
FFmpeg processing. The repository can be tested without those resources by
using the deterministic fake-recognition scenarios.

Required:

- Python 3.10 or newer
- Git
- FFmpeg available as the `ffmpeg` command
- Internet access for Quran.com and model downloads
- Enough disk space for model caches and intermediate audio
- The configured font files, background image, and ASS template
- A desktop GPU with a compatible PyTorch installation is recommended for
  real recognition and forced alignment

Check the external tools before starting:

```bash
python --version
git --version
ffmpeg -version
```

If `ffmpeg -version` fails, install FFmpeg using the operating system's
package manager or official distribution, then open a new terminal and run
the command again. The `.m4a` silence detector and audio renderer require
FFmpeg.

## 2. Clone and enter the repository

Clone the GitHub repository on the desktop:

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL>
cd quran-videos
```

Use the branch or commit containing the completed pipeline. Confirm that the
expected files are present:

```bash
python scripts/run_pipeline.py --help
ls config.yaml requirements.txt scripts tests
```

Do not copy generated `work/<surah>/` files from another machine for a fresh
real-audio run. They contain machine-specific paths, hashes, and cached
results.

## 3. Create the Python environment

Create and activate a virtual environment:

```bash
python -m venv .venv
```

Linux/macOS:

```bash
source .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Install the declared dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For real CTC alignment, install the `ctc-forced-aligner` package and its
PyTorch/runtime dependencies in the desktop environment required by that
package. The package is intentionally not downloaded by the project
automatically. Verify the import:

```bash
python -c "import ctc_forced_aligner; print(ctc_forced_aligner)"
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

If CUDA is available and the configuration uses `device: auto`, the pipeline
can select CUDA. Set `models.device` to `cpu` or `cuda` in `config.yaml` when
you need an explicit choice.

## 4. Check the configuration

The default settings are in [config.yaml](config.yaml). Important values
include:

- `api.base_url`: Quran.com API v4 endpoint
- `api.translation_id`: `20`, Saheeh International
- `api.transliteration_id`: `57`, Transliteration
- `api.riwaya`: `hafs`
- `models.recognizer`: recognition model
- `models.aligner`: CTC alignment model
- `models.device`: `auto`, `cpu`, or `cuda`
- `alignment.silence_threshold_db` and `alignment.silence_snap_max_s`
- `paths.background`, `paths.template_ass`, and output directories
- `display.*_font`: configured font files used for measurement and ASS
  rendering

Paths in the configuration are relative to the repository root. Confirm that
the configured files exist:

```bash
python - <<'PY'
from pathlib import Path
import yaml

with open("config.yaml", encoding="utf-8") as handle:
    config = yaml.safe_load(handle)
for key in ("background", "template_ass"):
    path = Path(config["paths"][key])
    print(f"{key}: {path} {'OK' if path.is_file() else 'MISSING'}")
for key in ("reciter_font", "arabic_font", "translit_font", "english_font"):
    path = Path(config["display"][key])
    print(f"{key}: {path} {'OK' if path.is_file() else 'MISSING'}")
PY
```

The CLI validates the configuration at startup. Do not put credentials or
secrets in `config.yaml`.

## 5. Add recordings

Place recordings anywhere outside the generated `work/` directories, for
example:

```text
recordings/
  114_take1.m4a
  114_take2.m4a
```

Recordings must be passed in recitation order. Each file is one take. The
matcher carries its furthest-ayah position between takes and supports
repeats, abandoned restarts, and partial recitations.

Check that each input is readable by FFmpeg:

```bash
ffprobe recordings/114_take1.m4a
ffprobe recordings/114_take2.m4a
```

## 6. Run the real pipeline

Run all stages through review:

```bash
python scripts/run_pipeline.py \
  --surah 114 \
  --audio recordings/114_take1.m4a recordings/114_take2.m4a
```

The stages run in this order:

1. **fetch** — retrieves Quran.com text and writes `text.json`.
2. **recognize** — runs word-timestamp recognition for every take.
3. **match** — matches recognized words to expected ayahs and writes
   `spans.json`.
4. **align** — force-aligns accepted spans and writes `aligned.json`.
5. **review** — writes `review.json` and `review.html`.
6. **render** — cuts and joins accepted audio and writes `timeline.json`.

Generated files are stored under:

```text
work/<surah>/
  text.json
  take_<n>/words.json
  spans.json
  aligned.json
  review.json
  review.html
  overrides.json
  edited.wav
  timeline.json
```

Logs are written to `logs/<surah>.log`.

The first real run downloads model assets if they are not already cached.
This can take time and requires network access.

## 7. Run individual stages

Use `--stage` to run through a particular stage after earlier outputs are
available:

```bash
python scripts/run_pipeline.py \
  --surah 114 \
  --audio recordings/114_take1.m4a recordings/114_take2.m4a \
  --stage align
```

Use `--force` to rerun the selected stage and all later stages:

```bash
python scripts/run_pipeline.py \
  --surah 114 \
  --audio recordings/114_take1.m4a recordings/114_take2.m4a \
  --force recognize
```

Recognition is cached per take using the audio hash and model name. Other
stage cache records are stored in `work/<surah>/manifest.json`. If an input
or relevant configuration section changes, the affected stage is rerun.

You can also run recognition directly:

```bash
python scripts/recognize.py \
  --surah 114 \
  --take 1 \
  --audio recordings/114_take1.m4a
```

## 8. Review flags and decisions

If unresolved `warn` or `error` flags remain after the review stage, the
pipeline exits with code `2` and prints the path to `review.html`.

Open the page in a browser:

```bash
open work/114/review.html       # macOS
xdg-open work/114/review.html   # Linux
start work/114/review.html      # Windows
```

Record decisions with `review.py`:

```bash
# Drop a bad accepted span.
python scripts/review.py --surah 114 --drop 7

# Shift the start and end cut points for a span.
python scripts/review.py --surah 114 --shift 3 -0.10 0.20

# Accept a flag type for one ayah; use "none" for an ayah-independent flag.
python scripts/review.py --surah 114 --accept low_align_conf 5
python scripts/review.py --surah 114 --accept bismillah_missing none
```

Then rerun review:

```bash
python scripts/run_pipeline.py \
  --surah 114 \
  --audio recordings/114_take1.m4a recordings/114_take2.m4a \
  --stage review
```

After review is acceptable, render the reviewed audio:

```bash
python scripts/run_pipeline.py \
  --surah 114 \
  --audio recordings/114_take1.m4a recordings/114_take2.m4a \
  --stage render
```

## 9. Desktop verification checklist

For the first desktop run, capture these checks:

```bash
python -m unittest discover -s tests -p 'test_*.py'
python -c "import ctc_forced_aligner; print(ctc_forced_aligner)"
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
ffmpeg -version
```

Then run the real pipeline and verify:

```bash
test -f work/114/aligned.json
test -f work/114/review.json
test -f work/114/timeline.json
python -m json.tool work/114/aligned.json >/dev/null
python -m json.tool work/114/review.json >/dev/null
python -m json.tool work/114/timeline.json >/dev/null
```

Inspect `aligned.json` and confirm that:

- items follow the accepted span order;
- every expected word has one aligned record;
- timestamps are finite and ordered;
- words remain inside their cuts;
- source take numbers and ayah numbers are correct.

Inspect `timeline.json` and confirm that:

- items are in Bismillah/ayah order;
- repeated and dropped spans are absent;
- word times are relative to the edited audio;
- `duration` matches the joined audio.

## 10. Current output boundary

The current render stage cuts and joins the reviewed audio and writes
`edited.wav` plus `timeline.json`. Subtitle generation is available through
`make_ass.py`, but the pipeline does not yet automatically perform the final
ASS burn-in and MP4 encoding step.

To generate ASS from an existing timeline:

```bash
python scripts/make_ass.py \
  --surah 114 \
  --timeline work/114/timeline.json
```

The final video burn-in/MP4 encoding still requires an explicit FFmpeg
command using the generated ASS, the configured background, and
`work/114/edited.wav`. Do not treat the presence of `timeline.json` as proof
that a finished MP4 was produced.

## 11. Deterministic fake recognition and laptop tests

Fake recognition uses only API-derived fixtures and does not load audio or a
model:

```bash
python scripts/recognize.py --fake --scenario clean --surah 114
```

Available scenarios are documented in `tests/scenarios/`. Fake outputs use
`fake://` audio identifiers and are never treated as real audio for CTC
alignment.

Run the complete test suite:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

One FFmpeg-dependent `.m4a` test may be skipped on a machine without FFmpeg.
That skip does not verify real `.m4a` decoding; install FFmpeg and rerun the
suite when desktop verification is required.

## 12. Exit codes and troubleshooting

- `0` — stage or pipeline completed successfully.
- `1` — configuration, input, dependency, model, FFmpeg, or other execution
  error.
- `2` — stopped because unresolved review warnings/errors remain.

Common problems:

- **`ffmpeg is required`**: install FFmpeg and ensure `ffmpeg -version`
  works from the same terminal used to run Python.
- **`ctc-forced-aligner package is not installed`**: install the package in
  the active virtual environment and verify its import.
- **No word timestamps**: verify that the recognizer supports word
  timestamps and that the configured model loaded successfully.
- **Missing configured file**: run the configuration file check in section 4
  and correct the path relative to the repository root.
- **Review exit code 2**: inspect `work/<surah>/review.html`, record explicit
  decisions, then rerun the review or render stage.
- **Stale results**: use `--force STAGE`; recognition hashes audio files, and
  later stage outputs are tracked in `manifest.json`.

Keep the original recordings and `work/<surah>/review.json`,
`overrides.json`, and `timeline.json` together if you need to reproduce or
audit a result.
