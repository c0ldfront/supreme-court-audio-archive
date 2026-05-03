# Pipeline v2 Architecture

## Core Patterns

- `PipelineConfig`: Pydantic configuration aggregate loaded from TOML or YAML.
- `PipelineRunner`: application service that resolves workspace, state, steps,
  and event delivery.
- `PipelineStep`: template-method base class for step lifecycle handling.
- `processors._native`: in-package processor modules for archive, transcript,
  timeline, alignment, merge, official word alignment, speech-gap refinement,
  speaker profiles, QA marker application, and static viewer rendering.
- `EventBus`: publish/subscribe lifecycle events for progress and notifications.
- `Notifier`: strategy interface for console, JSONL, and webhook delivery.
- `StateStore`: repository for resumable run state.

## Step Lifecycle

1. Resolve case artifacts.
2. Emit `step.planned` and `step.progress`.
3. Apply skip policy.
4. Emit `step.started`.
5. Execute the native in-package Python processor or dry-run plan.
6. Emit `step.completed` or `step.failed`.
7. Persist `StepResult` when not in dry-run mode.

## Isolation Boundary

The v2 runner does not call the repository root numbered processors. Step
classes call modules under `scotus_pipeline.processors._native` directly, and
the CLI plan reports the processor class instead of a shell command.

External command use is limited to domain tools such as `pdftotext`, `ffprobe`,
and `ffmpeg`, plus optional in-process ASR libraries (`whisperx`/`torch`) when
alignment work needs fresh speech recognition. Cache validation includes the
speaker-turn source path so a stale chunk from a different docket is rejected
instead of being merged into the current case.
