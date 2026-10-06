from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

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

    def resolve(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        return (self.workspace / path).resolve()

    def read_json(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def require_artifacts(self) -> CaseArtifacts:
        if self.artifacts is None:
            raise RuntimeError("Case artifacts are unavailable; archive or select a source directory first")
        return self.artifacts

    def stage_timeline(self, step_id: str) -> Path:
        return self.require_artifacts().work_dir / "timeline" / f"{step_id}.json"

    def input_timeline(self, step_id: str) -> Path:
        from scotus_pipeline.steps.registry import build_steps

        previous = self.require_artifacts().timeline
        for step in build_steps(self.config):
            if step.step_id == step_id:
                return previous
            if step.enabled and step.step_id in {
                "build_timeline",
                "merge_alignment",
                "align_official_words",
                "refine_speech_gaps",
                "apply_qa_markers",
            }:
                previous = self.stage_timeline(step.step_id)
        raise ValueError(f"Unknown step: {step_id}")
