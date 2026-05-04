# SCOTUS Pipeline v2

This is an isolated rewrite of the case processing pipeline. It does not modify
or replace the current numbered processors or `run_pipeline.sh`, and the v2
runner does not call them.

The v2 design is configuration-first and typed end to end:

- Pydantic models validate pipeline configuration, case metadata, artifacts, and
  step results.
- Pipeline steps are Python classes with explicit inputs, outputs, validation,
  immutable timeline snapshots, and hash-aware resume behavior.
- Step execution calls in-package native processors under
  `scotus_pipeline.processors._native`; there is no root-script compatibility
  executor.
- Progress, logging, notifications, and step lifecycle events flow through one
  event bus.
- TOML is supported without optional parser behavior; YAML is also supported.
- Artifact JSON is versioned with `schema_version = "1"` and validated at the
  step boundary to catch cross-case leakage before later stages consume it.
- State is written as JSON so failed runs can be inspected and resumed.
- The viewer step emits a JSON bundle for the frontend. Python no longer renders
  the final HTML viewer.

## Quick Start

```bash
cd pipeline-v2
uv run --with-editable . scotus-pipeline validate configs/case.example.toml
uv run --with-editable . scotus-pipeline plan configs/case.example.toml
uv run --with-editable . scotus-pipeline run configs/case.example.toml --dry-run
```

Run against an already archived case:

```bash
uv run --with-editable . scotus-pipeline run configs/case.example.toml \
  --case-dir ../.courts/sources/24-109
```

Run a new docket end to end:

```bash
uv run --with-editable . scotus-pipeline validate configs/my-new-case.toml
uv run --with-editable . scotus-pipeline plan configs/my-new-case.toml
uv run --with-editable . scotus-pipeline run configs/my-new-case.toml --no-resume
```

The frontend input is written as `output/<docket>/bundle.json`, with the public
audio copied beside it as `output/<docket>/audio.mp3`.

## Paths

Pipeline v2 now has two roots:

- `.courts/` for internal sources, cache, work artifacts, run state, and logs.
- `output/<docket>/` for the viewer-facing deliverable only.

For docket `24-624`, the generated bundle is:

```text
output/24-624/bundle.json
```

## Design Notes

Pipeline v2 is a self-contained package plus native processors. The CLI builds
a typed config, resolves case artifacts, runs each native processor, emits
structured progress/notification events, and records typed `StepResult` state
for resume.

The alignment cache is source-aware: cached chunk JSON is reused only when its
window, audio hash, speaker-turn hash, speaker-turn source, model settings, and
alignment thresholds match the current case. This prevents stale artifacts from
another docket from contaminating a new viewer build.

Native WhisperX chunk alignment uses a dedicated JSONL worker process by
default. The worker keeps ASR model state outside the orchestrator, preserves
structured progress events, and writes worker stderr to
`.courts/cache/<docket>/asr/asr-worker.stderr.log` instead of an unread pipe.

Timeline snapshots are copied under `.courts/work/<docket>/timeline/snapshots/`
after step validation succeeds, so the public output folder never fills with
intermediate checkpoints.

Runtime system dependencies:

- `pdftotext` for transcript extraction.
- `ffprobe` for audio duration checks.
- `ffmpeg` for chunk audio extraction.
- `torch` and `whisperx` when ASR alignment has to compute fresh cache entries.
