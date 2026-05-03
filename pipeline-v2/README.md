# SCOTUS Pipeline v2

This is an isolated rewrite of the case processing pipeline. It does not modify
or replace the current numbered processors or `run_pipeline.sh`, and the v2
runner does not call them.

The v2 design is configuration-first and typed end to end:

- Pydantic models validate pipeline configuration, case metadata, artifacts, and
  step results.
- Pipeline steps are Python classes with explicit inputs, outputs, and retry
  behavior.
- Step execution calls in-package native processors under
  `scotus_pipeline.processors._native`; there is no root-script compatibility
  executor.
- Progress, logging, notifications, and step lifecycle events flow through one
  event bus.
- TOML is supported without optional parser behavior; YAML is also supported.
- State is written as JSON so failed runs can be inspected and resumed.

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
  --case-dir ../archive/scotus-audio/2025/24-109_2
```

## Design Notes

Pipeline v2 is now a self-contained package shell plus native processors. The
CLI builds a typed config, resolves case artifacts, runs each native processor,
emits structured progress/notification events, and records typed `StepResult`
state for resume.

The alignment cache is source-aware: cached chunk JSON is reused only when its
window and `speaker_turns` source match the current case, which prevents stale
artifacts from another docket from contaminating a new viewer build.

Native WhisperX chunk alignment currently serializes execution to one worker
inside the process, even if a legacy config requests more workers. This avoids
loading multiple ASR models into the same Python process/GPU state.
