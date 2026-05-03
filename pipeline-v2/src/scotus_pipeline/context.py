from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from scotus_pipeline.config import PipelineConfig
from scotus_pipeline.events import EventBus
from scotus_pipeline.models import CaseArtifacts
from scotus_pipeline.state import PipelineState


class PipelineContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    config: PipelineConfig
    workspace: Path
    config_path: Path
    event_bus: EventBus
    state: PipelineState
    dry_run: bool = False
    artifacts: CaseArtifacts | None = None
    python_executable: Path = Field(default_factory=lambda: Path(sys.executable))

    def resolve(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        return (self.workspace / path).resolve()

    def read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def processor_command(self, script: str) -> list[str]:
        runtime = self.config.runtime
        if runtime.processor == "uv":
            command = [runtime.uv_executable, "run"]
            for group in runtime.uv_groups:
                command.extend(["--group", group])
            command.extend(["python", script])
            return command
        return [str(self.python_executable), script]


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
    stem = audio.stem
    paths = context.config.paths
    return CaseArtifacts(
        case_dir=case_dir,
        metadata=metadata,
        audio=audio,
        transcript_pdf=transcript_pdf,
        speaker_turns=(
            context.resolve(paths.transcript_output_dir) / f"{transcript_pdf.stem}.speaker-turns.json"
        ),
        timeline=context.resolve(paths.timeline_output_dir) / f"{stem}.case-timeline.json",
        chunk_report=context.resolve(paths.alignment_output_dir) / "chunks" / f"{stem}.chunk-report.json",
        merged_alignment=context.resolve(paths.alignment_output_dir) / f"{stem}.merged-chunks.json",
        official_word_alignment=context.resolve(paths.alignment_output_dir)
        / f"{stem}.official-asr-word-alignment.json",
        profiles=context.resolve(paths.profiles_output_dir) / f"{stem}.speaker-profiles.json",
        viewer=context.resolve(paths.viewer_output_dir) / f"{stem}.html",
    )


def find_case_by_docket(context: PipelineContext) -> CaseArtifacts:
    docket = normalize_docket(context.config.case.docket or "")
    terms = set(context.config.case.term)
    archive_dir = context.resolve(context.config.paths.archive_dir)
    candidates: list[tuple[str, int, Path]] = []
    for metadata_path in sorted(archive_dir.glob("*/**/metadata.json")):
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
