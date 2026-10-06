"""Coordinate alignment selection, cache reuse, and timeline publication."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scotus_pipeline.io import atomic_write_json, read_json
from scotus_pipeline.processors.contracts import OfficialWordsProcessorInput
from scotus_pipeline.processors.official_words.asr import build_asr_word_alignment
from scotus_pipeline.processors.official_words.blocks import build_block_word_alignment
from scotus_pipeline.processors.official_words.cache import official_alignment_fingerprint
from scotus_pipeline.processors.official_words.timeline import apply_word_alignment


def build_word_alignment(
    timeline: dict[str, Any], audio_path: Path, args: OfficialWordsProcessorInput
) -> dict[str, Any]:
    if args.method == "block":
        return build_block_word_alignment(timeline, audio_path, args)
    return build_asr_word_alignment(timeline, audio_path, args)


def run(args: OfficialWordsProcessorInput) -> tuple[Path, Path]:
    timeline = read_json(args.timeline)
    if not isinstance(timeline, dict):
        raise RuntimeError(f"timeline must be a JSON object: {args.timeline}")
    if not args.audio.exists():
        raise FileNotFoundError(args.audio)
    fingerprint = official_alignment_fingerprint(timeline, args.audio, args)
    alignment = (
        read_json(args.output_alignment) if args.output_alignment.exists() and not args.overwrite else None
    )
    reusable = isinstance(alignment, dict) and alignment.get("source_fingerprint") == fingerprint
    if not reusable:
        alignment = build_word_alignment(timeline, args.audio, args)
        alignment["source_fingerprint"] = fingerprint
        atomic_write_json(args.output_alignment, alignment)
    elif alignment.get("schema_version") != "1":
        atomic_write_json(args.output_alignment, alignment)

    refined = apply_word_alignment(timeline, alignment, args)
    atomic_write_json(args.output_timeline, refined)
    return args.output_alignment, args.output_timeline
