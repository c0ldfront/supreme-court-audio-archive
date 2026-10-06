from __future__ import annotations

import datetime as dt
import time
import traceback
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from scotus_pipeline.context import PipelineContext
from scotus_pipeline.events import EventType, Severity
from scotus_pipeline.io import copy_snapshot, hash_existing
from scotus_pipeline.models import StepResult, StepStatus
from scotus_pipeline.processors.contracts import ProcessorResult, ProgressCallback


class PipelineStep(ABC):
    step_id: str
    title: str

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    @abstractmethod
    def outputs(self, context: PipelineContext) -> list[Path]:
        raise NotImplementedError

    def inputs(self, context: PipelineContext) -> list[Path]:
        return []

    def processor_name(self) -> str:
        return f"{self.__class__.__module__}.{self.__class__.__name__}"

    def progress_callback(self, context: PipelineContext) -> ProgressCallback:
        def progress(
            *,
            current: int,
            total: int,
            message: str,
            metrics: dict[str, Any] | None = None,
        ) -> None:
            percent = (current / max(1, total)) * 100
            context.event_bus.emit(
                EventType.step_progress,
                message,
                step_id=self.step_id,
                data={
                    "completed": current,
                    "total": total,
                    "percent": percent,
                    "current_step": self.title,
                    **(metrics or {}),
                },
            )

        return progress

    @abstractmethod
    def execute(self, context: PipelineContext) -> ProcessorResult:
        raise NotImplementedError

    def validate_success(
        self,
        context: PipelineContext,
        metadata: dict[str, Any],
    ) -> tuple[bool, str | None, dict[str, object]]:
        return True, None, {}

    def run(self, context: PipelineContext) -> StepResult:
        started_at = dt.datetime.now(dt.UTC).isoformat()
        started = time.monotonic()
        outputs: list[Path] = []
        input_hashes: dict[str, str] = {}
        output_hashes: dict[str, str] = {}
        processor = self.processor_name()
        metadata: dict[str, Any] = {"processor": processor}

        def finish(status: StepStatus, *, snapshots: dict[str, Path] | None = None) -> StepResult:
            return StepResult(
                step_id=self.step_id,
                status=status,
                started_at=started_at,
                finished_at=dt.datetime.now(dt.UTC).isoformat(),
                duration_seconds=time.monotonic() - started,
                outputs=outputs,
                input_hashes=input_hashes,
                output_hashes=output_hashes,
                snapshots=snapshots or {},
                metadata=metadata,
            )

        def fail(message: str) -> StepResult:
            metadata["error"] = message
            context.event_bus.emit(
                EventType.step_failed,
                message,
                severity=Severity.error,
                step_id=self.step_id,
                data=metadata,
            )
            return finish(StepStatus.failed)

        if not self.enabled:
            metadata["reason"] = "disabled by config"
            context.event_bus.emit(EventType.step_skipped, metadata["reason"], step_id=self.step_id)
            return finish(StepStatus.skipped)

        try:
            outputs = self.outputs(context)
            input_hashes = hash_existing(self.inputs(context), require_all=True)
            output_hashes = hash_existing(outputs)
            if context.config.runtime.resume and context.state.completed(
                self.step_id,
                input_hashes=input_hashes,
                output_hashes=output_hashes,
            ):
                metadata["reason"] = "already completed in state"
                context.event_bus.emit(EventType.step_skipped, metadata["reason"], step_id=self.step_id)
                return finish(StepStatus.skipped)

            context.event_bus.emit(
                EventType.step_started, self.title, step_id=self.step_id, data={"processor": processor}
            )
            if context.dry_run:
                metadata["dry_run"] = True
                context.event_bus.emit(
                    EventType.step_completed,
                    f"{self.title} dry-run planned",
                    step_id=self.step_id,
                    data=metadata,
                )
                return finish(StepStatus.succeeded)

            result = self.execute(context)
            metadata.update(result.model_dump(mode="json"))
            output_hashes = hash_existing(outputs)
            valid, error, validation_metadata = self.validate_success(context, metadata)
            metadata.update(validation_metadata)
            if not valid:
                return fail(error or f"{self.title} failed validation")

            snapshots = self.write_snapshots(context, outputs)
            duration = time.monotonic() - started
            context.event_bus.emit(
                EventType.step_completed,
                f"{self.title} completed in {duration:.1f}s",
                step_id=self.step_id,
                data={"duration_seconds": duration, **metadata},
            )
            return finish(StepStatus.succeeded, snapshots=snapshots)
        except Exception as exc:
            metadata["traceback"] = traceback.format_exc().splitlines()[-20:]
            return fail(str(exc) or type(exc).__name__)

    def write_snapshots(self, context: PipelineContext, outputs: list[Path]) -> dict[str, Path]:
        if context.artifacts is None:
            return {}
        snapshots: dict[str, Path] = {}
        timeline = context.artifacts.timeline
        for output in outputs:
            if output != timeline or not output.exists():
                continue
            snapshot = context.artifacts.snapshot_dir / f"{self.step_id}{output.suffix}"
            copy_snapshot(output, snapshot)
            snapshots[str(output)] = snapshot
        return snapshots
