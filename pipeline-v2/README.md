# SCOTUS Pipeline v2

This is an isolated rewrite of the case processing pipeline. It does not modify
or replace the current numbered scripts or `run_pipeline.sh`.

The v2 design is configuration-first and typed end to end:

- Pydantic models validate pipeline configuration, case metadata, artifacts, and
  step results.
- Pipeline steps are Python classes with explicit inputs, outputs, and retry
  behavior.
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

The first v2 implementation is a proper application shell around Python step
objects. Each step is isolated, emits structured events, and can be swapped from
the compatibility executor to a native processor without changing the runner,
configuration, progress, notifications, or state model.

The compatibility executor calls Python scripts directly through the active
Python interpreter. It avoids shell orchestration while keeping GPU-heavy and
long-running work process-isolated during the migration.
