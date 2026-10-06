# Pipeline Architecture

## Core Patterns

- `PipelineConfig`: Pydantic configuration aggregate loaded from TOML or YAML.
- `PipelineRunner`: application service that resolves workspace, state, steps,
  and event delivery.
- `PipelineStep`: template-method base class for step lifecycle handling.
- `artifacts`: Pydantic schemas for versioned on-disk artifacts.
- `io`: atomic writers, hashing, and immutable snapshot helpers.
- `processors._native`: in-package processor modules for archive, transcript,
  timeline, alignment, merge, official word alignment, speech-gap refinement,
  speaker profiles, QA marker application, and viewer bundle generation.
- `asr`: JSONL worker process used for WhisperX work outside the orchestrator.
- `EventBus`: publish/subscribe lifecycle events for progress and notifications.
- `Notifier`: strategy interface for console, JSONL, and webhook delivery.
- `StateStore`: repository for resumable run state.

## Step Lifecycle

1. Resolve case artifacts.
2. Hash current step inputs and expected outputs; missing declared inputs fail
   before execution.
3. Emit `step.planned` and `step.progress`.
4. Apply skip policy using state plus current input/output hashes.
5. Emit `step.started`.
6. Execute the native in-package Python processor or dry-run plan.
7. Validate versioned artifacts at the step boundary.
8. Write immutable timeline snapshots for steps that rewrite the timeline after
   validation succeeds.
9. Emit `step.completed` or `step.failed`.
10. Persist `StepResult` when not in dry-run mode.

## Isolation Boundary

The runner does not call the legacy numbered processors. Step
classes call modules under `scotus_pipeline.processors._native` directly, and
the CLI plan reports the processor class instead of a shell command.

External command use is limited to domain tools such as `pdftotext`, `ffprobe`,
and `ffmpeg`, plus optional ASR libraries (`whisperx`/`torch`) inside the ASR
worker when alignment work needs fresh speech recognition. Cache validation
includes the audio hash, speaker-turn hash, source path, chunk window, and model
settings so a stale chunk from a different docket is rejected instead of being
merged into the current case.

## Artifact Contract

Every primary JSON artifact is written through the package atomic writer and
validated with a Pydantic `schema_version = "1"` model before downstream steps
consume it. Case-bearing artifacts validate `case.audio_file` against the
current resolved case audio path, which is the guard that prevents a 24-109
artifact from being accepted while building a 24-624 viewer.

The final viewer product is `output/<docket>/bundle.json`.
It contains the validated timeline, speaker profiles, relative audio source,
and case metadata. Astro or any other frontend owns HTML rendering from that
bundle.

## Filesystem Layout

The runtime has one internal root and one output root:

- `.courts/sources/<docket>/` stores downloaded Supreme Court audio,
  transcripts, and metadata.
- `.courts/sources/_index/` stores archive manifests.
- `.courts/cache/<docket>/asr/` stores reusable WhisperX WAV/JSON cache.
- `.courts/work/<docket>/` stores typed intermediate artifacts.
- `.courts/runs/<docket>/` stores resumable state and event logs.
- `output/<docket>/` stores the public viewer payload.

For a normal case run, generated artifacts are grouped under:

```text
.courts/work/<docket>/transcript/
.courts/work/<docket>/alignment/
.courts/work/<docket>/timeline/
.courts/work/<docket>/profiles/
output/<docket>/bundle.json
output/<docket>/audio.mp3
```

## Runtime State

State filenames include the project name, docket, and term to prevent two cases
with the same workspace from sharing one resume file. Each `StepResult` stores
the input and output hashes used for the skip decision.

Official-word alignment artifacts also store a `source_fingerprint` over docket,
audio hash, method, and transcript turn text. Existing alignment JSON is reused
only when that fingerprint matches the current timeline, which catches stale
files even if their outer case metadata was rewritten.
