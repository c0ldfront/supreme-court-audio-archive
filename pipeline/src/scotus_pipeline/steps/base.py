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
from scotus_pipeline.processors.contracts import ProcessorResult


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

    def progress_callback(self, context: PipelineContext):
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

    def should_skip(self, context: PipelineContext) -> tuple[bool, str | None]:
        if not self.enabled:
            return True, "disabled by config"
        if context.config.runtime.resume and context.state.completed(
            self.step_id,
            input_hashes=hash_existing(self.inputs(context), require_all=True),
            output_hashes=hash_existing(self.outputs(context)),
        ):
            return True, "already completed in state"
        return False, None

    def validate_success(
        self,
        context: PipelineContext,
        metadata: dict[str, Any],
    ) -> tuple[bool, str | None, dict[str, object]]:
        return True, None, {}

    def run(self, context: PipelineContext) -> StepResult:
        started_at = dt.datetime.now(dt.UTC).isoformat()
        start = time.monotonic()
        inputs = self.inputs(context)
        outputs = self.outputs(context)
        input_hashes = hash_existing(inputs, require_all=True)
        should_skip, reason = self.should_skip(context)
        processor = self.processor_name()
        if should_skip:
            context.event_bus.emit(
                EventType.step_skipped,
                reason or "skipped",
                step_id=self.step_id,
            )
            return StepResult(
                step_id=self.step_id,
                status=StepStatus.skipped,
                started_at=started_at,
                finished_at=dt.datetime.now(dt.UTC).isoformat(),
                duration_seconds=0.0,
                outputs=outputs,
                input_hashes=input_hashes,
                output_hashes=hash_existing(outputs),
                metadata={"reason": reason, "processor": processor},
            )

        context.event_bus.emit(
            EventType.step_started,
            self.title,
            step_id=self.step_id,
            data={"processor": processor},
        )

        if context.dry_run:
            context.event_bus.emit(
                EventType.step_completed,
                f"{self.title} dry-run planned",
                step_id=self.step_id,
                data={"dry_run": True},
            )
            return StepResult(
                step_id=self.step_id,
                status=StepStatus.succeeded,
                started_at=started_at,
                finished_at=dt.datetime.now(dt.UTC).isoformat(),
                duration_seconds=0.0,
                outputs=outputs,
                input_hashes=input_hashes,
                metadata={"dry_run": True, "processor": processor},
            )

        try:
            processor_result = self.execute(context)
        except Exception as exc:
            duration = time.monotonic() - start
            context.event_bus.emit(
                EventType.step_failed,
                f"{self.title} failed: {exc}",
                severity=Severity.error,
                step_id=self.step_id,
                data={"error": str(exc), "processor": processor},
            )
            return StepResult(
                step_id=self.step_id,
                status=StepStatus.failed,
                started_at=started_at,
                finished_at=dt.datetime.now(dt.UTC).isoformat(),
                duration_seconds=duration,
                outputs=outputs,
                input_hashes=input_hashes,
                output_hashes=hash_existing(outputs),
                metadata={
                    "error": str(exc),
                    "processor": processor,
                    "traceback": traceback.format_exc().splitlines()[-20:],
                },
            )

        duration = time.monotonic() - start
        metadata = {
            "outputs": [str(path) for path in processor_result.outputs],
            "metrics": processor_result.metrics,
            "warnings": processor_result.warnings,
            **processor_result.model_dump(mode="json", exclude={"outputs", "metrics", "warnings"}),
        }
        metadata = {"processor": processor, **metadata}
        output_hashes = hash_existing(outputs)
        valid, validation_error, validation_metadata = self.validate_success(context, metadata)
        if not valid:
            message = validation_error or f"{self.title} failed validation"
            context.event_bus.emit(
                EventType.step_failed,
                message,
                severity=Severity.error,
                step_id=self.step_id,
                data=validation_metadata,
            )
            return StepResult(
                step_id=self.step_id,
                status=StepStatus.failed,
                started_at=started_at,
                finished_at=dt.datetime.now(dt.UTC).isoformat(),
                duration_seconds=duration,
                outputs=outputs,
                input_hashes=input_hashes,
                output_hashes=output_hashes,
                metadata={**metadata, **validation_metadata},
            )

        snapshots = self.write_snapshots(context, outputs)
        context.event_bus.emit(
            EventType.step_completed,
            f"{self.title} completed in {duration:.1f}s",
            step_id=self.step_id,
            data={"duration_seconds": duration, **metadata, **validation_metadata},
        )
        return StepResult(
            step_id=self.step_id,
            status=StepStatus.succeeded,
            started_at=started_at,
            finished_at=dt.datetime.now(dt.UTC).isoformat(),
            duration_seconds=duration,
            outputs=outputs,
            input_hashes=input_hashes,
            output_hashes=output_hashes,
            snapshots=snapshots,
            metadata={**metadata, **validation_metadata},
        )

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
