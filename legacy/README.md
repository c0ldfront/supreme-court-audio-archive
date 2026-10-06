# Supreme Court Audio Archive

This repo contains scripts for archiving and analyzing U.S. Supreme Court oral
argument audio from the Court's official website.

The Court's public audio index advertises term-year pages beginning with 2010.
Running the script without a `--term` filter archives every advertised term.

## Usage

Create the isolated Python environment:

```bash
uv venv --python /usr/bin/python3.12 .venv
uv sync --group audio
source .venv/bin/activate
```

List available terms:

```bash
uv run python scripts/01_archive_scotus_audio.py --list-terms
```

Preview the latest populated term without writing files:

```bash
uv run python scripts/01_archive_scotus_audio.py --term 2025 --limit 5 --dry-run
```

Archive all advertised Court audio:

```bash
uv run python scripts/01_archive_scotus_audio.py --output archive/scotus-audio
```

Archive one term and include transcript PDFs:

```bash
uv run python scripts/01_archive_scotus_audio.py --term 2025 --include-transcripts
```

Extract transcript PDF text and speaker turns:

```bash
uv run python scripts/02_extract_transcript_pdf.py
```

Build a viewer-ready case timeline:

```bash
uv run python scripts/05_build_case_timeline.py
```

Align a small transcript/audio window with WhisperX timestamps:

```bash
uv run --group audio python scripts/06_align_transcript_to_audio.py --start 0 --duration 300 --turn-count 40
```

Script `06` transcribes each audio window and writes WhisperX word-level
timestamps into `alignment-output/cache/`. It falls back to segment-level timing
only when WhisperX cannot produce word timings.

Run alignment in cacheable chunks and write a quality report:

```bash
uv run --group audio python scripts/07_run_alignment_chunks.py --chunk-seconds 300 --max-chunks 1 --workers 2
```

Stop early when a chunk has too many unmatched turns:

```bash
uv run --group audio python scripts/07_run_alignment_chunks.py --chunk-seconds 300 --stop-on-warning
```

Merge chunk alignments into one timeline-ready alignment file:

```bash
uv run --group audio python scripts/08_merge_chunk_alignments.py
```

Merge chunks and rebuild the viewer timeline from that merged alignment:

```bash
uv run --group audio python scripts/08_merge_chunk_alignments.py --rebuild-timeline
```

Align the official transcript words to the cached WhisperX ASR word-timing spine:

```bash
uv run --group audio python scripts/09_force_align_official_transcript_blocks.py
```

Refine the rebuilt timeline with speech/non-speech gaps from the WhisperX cache:

```bash
uv run --group audio python scripts/10_refine_timeline_speech_gaps.py
```

Build speaker profiles with official Court portrait URLs for the Justices:

```bash
uv run python scripts/11_build_speaker_profiles.py
```

Build a static local HTML viewer:

```bash
uv run python scripts/12_build_case_viewer.py
```

Check known phrase/time markers against the generated timeline:

```bash
uv run python scripts/15_check_alignment_markers.py \
  --marker '0:13|in Allen versus Milligan' \
  --marker '7:13|a suggestion that we not'
```

Run the full case pipeline from archive through viewer:

```bash
./run_pipeline.sh --docket 24-109 --term 2025
```

Run the pipeline against an already archived case and limit alignment work while
testing:

```bash
./run_pipeline.sh --case-dir archive/scotus-audio/2025/24-109_2 --max-chunks 2
```

## Output

Records are written under:

```text
archive/scotus-audio/{term}/{detail_id}/
```

Each record directory contains:

- The MP3 audio file.
- `metadata.json` with docket, case name, argument date, source URLs, file size,
  and SHA-256 checksum.
- The transcript PDF when `--include-transcripts` is used and the Court provides
  one.

The root output directory also contains:

- `manifest.latest.json`, a full manifest for the most recent run.
- `manifest.jsonl`, one JSON object per archived record.

Existing non-empty files are skipped by default. Use `--overwrite` to redownload.

## Local Audio Testing

The `audio` dependency group installs CUDA PyTorch, WhisperX, and pyannote.audio
into the repo-local `.venv`.

Check the GPU and create a short WAV clip from the first archived MP3:

```bash
uv run --group audio python scripts/04_audio_pipeline_smoke.py --clip-seconds 60 --check-gpu --overwrite
```

Run a small WhisperX transcription test:

```bash
uv run --group audio python scripts/04_audio_pipeline_smoke.py --clip-seconds 60 --transcribe --model-size tiny.en
```

Run local pyannote diarization with the default community model:

```bash
uv run --group audio python scripts/04_audio_pipeline_smoke.py --clip-seconds 60 --diarize
```

Check access for the official gated pyannote models:

```bash
uv run --group audio hf auth login
uv run --group audio python scripts/03_check_hf_pyannote_access.py
```

Alternatively, set a token for the current shell:

```bash
export HF_TOKEN=...
uv run --group audio python scripts/03_check_hf_pyannote_access.py
```

The smoke test defaults to `pyannote-community/speaker-diarization-community-1`.
To force the official gated model after access is accepted:

```bash
uv run --group audio python scripts/04_audio_pipeline_smoke.py --clip-seconds 60 --diarize --diarization-model pyannote/speaker-diarization-community-1
```

Smoke-test outputs are written to `audio-test-output/`.

Timeline outputs are written to `timeline-output/`.

Alignment outputs are written to `alignment-output/`.

Chunked alignment outputs and reports are written to `alignment-output/chunks/`.

Merged chunk alignment is written to `alignment-output/{audio-stem}.merged-chunks.json`.

Official transcript word alignment is written to `alignment-output/{audio-stem}.official-asr-word-alignment.json`.
It maps official transcript text onto cached WhisperX word timings where possible
and interpolates only the official words that do not have a reliable ASR match.

Speech/non-speech refinement updates the case timeline and records gap events in `timeline.events`.

Speaker profiles are written to `profiles-output/`.

The static case viewer is written to `viewer-output/`.
