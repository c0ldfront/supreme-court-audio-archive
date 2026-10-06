# SCOTUS Pipeline

A configurable Python CLI for archiving Supreme Court oral arguments and
aligning official transcript turns with their audio.

## Commands

Run from `pipeline/` in a source checkout:

```bash
uv sync --extra asr
uv run --extra asr scotus-pipeline validate configs/case-24-624.toml
uv run --extra asr scotus-pipeline plan configs/case-24-624.toml --json
uv run --extra asr scotus-pipeline run configs/case-24-624.toml --dry-run
uv run --extra asr scotus-pipeline run configs/case-24-624.toml
```

Python 3.12 is required. Processing uses `pdftotext`, `ffprobe`, and `ffmpeg`.
Fresh WhisperX alignment uses CUDA when available and falls back to CPU. The base
package supports planning, validation, and tests without ASR dependencies.

For an installed package, invoke `scotus-pipeline` directly. Create a standalone
config with `scotus-pipeline init-config case.toml`; the command refuses to
overwrite an existing file. Relative `project.workspace` values are resolved
against the configuration file's directory.

## Processing and resume

```bash
# Execute every enabled stage again, reusing valid processor caches.
uv run --extra asr scotus-pipeline run configs/case-24-624.toml --no-resume

# Use existing source audio, PDF, and metadata without fetching the archive.
uv run --extra asr scotus-pipeline run configs/case-24-624.toml \
  --case-dir ../.courts/sources/24-624

# Run a single stage after its prerequisites exist.
uv run --extra asr scotus-pipeline run configs/case-24-624.toml --only-step build_viewer
```

`--no-resume` bypasses saved step completion; it does not discard valid WhisperX
caches. Set `[alignment].overwrite = true` to recompute audio alignment. Return
it to `false` afterward if you want subsequent runs to reuse those results.
Use `--no-resume` after changing processing settings; resume currently compares
file hashes, not the full configuration.

`--from-step STEP` begins at a named stage. Unknown steps and conflicting
selectors are errors. Disabled stages do not require their inputs to exist.
Dry runs and plans do not write state, logs, or artifacts or send webhooks.

## Quality and artifacts

Alignment warnings are printed even if processing completes. Set
`[alignment].stop_on_warning = true` to reject warning-quality output.
Inspect `.courts/work/<docket>/alignment/chunk-report.json` for chunk results.
The default `tiny.en` model is useful for iteration; model size is configurable.
Processing currently serializes WhisperX chunks, regardless of `workers`.

The viewer consumes `output/<docket>/bundle.json` and its adjacent `audio.mp3`.
All intermediate data stays in `.courts/`; case bundles remove local filesystem
paths from published metadata.

See the repository's `docs/` directory for architecture and operational notes.
