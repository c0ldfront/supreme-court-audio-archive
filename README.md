# SCOTUS Transcript Pipeline

Archive Supreme Court oral argument audio and official transcripts, align words
and speaker turns using WhisperX, and build an interactive transcript viewer.

## Project layout

| Path | Purpose |
| --- | --- |
| `pipeline/` | Python package, CLI, case configurations, and tests |
| `viewer/` | Astro transcript viewer |
| `legacy/` | Original numbered scripts, their environment, and preserved outputs |
| `.courts/` | Local source audio, transcripts, alignment cache, state, and logs (ignored) |
| `output/<docket>/` | Generated viewer bundles and packaged sites (ignored) |

## Process a case

```bash
cd pipeline
uv sync
uv run scotus-pipeline validate configs/case.example.toml
uv run scotus-pipeline plan configs/case.example.toml
```

For fresh audio alignment, install the optional dependencies with
`uv sync --extra asr`. The pipeline also requires `ffmpeg`, `ffprobe`, and
`pdftotext`. See [pipeline documentation](pipeline/README.md) for full runs,
resume behavior, and configuration. `configs/case-24-624.toml` contains the
existing configuration for docket 24-624.

## Build the viewer

After the pipeline produces `output/<docket>/bundle.json` and `audio.mp3`:

```bash
cd viewer
npm ci
npm run build:case -- 24-624 --base /your-repository/24-624/
```

See [viewer documentation](viewer/README.md) for development and deployment.
The existing GitHub Pages base remains `/supreme-court-ingest/`; override
`--base` when deploying under another repository name.

## Verify changes

```bash
cd pipeline
uv run python -m unittest discover -s tests -v
cd ../viewer
npm run typecheck
npm run build
```

## Legacy implementation

The original workflow remains in [legacy/](legacy/README.md). Run its commands
from that directory; `legacy/run_pipeline.sh` sets its working directory itself.
Historical output folders retain their original names and contents there.
Generated audio, caches, logs, and output bundles are local artifacts, not source.
They are preserved on disk and excluded from future commits. Previously tracked
outputs remain recoverable from checkpoint commit `407fc4e`.

Existing cached metadata may contain absolute paths from the former checkout
location. Regenerate a case if those historical paths prevent reuse. Python
virtual environments are also local: use `uv sync` in the relevant package to
refresh them after relocation.

For a dry run against existing source files, supply the case directory explicitly:

```bash
cd pipeline
uv run scotus-pipeline run configs/case-24-624.toml --dry-run --no-resume --case-dir ../.courts/sources/24-624
```

The existing dry-run implementation requires resolved case artifacts; a dry run
starting with only a docket fails after the archive step. Use `plan` for cases
that have not been archived yet.
