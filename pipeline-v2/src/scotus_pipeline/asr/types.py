from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    op: Literal["align", "shutdown"] = "align"
    case_dir: Path | None = None
    metadata: Path | None = None
    audio: Path | None = None
    audio_sha256: str | None = None
    speaker_turns: Path | None = None
    output_dir: Path | None = None
    cache_dir: Path | None = None
    output: Path | None = None
    start: float = Field(default=0.0, ge=0)
    duration: float = Field(default=300.0, gt=0)
    turn_start: int = Field(default=0, ge=0)
    turn_count: int | None = Field(default=40, ge=1)
    model_size: str = "tiny.en"
    max_segments_per_turn: int = Field(default=30, ge=1)
    min_score: float = Field(default=0.45, ge=0, le=1)
    overwrite: bool = False
    no_word_alignment: bool = False


class WorkerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    ok: bool
    output: Path | None = None
    error: str | None = None
