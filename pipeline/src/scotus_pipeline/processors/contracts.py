from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


@runtime_checkable
class ProgressCallback(Protocol):
    def __call__(
        self,
        *,
        current: int,
        total: int,
        message: str,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        raise NotImplementedError


class ProcessorContract(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, arbitrary_types_allowed=True)
    progress: ProgressCallback | None = Field(default=None, exclude=True)


class ProcessorResult(ProcessorContract):
    outputs: list[Path] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class ArchiveProcessorInput(ProcessorContract):
    output: Path
    term: list[int] | None = None
    from_term: int | None = None
    to_term: int | None = None
    docket: list[str] | None = None
    query: list[str] | None = None
    list_terms: bool = False
    include_transcripts: bool = True
    dry_run: bool = False
    overwrite: bool = False
    limit: int | None = Field(default=None, ge=1)
    rate_limit: float = Field(default=0.5, ge=0)
    retry_delay: float = Field(default=2.0, ge=0)
    retries: PositiveInt = 3
    timeout: PositiveInt = 60


class BuildTimelineProcessorInput(ProcessorContract):
    case_dir: Path
    metadata: Path | None
    audio: Path
    speaker_turns: Path
    whisperx: Path | None = None
    diarization: Path | None = None
    alignment: Path | None = None
    output: Path


class AlignmentChunksProcessorInput(ProcessorContract):
    case_dir: Path
    metadata: Path | None
    audio: Path
    timeline: Path
    speaker_turns: Path
    output_dir: Path
    cache_dir: Path
    report: Path
    chunk_seconds: float = Field(default=300.0, gt=0)
    max_chunks: int | None = Field(default=None, ge=1)
    turn_padding: int = Field(default=3, ge=0)
    model_size: str = "tiny.en"
    max_segments_per_turn: PositiveInt = 30
    min_score: float = Field(default=0.45, ge=0, le=1)
    max_bad_ratio: float = Field(default=0.5, ge=0, le=1)
    stop_on_warning: bool = False
    dry_run: bool = False
    overwrite: bool = False
    use_asr_worker: bool = True
    request_timeout_seconds: float = Field(default=1800.0, gt=0)


class AlignTranscriptProcessorInput(ProcessorContract):
    case_dir: Path
    metadata: Path | None
    audio: Path
    speaker_turns: Path
    whisperx: Path | None = None
    output_dir: Path
    cache_dir: Path
    output: Path
    start: float = Field(default=0.0, ge=0)
    duration: float = Field(default=300.0, gt=0)
    full: bool = False
    turn_start: int = Field(default=0, ge=0)
    turn_count: int | None = Field(default=40, ge=1)
    model_size: str = "tiny.en"
    max_segments_per_turn: PositiveInt = 30
    min_score: float = Field(default=0.45, ge=0, le=1)
    overwrite: bool = False
    no_word_alignment: bool = False


class MergeAlignmentProcessorInput(ProcessorContract):
    case_dir: Path
    metadata: Path | None
    audio: Path
    speaker_turns: Path
    output_dir: Path
    output: Path
    chunks_dir: Path
    chunk_report: Path
    chunks: list[Path] | None = None
    all_chunks: bool = False
    rebuild_timeline: bool = False
    timeline_output: Path | None = None


class OfficialWordsProcessorInput(ProcessorContract):
    timeline: Path
    audio: Path
    output_alignment: Path
    output_timeline: Path
    method: Literal["asr", "block"] = "asr"
    cache_dir: Path
    min_asr_chunk_seconds: float = Field(default=120.0, gt=0)
    language: str = "en"
    align_model: str | None = None
    device: Literal["cuda", "cpu"] | None = None
    block_seconds: float = Field(default=180.0, gt=0)
    overlap_seconds: float = Field(default=45.0, ge=0)
    pad_seconds: float = Field(default=12.0, ge=0)
    min_timed_word_ratio: float = Field(default=0.45, ge=0, le=1)
    min_mean_word_score: float = Field(default=0.35, ge=0, le=1)
    min_apply_mean_word_score: float = Field(default=0.35, ge=0, le=1)
    min_word_seconds: float = Field(default=0.08, gt=0)
    long_interpolation_min_words: PositiveInt = 12
    long_interpolation_words_per_second: float = Field(default=3.0, gt=0)
    max_turn_overlap_seconds: float = Field(default=1.0, ge=0)
    max_token_scan: PositiveInt = 12
    min_token_match_ratio: float = Field(default=0.78, ge=0, le=1)
    interpolate_method: Literal["nearest", "linear", "ignore"] = "nearest"
    progress_every: PositiveInt = 5
    max_blocks: int | None = Field(default=None, ge=1)
    apply_low_quality: bool = False
    overwrite: bool = False


class SpeechGapsProcessorInput(ProcessorContract):
    timeline: Path
    output: Path
    cache_dir: Path
    whisperx: list[Path] | None = None
    merge_gap_seconds: float = Field(default=0.8, ge=0)
    min_non_speech_seconds: float = Field(default=1.75, ge=0)
    snap_gap_seconds: float = Field(default=1.0, ge=0)
    min_region_seconds: float = Field(default=0.05, ge=0)
    min_segment_seconds: float = Field(default=0.2, ge=0)


class SpeakerProfilesProcessorInput(ProcessorContract):
    timeline: Path
    output: Path


class ViewerProcessorInput(ProcessorContract):
    timeline: Path
    profiles: Path
    output: Path
    public_audio: Path | None = None


class QaMarkersProcessorInput(ProcessorContract):
    timeline: Path
    markers: Path
    output: Path
    report: Path
    default_words_per_second: float = Field(default=2.8, gt=0)
    min_segment_duration: float = Field(default=0.2, gt=0)
    enforce_order: bool = False
    min_order_gap: float = Field(default=0.0, ge=0)


class ArchiveProcessorResult(ProcessorResult):
    archive_dir: Path


class TranscriptProcessorResult(ProcessorResult):
    text: Path
    speaker_turns: Path
    turn_count: int = Field(ge=0)


class TimelineProcessorResult(ProcessorResult):
    timeline: Path


class AlignmentChunksProcessorResult(ProcessorResult):
    report: Path


class MergeAlignmentProcessorResult(ProcessorResult):
    merged_alignment: Path
    timeline: Path


class OfficialWordsProcessorResult(ProcessorResult):
    official_word_alignment: Path
    timeline: Path


class SpeakerProfilesProcessorResult(ProcessorResult):
    profiles: Path


class ViewerBundleProcessorResult(ProcessorResult):
    bundle: Path


class QaMarkersProcessorResult(ProcessorResult):
    timeline: Path
    report: Path
