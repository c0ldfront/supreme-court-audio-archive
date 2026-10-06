# Contributing

Keep changes focused and describe the behavior they change. Use commit subjects
such as `fix: preserve resume state after skipped steps` or
`feat: add transcript search` rather than temporary checkpoint labels.

## Checks

From `pipeline/`:

```bash
uv sync
uv run ruff check src tests
uv run ruff format --check src tests
uv run python -m unittest discover -s tests -v
uv build
```

Use `uv sync --extra asr` and `uv run --extra asr ...` when working with local
model dependencies; a plain sync removes extras from that environment.

From `viewer/`:

```bash
npm ci
npm run typecheck
npm test
npm run build
```

Regression tests should use temporary synthetic data and remain independent of
network access, court recordings, GPUs, and model downloads. The frontend
integration test invokes a real Astro build with a minimal generated bundle.

Do not commit recordings, transcripts, generated sites, model caches, local
configuration, run logs, or credentials. Keep user-visible text independent of
local machine names, paths, and historical demo cases.

For a change to audio processing, also run a real case and report whether
alignment results were cached or freshly computed, plus any quality warnings.
