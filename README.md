# SCOTUS Transcript Pipeline

Turn U.S. Supreme Court oral arguments into searchable, synchronized audio
transcripts. The Python pipeline downloads official audio and transcript PDFs,
extracts speaker turns, aligns words with WhisperX, and produces a portable
bundle. The Astro viewer adds playback, speaker navigation, transcript search,
word highlighting, and QA markers.

Alignment is experimental. A completed run can contain low-confidence timings;
review the reported quality warnings before sharing a case viewer.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/).
- Node.js 22.12 or later and npm for the viewer.
- `ffmpeg` (including `ffprobe`) and Poppler's `pdftotext` for case processing.
- WhisperX and PyTorch for fresh alignment, installed with the `asr` extra.
  CUDA is used when available; otherwise WhisperX runs on CPU with int8 compute.

On Debian or Ubuntu, system dependencies can be installed with
`sudo apt install ffmpeg poppler-utils`.

## Quick start

From the repository root:

```bash
cd pipeline
uv sync --extra asr
uv run --extra asr scotus-pipeline validate configs/case-24-624.toml
uv run --extra asr scotus-pipeline run configs/case-24-624.toml --dry-run
uv run --extra asr scotus-pipeline run configs/case-24-624.toml
```

The supplied configurations resolve their workspace to the repository root.
A dry run shows enabled steps without downloading data, running models, or
writing artifacts. To inspect the pipeline without installing model dependencies,
use `uv sync` and omit `--extra asr`.

To process another case, copy an example config and change `[case].docket` and
`[case].term`. `scotus-pipeline init-config case.toml` creates a standalone
configuration whose workspace is the directory containing that file.

## Build a case viewer

After processing a case:

```bash
cd viewer
npm ci
npm run build:case -- 24-624
```

The resulting site is `output/24-624/site/`. Serve that directory with any static
HTTP server. For example, from the repository root:

```bash
python -m http.server 8080 --directory output/24-624/site
```

For hosting under a URL prefix, supply it explicitly:

```bash
npm run build:case -- 24-624 --base /court-transcripts/24-624/
```

The build preserves the pipeline's `bundle.json` and `audio.mp3`. No deployment
runs automatically. A plain `npm run build` produces an empty landing page;
case data is not bundled into source control.

## Repository layout

| Directory | Contents |
| --- | --- |
| `pipeline/src/scotus_pipeline/` | CLI, orchestration, processors, and ASR worker |
| `pipeline/configs/` | Example case configurations |
| `pipeline/tests/` | Offline regression tests |
| `viewer/src/` | Astro components and browser code |
| `viewer/scripts/`, `viewer/tests/` | Case packaging and integration tests |
| `docs/` | Architecture, operation, and release notes |

Runtime data is ignored: `.courts/` holds sources, cache, working artifacts,
logs, and state; `output/<docket>/` holds bundles and generated sites.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for checks and contribution conventions.
The tests use temporary synthetic fixtures and do not download court recordings
or speech models. Run the checks locally using the commands in the contribution
guide.

- [Pipeline usage](pipeline/README.md)
- [Viewer usage](viewer/README.md)
- [Architecture](docs/architecture.md)
- [Troubleshooting and quality checks](docs/operations.md)
- [Release review](docs/release.md)
