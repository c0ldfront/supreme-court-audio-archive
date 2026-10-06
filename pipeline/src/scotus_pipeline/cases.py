"""Discover archived cases and map them to pipeline artifact paths."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from scotus_pipeline.context import PipelineContext
from scotus_pipeline.models import CaseArtifacts


def build_artifacts_from_case_dir(context: PipelineContext, case_dir: Path) -> CaseArtifacts:
    case_dir = context.resolve(case_dir)
    metadata_path = case_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError(metadata_path)

    metadata = context.read_json(metadata_path)
    audio = _resolve_audio(metadata, case_dir)
    transcript_pdf = _resolve_transcript(metadata, case_dir)
    return build_artifacts(context, case_dir, metadata_path, audio, transcript_pdf)


def build_artifacts(
    context: PipelineContext,
    case_dir: Path,
    metadata: Path,
    audio: Path,
    transcript_pdf: Path,
) -> CaseArtifacts:
    metadata_payload = context.read_json(metadata)
    case_number = case_output_key(metadata_payload, context.config.case.docket, case_dir)
    output_dir = context.resolve(context.config.paths.output_dir) / case_number
    source_dir = context.resolve(context.config.paths.sources_dir) / case_number
    work_dir = context.resolve(context.config.paths.work_dir) / case_number
    cache_dir = context.resolve(context.config.paths.cache_dir) / case_number
    run_dir = context.resolve(context.config.paths.runs_dir) / case_number
    return CaseArtifacts(
        output_dir=output_dir,
        source_dir=source_dir,
        work_dir=work_dir,
        cache_dir=cache_dir,
        run_dir=run_dir,
        snapshot_dir=work_dir / "timeline" / "snapshots",
        case_dir=case_dir,
        metadata=metadata,
        audio=audio,
        transcript_pdf=transcript_pdf,
        transcript_text=work_dir / "transcript" / "transcript.txt",
        speaker_turns=work_dir / "transcript" / "speaker-turns.json",
        timeline=work_dir / "timeline" / "timeline.json",
        chunk_report=work_dir / "alignment" / "chunk-report.json",
        merged_alignment=work_dir / "alignment" / "merged.json",
        official_word_alignment=work_dir / "alignment" / "official-words.json",
        profiles=work_dir / "profiles" / "profiles.json",
        viewer=output_dir / "bundle.json",
        public_audio=output_dir / f"audio{audio.suffix.lower() or '.mp3'}",
    )


def find_case_by_docket(context: PipelineContext) -> CaseArtifacts:
    docket = normalize_docket(context.config.case.docket or "")
    terms = set(context.config.case.term)
    sources_dir = context.resolve(context.config.paths.sources_dir)
    candidates: list[tuple[str, int, Path]] = []
    for metadata_path in sorted(sources_dir.glob("*/metadata.json")):
        try:
            metadata = context.read_json(metadata_path)
        except (OSError, json.JSONDecodeError):
            continue
        if normalize_docket(str(metadata.get("docket", ""))) != docket:
            continue
        term = int(metadata.get("term", 0) or 0)
        if terms and term not in terms:
            continue
        date = str(metadata.get("date_argued_iso") or metadata.get("date_argued") or "")
        candidates.append((date, term, metadata_path))

    if not candidates:
        raise FileNotFoundError(f"archived case not found for docket {context.config.case.docket}")

    _, _, metadata_path = sorted(candidates, reverse=True)[0]
    metadata = context.read_json(metadata_path)
    case_dir = metadata_path.parent
    return build_artifacts(
        context,
        case_dir,
        metadata_path,
        _resolve_audio(metadata, case_dir),
        _resolve_transcript(metadata, case_dir),
    )


def normalize_docket(value: str) -> str:
    return value.strip().casefold().replace(" ", "")


def case_output_key(metadata: dict[str, Any], configured_docket: str | None, case_dir: Path) -> str:
    raw = str(metadata.get("docket") or configured_docket or case_dir.name)
    key = re.sub(r"[^A-Za-z0-9._-]+", "-", raw.strip())
    return key.strip("-") or "case"


def _resolve_audio(metadata: dict[str, Any], case_dir: Path) -> Path:
    audio_file = metadata.get("audio_file")
    if isinstance(audio_file, dict) and audio_file.get("path"):
        path = Path(str(audio_file["path"]))
        if path.exists():
            return path.resolve()
    matches = sorted(case_dir.glob("*.mp3"))
    if not matches:
        raise FileNotFoundError(f"no MP3 found in {case_dir}")
    return matches[0].resolve()


def _resolve_transcript(metadata: dict[str, Any], case_dir: Path) -> Path:
    transcript_file = metadata.get("transcript_file")
    if isinstance(transcript_file, dict) and transcript_file.get("path"):
        path = Path(str(transcript_file["path"]))
        if path.exists():
            return path.resolve()
    matches = sorted(case_dir.glob("*.pdf"))
    if not matches:
        raise FileNotFoundError(f"no transcript PDF found in {case_dir}")
    return matches[0].resolve()
