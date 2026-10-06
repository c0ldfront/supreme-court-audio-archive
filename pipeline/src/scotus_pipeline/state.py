from __future__ import annotations

import datetime as dt
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from scotus_pipeline.io import atomic_write_text
from scotus_pipeline.models import StepResult, StepStatus


class PipelineState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    config_path: Path
    started_at: str = Field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat())
    steps: dict[str, StepResult] = Field(default_factory=dict)

    def record(self, result: StepResult) -> None:
        previous = self.steps.get(result.step_id)
        if result.status == StepStatus.skipped and previous and previous.status == StepStatus.succeeded:
            return
        self.steps[result.step_id] = result
        self.updated_at = dt.datetime.now(dt.UTC).isoformat()

    def completed(
        self,
        step_id: str,
        *,
        input_hashes: dict[str, str] | None = None,
        output_hashes: dict[str, str] | None = None,
    ) -> bool:
        result = self.steps.get(step_id)
        if not result or result.status != StepStatus.succeeded:
            return False
        if input_hashes is not None and result.input_hashes != input_hashes:
            return False
        return not (output_hashes is not None and result.output_hashes != output_hashes)


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_or_create(self, *, run_id: str, config_path: Path) -> PipelineState:
        if self.path.exists():
            return PipelineState.model_validate_json(self.path.read_text(encoding="utf-8"))
        return PipelineState(run_id=run_id, config_path=config_path)

    def save(self, state: PipelineState) -> None:
        atomic_write_text(self.path, state.model_dump_json(indent=2) + "\n")
