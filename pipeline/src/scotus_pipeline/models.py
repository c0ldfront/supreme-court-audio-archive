from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class CaseIdentity(DomainModel):
    docket: str
    name: str | None = None
    term: int | None = None
    date_argued: str | None = None
    detail_id: str | None = None


class CaseArtifacts(DomainModel):
    output_dir: Path
    source_dir: Path
    work_dir: Path
    cache_dir: Path
    run_dir: Path
    snapshot_dir: Path
    case_dir: Path
    metadata: Path
    audio: Path
    transcript_pdf: Path
    transcript_text: Path
    speaker_turns: Path
    timeline: Path
    chunk_report: Path
    merged_alignment: Path
    official_word_alignment: Path
    profiles: Path
    viewer: Path
    public_audio: Path
    qa_reports: list[Path] = Field(default_factory=list)


class StepStatus(StrEnum):
    pending = "pending"
    running = "running"
    skipped = "skipped"
    succeeded = "succeeded"
    failed = "failed"


class StepResult(DomainModel):
    step_id: str
    status: StepStatus
    started_at: str
    finished_at: str
    duration_seconds: float
    command: list[str] = Field(default_factory=list)
    outputs: list[Path] = Field(default_factory=list)
    input_hashes: dict[str, str] = Field(default_factory=dict)
    output_hashes: dict[str, str] = Field(default_factory=dict)
    snapshots: dict[str, Path] = Field(default_factory=dict)
    config_fingerprint: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
