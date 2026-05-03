# Current Pipeline Audit

This audit covers the existing workflow in the repository root, especially
`run_pipeline.sh` and `scripts/13_run_case_pipeline.py`.

## What Works

- The pipeline is already mostly Python and has clear numbered stages.
- Each stage can be run independently, which is useful for debugging long audio
  alignment runs.
- Output directories are stable and easy to inspect.
- The current runner prints coarse progress and fails fast on non-zero exits.

## Main Problems

- Configuration is fragmented across shell flags, hard-coded defaults, and
  per-script argparse definitions.
- Pipeline state is implicit. Existing outputs and cache files influence later
  runs, but there is no typed run state or resumability contract.
- Progress reporting is string-based and not machine-readable.
- Notification behavior is limited to terminal output.
- Step contracts are not modeled. Required inputs, expected outputs, retry
  policy, and skip policy are spread across procedural code.
- Error reporting loses structure because subprocess output is streamed as raw
  text without event metadata.
- There is no config validation layer before expensive work starts.

## v2 Direction

The rewrite treats the pipeline as an application:

- Pydantic owns all external configuration and cross-step data contracts.
- A runner coordinates typed `PipelineStep` objects.
- A state store records step results for resumable runs.
- A notification strategy sends the same event stream to console, JSONL, or
  webhook targets.
- TOML/YAML config becomes the source of truth for paths, case selection,
  runtime behavior, step enablement, and alignment options.
