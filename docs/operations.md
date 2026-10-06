# Operation and troubleshooting

## Alignment quality

A successful exit means stages completed and artifact checks passed. It does not
certify word timing accuracy. Inspect chunk quality counts and low-confidence
matches before sharing a viewer. `alignment.stop_on_warning = true` makes these
warnings fatal. `alignment.overwrite = true` recomputes alignment; `--no-resume`
alone still permits valid caches.

WhisperX runs in a separate JSONL worker. If it exits unexpectedly, inspect
`.courts/cache/<docket>/asr/asr-worker.stderr.log`. Install the `asr` extra and
check model availability before computing fresh chunks. The processor selects
CUDA with float16 when available, or CPU with int8 otherwise. CPU runs may take
considerably longer.

## State and paths

State and logs are under `.courts/runs/<docket>/`. Successful step hashes survive
resume skips. Shared timeline files are rewritten by later stages, so some
upstream stages may rerun on a subsequent invocation. After changing processing
settings, use `--no-resume`; step hashes do not include the full config.

After moving a checkout, cached artifacts can retain old absolute paths. Source
discovery falls back to the audio and PDF in the current case folder. Reprocess
the case with `--no-resume` if an artifact identity check rejects older data.
Do not edit fingerprints to force cache reuse.

## Useful diagnostics

```bash
scotus-pipeline validate case.toml
scotus-pipeline plan case.toml --json
scotus-pipeline run case.toml --dry-run
```

Plans and dry runs need no downloaded case and create no runtime artifacts.
They do not test model availability or external service reachability.

## Viewer output

Serve `output/<docket>/site/` over HTTP. A blank landing page means a generic
frontend build was served instead of a packaged case. A missing bundle or audio
usually means the site's URL prefix differs from the `--base` used at build time.
Rebuild with the correct prefix; the deployment origin can be set via `ASTRO_SITE`.
