from __future__ import annotations

import datetime as dt
import time
from abc import ABC, abstractmethod
from pathlib import Path

from scotus_pipeline.context import PipelineContext
from scotus_pipeline.events import EventType, Severity
from scotus_pipeline.executor import CommandResult, PythonProcessExecutor
from scotus_pipeline.models import StepResult, StepStatus


class PipelineStep(ABC):
    step_id: str
    title: str

    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled

    @abstractmethod
    def outputs(self, context: PipelineContext) -> list[Path]:
        raise NotImplementedError

    @abstractmethod
    def command(self, context: PipelineContext) -> list[str]:
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
        result: CommandResult,
    ) -> tuple[bool, str | None, dict[str, object]]:
        return True, None, {}

    def run(self, context: PipelineContext) -> StepResult:
        started_at = dt.datetime.now(dt.UTC).isoformat()
        start = time.monotonic()
        should_skip, reason = self.should_skip(context)
        command = self.command(context)
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
                command=command,
                outputs=self.outputs(context),
                metadata={"reason": reason},
            )

        context.event_bus.emit(
            EventType.step_started,
            self.title,
            step_id=self.step_id,
            data={"command": command},
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
                command=command,
                outputs=self.outputs(context),
                metadata={"dry_run": True},
            )

        executor = PythonProcessExecutor(
            cwd=context.workspace,
            event_bus=context.event_bus,
            quiet=context.config.runtime.quiet,
            emit_output=context.config.runtime.emit_subprocess_output,
        )
        result = executor.run(command, step_id=self.step_id)
        duration = time.monotonic() - start
        if result.return_code != 0:
            context.event_bus.emit(
                EventType.step_failed,
                f"{self.title} failed with exit code {result.return_code}",
                severity=Severity.error,
                step_id=self.step_id,
            )
            return StepResult(
                step_id=self.step_id,
                status=StepStatus.failed,
                started_at=started_at,
                finished_at=dt.datetime.now(dt.UTC).isoformat(),
                duration_seconds=duration,
                command=result.command,
                outputs=self.outputs(context),
                metadata={"return_code": result.return_code},
            )

        valid, validation_error, validation_metadata = self.validate_success(context, result)
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
                command=result.command,
                outputs=self.outputs(context),
                metadata={"return_code": result.return_code, **validation_metadata},
            )

        context.event_bus.emit(
            EventType.step_completed,
            f"{self.title} completed in {duration:.1f}s",
            step_id=self.step_id,
            data={"duration_seconds": duration, **validation_metadata},
        )
        return StepResult(
            step_id=self.step_id,
            status=StepStatus.succeeded,
            started_at=started_at,
            finished_at=dt.datetime.now(dt.UTC).isoformat(),
            duration_seconds=duration,
            command=result.command,
            outputs=self.outputs(context),
            metadata={"return_code": result.return_code, **validation_metadata},
        )
