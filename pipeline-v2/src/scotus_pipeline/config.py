from __future__ import annotations

import tomllib
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class ProjectConfig(StrictModel):
    name: str = "scotus-case"
    workspace: Path = Path("..")


class CaseConfig(StrictModel):
    docket: str | None = None
    term: list[int] = Field(default_factory=list)
    case_dir: Path | None = None

    @model_validator(mode="after")
    def require_case_selector(self) -> CaseConfig:
        if not self.docket and not self.case_dir:
            raise ValueError("case.docket or case.case_dir is required")
        return self


class PathsConfig(StrictModel):
    courts_dir: Path = Path(".courts")
    output_dir: Path = Path("output")

    @property
    def sources_dir(self) -> Path:
        return self.courts_dir / "sources"

    @property
    def source_index_dir(self) -> Path:
        return self.sources_dir / "_index"

    @property
    def cache_dir(self) -> Path:
        return self.courts_dir / "cache"

    @property
    def work_dir(self) -> Path:
        return self.courts_dir / "work"

    @property
    def runs_dir(self) -> Path:
        return self.courts_dir / "runs"


class StepsConfig(StrictModel):
    archive: bool = True
    extract_transcript: bool = True
    build_timeline: bool = True
    run_alignment: bool = True
    merge_alignment: bool = True
    align_official_words: bool = True
    refine_speech_gaps: bool = True
    build_profiles: bool = True
    build_viewer: bool = True
    apply_qa_markers: bool = False


class ArchiveConfig(StrictModel):
    include_transcripts: bool = True
    overwrite: bool = False
    rate_limit_seconds: float = Field(default=0.5, ge=0)
    retries: PositiveInt = 3
    timeout_seconds: PositiveInt = 60


class AlignmentConfig(StrictModel):
    enabled: bool = True
    chunk_seconds: float = Field(default=300.0, gt=0)
    max_chunks: int | None = Field(default=None, ge=1)
    workers: PositiveInt = 1
    model_size: str = "tiny.en"
    max_bad_ratio: float = Field(default=0.5, ge=0, le=1)
    stop_on_warning: bool = False
    overwrite: bool = False


class OfficialWordsConfig(StrictModel):
    method: Literal["asr", "block"] = "asr"
    overwrite: bool = False
    apply_low_quality: bool = False
    progress_every: PositiveInt = 5


class QaConfig(StrictModel):
    enabled: bool = False
    markers: list[Path] = Field(default_factory=list)
    enforce_order: bool = False
    min_order_gap: float = Field(default=0.0, ge=0)


class RuntimeConfig(StrictModel):
    resume: bool = True
    max_retries: int = Field(default=0, ge=0)
    quiet: bool = False


class NotificationType(StrEnum):
    console = "console"
    jsonl = "jsonl"
    webhook = "webhook"


class NotificationLevel(StrEnum):
    debug = "debug"
    info = "info"
    warning = "warning"
    error = "error"


class NotificationConfig(StrictModel):
    type: NotificationType
    level: NotificationLevel = NotificationLevel.info
    path: Path | None = None
    url: str | None = None
    timeout_seconds: PositiveInt = 10

    @model_validator(mode="after")
    def validate_target(self) -> NotificationConfig:
        if self.type == NotificationType.jsonl and not self.path:
            raise ValueError("jsonl notifications require path")
        if self.type == NotificationType.webhook and not self.url:
            raise ValueError("webhook notifications require url")
        return self


class PipelineConfig(StrictModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    case: CaseConfig
    paths: PathsConfig = Field(default_factory=PathsConfig)
    steps: StepsConfig = Field(default_factory=StepsConfig)
    archive: ArchiveConfig = Field(default_factory=ArchiveConfig)
    alignment: AlignmentConfig = Field(default_factory=AlignmentConfig)
    official_words: OfficialWordsConfig = Field(default_factory=OfficialWordsConfig)
    qa: QaConfig = Field(default_factory=QaConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    notifications: list[NotificationConfig] = Field(
        default_factory=lambda: [NotificationConfig(type=NotificationType.console)]
    )

    @field_validator("notifications", mode="before")
    @classmethod
    def normalize_notifications(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        return value

    def with_overrides(
        self,
        *,
        case_dir: Path | None = None,
        docket: str | None = None,
        resume: bool | None = None,
        dry_run_quiet: bool | None = None,
    ) -> PipelineConfig:
        data = self.model_dump()
        if case_dir:
            data["case"]["case_dir"] = case_dir
            data["steps"]["archive"] = False
        if docket:
            data["case"]["docket"] = docket
        if resume is not None:
            data["runtime"]["resume"] = resume
        if dry_run_quiet is not None:
            data["runtime"]["quiet"] = dry_run_quiet
        return PipelineConfig.model_validate(data)


def load_config(path: Path) -> PipelineConfig:
    raw = load_mapping(path)
    return PipelineConfig.model_validate(raw)


def load_mapping(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    with path.open("rb") as handle:
        if suffix == ".toml":
            return tomllib.load(handle)
        if suffix in {".yaml", ".yml"}:
            loaded = yaml.safe_load(handle) or {}
            if not isinstance(loaded, dict):
                raise ValueError(f"{path} must contain a mapping at the top level")
            return loaded
    raise ValueError(f"unsupported config format: {path.suffix}; use .toml, .yaml, or .yml")
