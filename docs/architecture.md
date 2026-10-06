# Pipeline Architecture

## Core Patterns

- `PipelineConfig`: Pydantic configuration aggregate loaded from TOML or YAML.
- `PipelineRunner`: application service that resolves workspace, state, steps,
  and event delivery.
- `PipelineStep`: template-method base class for step lifecycle handling.
- `artifacts`: Pydantic schemas for versioned on-disk artifacts.
- `io`: atomic writers, hashing, and per-step snapshot helpers.
- `processors`: in-package processor modules for archive, transcript,
  timeline, alignment, merge, official word alignment, speech-gap refinement,
  speaker profiles, QA marker application, and viewer bundle generation.
- `asr`: JSONL worker process used for WhisperX work outside the orchestrator.
- `EventBus`: publish/subscribe lifecycle events for progress and notifications.
- `Notifier`: strategy interface for console, JSONL, and webhook delivery.
- `StateStore`: repository for resumable run state.

## Code boundaries

The CLI loads configuration and delegates execution to `PipelineRunner`.
The runner coordinates stages and persistence; it does not perform transcript
processing. `steps/registry.py` owns stage order, enablement, and step selection.

```text
scotus_pipeline/
  cli.py                 # command parsing and presentation
  config.py              # configuration validation
  runner.py              # execution, progress, and state persistence
  context.py             # runtime state and workspace resolution
  cases.py               # source discovery and case artifact layout
  steps/
    base.py              # common execution/validation/error lifecycle
    registry.py          # ordered stages and configuration gates
    archive.py           # official source ingestion
    transcript.py        # PDF extraction
    timeline.py          # timeline construction, merge, and refinement
    alignment.py         # alignment adapters and quality validation
    qa.py                # reviewed timing markers
    publishing.py        # profiles and viewer bundle
  processors/
    official_words/
      service.py         # method selection, cache reuse, and output writes
      cache.py           # input fingerprints
      tokens.py          # transcript tokenization
      asr.py             # matching to cached ASR words
      blocks.py          # model-driven block alignment
      timing.py          # coverage, scores, and interpolation
      timeline.py        # accepted timing application and fallback constraints
      progress.py        # processor progress callback
  asr/                   # isolated WhisperX worker and protocol
```

Stage modules translate runtime configuration into typed processor inputs.
Processors own the algorithms; reusable word-alignment code has no dependency
on the CLI, runner, or step classes. `official_words.run` remains the processor
entry point, and both cached ASR and block alignment produce the same artifact
contract. Model imports occur only when block alignment needs inference.

The common stage lifecycle computes input/output hashes once for a resume
check. Execution and validation failures share result construction and error
reporting, preserving elapsed time and available input hashes.

## Step Lifecycle

1. Resolve case artifacts.
2. Hash current step inputs and expected outputs; missing declared inputs fail
   before execution.
3. Emit `step.planned` and `step.progress`.
4. Apply skip policy using state plus current input/output hashes.
5. Emit `step.started`.
6. Execute the in-package Python processor. Dry runs only describe enabled steps.
7. Validate versioned artifacts at the step boundary.
8. Write per-step timeline snapshots for steps that rewrite the timeline after
   validation succeeds.
9. Emit `step.completed` or `step.failed`.
10. Persist `StepResult` when not in dry-run mode.

## Isolation Boundary

The runner does not call the legacy numbered processors. Step
classes call modules under `scotus_pipeline.processors` directly, and
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

State is stored at `.courts/runs/<docket>/state.json`. Each `StepResult` stores
the input and output hashes used for the skip decision. Successful records are
preserved when a later run skips the same stage. Each result also stores a hash
of processing configuration; older records lacking that hash are recomputed.
State is scoped by docket; use separate workspaces for independent runs.

Timeline stages write distinct files (`build_timeline.json`, `merge_alignment.json`,
`align_official_words.json`, `refine_speech_gaps.json`, `apply_qa_markers.json`).
Each stage consumes the preceding enabled timeline stage. QA precedes profiles;
the bundle step also copies the final timeline to the canonical `timeline.json`.

Official-word alignment artifacts also store a `source_fingerprint` over docket,
audio hash, method, processing settings, ASR cache hashes, and transcript turn text. Existing alignment JSON is reused
only when that fingerprint matches the current timeline, which catches stale
files even if their outer case metadata was rewritten.
