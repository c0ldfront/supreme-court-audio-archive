from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ArtifactModel(BaseModel):
    model_config = ConfigDict(extra="allow", validate_assignment=True)

    schema_version: Literal["1"] = "1"


class CaseMetadata(BaseModel):
    model_config = ConfigDict(extra="allow")

    docket: str | None = None
    name: str | None = None
    audio_file: str | None = None
    metadata_file: str | None = None

    def assert_audio(self, expected: Path) -> None:
        if not self.audio_file:
            raise ValueError("case.audio_file is missing")
        actual = Path(self.audio_file)
        actual_resolved = actual.resolve() if actual.exists() else actual
        expected_resolved = expected.resolve() if expected.exists() else expected
        if actual_resolved != expected_resolved:
            raise ValueError(f"case.audio_file mismatch: expected {expected_resolved}, got {actual_resolved}")


class TimelineSegment(BaseModel):
    model_config = ConfigDict(extra="allow")

    speaker: str | None = None
    text: str = ""
    turn_index: int | None = None
    start: float | None = None
    end: float | None = None


class TimelineV1(ArtifactModel):
    case: CaseMetadata
    duration: float = Field(ge=0)
    speakers: list[dict[str, Any]] = Field(default_factory=list)
    segments: list[TimelineSegment] = Field(default_factory=list)
    sources: dict[str, Any] = Field(default_factory=dict)
    timing: dict[str, Any] = Field(default_factory=dict)


class ChunkReportV1(ArtifactModel):
    case: CaseMetadata
    sources: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    chunks: list[dict[str, Any]] = Field(default_factory=list)


class ChunkAlignmentV1(ArtifactModel):
    case: CaseMetadata
    window: dict[str, Any]
    turn_window: dict[str, Any]
    sources: dict[str, Any] = Field(default_factory=dict)
    alignment: dict[str, Any] = Field(default_factory=dict)
    aligned_turns: list[dict[str, Any]] = Field(default_factory=list)


class MergedAlignmentV1(ArtifactModel):
    case: CaseMetadata
    sources: dict[str, Any] = Field(default_factory=dict)
    alignment: dict[str, Any] = Field(default_factory=dict)
    aligned_turns: list[dict[str, Any]] = Field(default_factory=list)


class OfficialWordAlignmentV1(ArtifactModel):
    case: CaseMetadata
    sources: dict[str, Any] = Field(default_factory=dict)
    summary: dict[str, Any] = Field(default_factory=dict)
    turns: list[dict[str, Any]] = Field(default_factory=list)


class SpeakerProfilesV1(ArtifactModel):
    case: CaseMetadata
    profiles: list[dict[str, Any]] = Field(default_factory=list)


class ViewerBundleV1(ArtifactModel):
    case: CaseMetadata
    timeline: TimelineV1
    profiles: SpeakerProfilesV1
    audio_src: str
    created_at: str


def validate_timeline_file(path: Path, *, audio: Path | None = None) -> TimelineV1:
    timeline = TimelineV1.model_validate_json(path.read_text(encoding="utf-8"))
    if audio is not None:
        timeline.case.assert_audio(audio)
    return timeline


def validate_chunk_report_file(path: Path, *, audio: Path | None = None) -> ChunkReportV1:
    report = ChunkReportV1.model_validate_json(path.read_text(encoding="utf-8"))
    if audio is not None:
        report.case.assert_audio(audio)
    return report


def validate_merged_alignment_file(path: Path, *, audio: Path | None = None) -> MergedAlignmentV1:
    merged = MergedAlignmentV1.model_validate_json(path.read_text(encoding="utf-8"))
    if audio is not None:
        merged.case.assert_audio(audio)
    return merged


def validate_official_word_alignment_file(
    path: Path, *, audio: Path | None = None
) -> OfficialWordAlignmentV1:
    alignment = OfficialWordAlignmentV1.model_validate_json(path.read_text(encoding="utf-8"))
    if audio is not None:
        alignment.case.assert_audio(audio)
    return alignment


def validate_profiles_file(path: Path, *, audio: Path | None = None) -> SpeakerProfilesV1:
    profiles = SpeakerProfilesV1.model_validate_json(path.read_text(encoding="utf-8"))
    if audio is not None:
        profiles.case.assert_audio(audio)
    return profiles
