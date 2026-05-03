from __future__ import annotations

import datetime as dt
import traceback
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from scotus_pipeline.context import PipelineContext
from scotus_pipeline.events import EventType, Severity
from scotus_pipeline.models import StepResult, StepStatus


class PipelineStep(ABC):
    step_id: str
    title: str

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    @abstractmethod
    def outputs(self, context: PipelineContext) -> list[Path]:
        raise NotImplementedError

    def processor_name(self) -> str:
        return f"{self.__class__.__module__}.{self.__class__.__name__}"

    @abstractmethod
    def execute(self, context: PipelineContext) -> dict[str, Any]:
        raise NotImplementedError

    def should_skip(self, context: PipelineContext) -> tuple[bool, str | None]:
        if not self.enabled:
            return True, "disabled by config"
        if context.config.runtime.resume and context.state.completed(self.step_id):
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
                outputs=self.outputs(context),
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
                outputs=self.outputs(context),
                metadata={"dry_run": True, "processor": processor},
            )

        try:
            metadata = self.execute(context)
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
                outputs=self.outputs(context),
                metadata={
                    "error": str(exc),
                    "processor": processor,
                    "traceback": traceback.format_exc().splitlines()[-20:],
                },
            )

        duration = time.monotonic() - start
        metadata = {"processor": processor, **metadata}
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
                outputs=self.outputs(context),
                metadata={**metadata, **validation_metadata},
            )

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
            outputs=self.outputs(context),
            metadata={**metadata, **validation_metadata},
        )
