# SCOTUS Pipeline

The Python package processes Supreme Court oral arguments into aligned transcripts
and viewer bundles. The older numbered-script implementation is preserved in
`../legacy/`.

The pipeline is configuration-first and typed end to end:

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
cd pipeline
uv run --with-editable . scotus-pipeline validate configs/case.example.toml
uv run --with-editable . scotus-pipeline plan configs/case.example.toml
```

Run against an already archived case:

```bash
uv run --with-editable . scotus-pipeline run configs/case.example.toml \
  --case-dir ../.courts/sources/24-109
```

Run a new docket end to end:

```bash
uv run --with-editable . scotus-pipeline validate configs/case-24-624.toml
uv run --with-editable . scotus-pipeline plan configs/case-24-624.toml
uv run --with-editable . scotus-pipeline run configs/case-24-624.toml --no-resume
```

The frontend input is written as `output/<docket>/bundle.json`, with the public
audio copied beside it as `output/<docket>/audio.mp3`.

## Paths

The pipeline now has two roots:

- `.courts/` for internal sources, cache, work artifacts, run state, and logs.
- `output/<docket>/` for the viewer-facing deliverable only.

For docket `24-624`, the generated bundle is:

```text
output/24-624/bundle.json
```

## Design Notes

The pipeline is a self-contained package plus native processors. The CLI builds
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
