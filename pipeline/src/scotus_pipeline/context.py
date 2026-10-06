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
