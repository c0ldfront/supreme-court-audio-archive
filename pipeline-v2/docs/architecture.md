# Pipeline v2 Architecture

## Core Patterns

- `PipelineConfig`: Pydantic configuration aggregate loaded from TOML or YAML.
- `PipelineRunner`: application service that resolves workspace, state, steps,
  and event delivery.
- `PipelineStep`: template-method base class for step lifecycle handling.
- `PythonProcessExecutor`: process boundary for long-running Python processors.
- `EventBus`: publish/subscribe lifecycle events for progress and notifications.
- `Notifier`: strategy interface for console, JSONL, and webhook delivery.
- `StateStore`: repository for resumable run state.

## Step Lifecycle

1. Resolve case artifacts.
2. Emit `step.planned` and `step.progress`.
3. Apply skip policy.
4. Emit `step.started`.
5. Execute Python processor or dry-run plan.
6. Emit `step.completed` or `step.failed`.
7. Persist `StepResult` when not in dry-run mode.

## Migration Boundary

The v2 runner is isolated from the legacy shell workflow. It currently executes
Python processors through an explicit executor so long-running GPU/audio work
has a process boundary. The runner, config, progress, notifications, and state
model do not depend on the old orchestration shape and can support native step
implementations as they are ported.
