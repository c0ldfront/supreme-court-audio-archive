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
resume skips. Each timeline-producing stage writes its own file under
`.courts/work/<docket>/timeline/`, so downstream changes do not overwrite earlier
stage results. Resume fingerprints include validated processing settings. The
canonical `timeline.json` is refreshed when the final viewer bundle is written.

QA marker files are combined in configured order and calibrated together. Profiles
are built after QA so their totals reflect the published transcript. Marker files
are treated as one anchor set; overlapping anchors participate in the same fit.

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

## Worker deadlines and model cache

A request exceeding `alignment.request_timeout_seconds` fails with a timeout;
the worker is terminated and reaped. Error exits also close its pipes and log
handle. Models are reused for subsequent chunks in the same worker, avoiding
repeated model loading. Raw inference caches validate clip content and model
settings; audio clip caches validate the source recording and window.

Use `scotus-pipeline smoke-asr AUDIO --seconds 15 --timeout 300` for an uncached
inference check. This validates actual transcription and timed words, not the
accuracy of a full case's transcript alignment.
