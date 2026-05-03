from __future__ import annotations

import datetime as dt
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from scotus_pipeline.models import StepResult, StepStatus


class PipelineState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    config_path: Path
    started_at: str = Field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: dt.datetime.now(dt.UTC).isoformat())
    steps: dict[str, StepResult] = Field(default_factory=dict)

    def record(self, result: StepResult) -> None:
        self.steps[result.step_id] = result
        self.updated_at = dt.datetime.now(dt.UTC).isoformat()

    def completed(self, step_id: str) -> bool:
        result = self.steps.get(step_id)
        return bool(result and result.status == StepStatus.succeeded)


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load_or_create(self, *, run_id: str, config_path: Path) -> PipelineState:
        if self.path.exists():
            return PipelineState.model_validate_json(self.path.read_text(encoding="utf-8"))
        return PipelineState(run_id=run_id, config_path=config_path)

    def save(self, state: PipelineState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
