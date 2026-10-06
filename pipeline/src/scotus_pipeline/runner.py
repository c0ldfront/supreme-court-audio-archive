from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from uuid import uuid4

from scotus_pipeline.cases import build_artifacts_from_case_dir, case_output_key, find_case_by_docket
from scotus_pipeline.config import NotificationConfig, NotificationType, PipelineConfig
from scotus_pipeline.context import PipelineContext
from scotus_pipeline.events import EventBus, EventType, Severity
from scotus_pipeline.models import StepResult, StepStatus
from scotus_pipeline.notifications import build_notifiers
from scotus_pipeline.state import PipelineState, StateStore
from scotus_pipeline.steps.base import PipelineStep
from scotus_pipeline.steps.registry import build_steps, select_steps


class PipelineRunner:
    def __init__(self, config: PipelineConfig, *, config_path: Path, dry_run: bool = False) -> None:
        self.config = config
        self.config_path = config_path.resolve()
        self.workspace = self._resolve_workspace(config.project.workspace)
        self.case_key = self._case_key(config)
        self.event_bus = EventBus()
        notification_configs = [
            item
            for item in config.notifications
            if (not dry_run or item.type == NotificationType.console)
            and (not config.runtime.quiet or item.type != NotificationType.console)
        ]
        for notifier in build_notifiers(self._resolve_notification_paths(notification_configs)):
            self.event_bus.subscribe(notifier.notify)
        state_path = self.workspace / config.paths.runs_dir / self.case_key / "state.json"
        self.state_store = StateStore(state_path)
        self.state = (
            PipelineState(run_id=uuid4().hex, config_path=self.config_path)
            if dry_run
            else self.state_store.load_or_create(run_id=uuid4().hex, config_path=self.config_path)
        )
        self.context = PipelineContext(
            config=config,
            workspace=self.workspace,
            config_path=self.config_path,
            event_bus=self.event_bus,
            state=self.state,
            dry_run=dry_run,
        )

    def _resolve_workspace(self, workspace: Path) -> Path:
        if workspace.is_absolute():
            return workspace.resolve()
        return (self.config_path.parent / workspace).resolve()

    def _case_key(self, config: PipelineConfig) -> str:
        return case_output_key({}, config.case.docket, config.case.case_dir or Path("case"))

    def _resolve_notification_paths(self, configs: list[NotificationConfig]) -> list[NotificationConfig]:
        resolved: list[NotificationConfig] = []
        for config in configs:
            if not config.path:
                resolved.append(config)
                continue
            path = Path(str(config.path).replace("{case}", self.case_key))
            if path.is_absolute():
                resolved.append(config.model_copy(update={"path": path.resolve()}))
            else:
                resolved.append(config.model_copy(update={"path": (self.workspace / path).resolve()}))
        return resolved

    def steps(self) -> list[PipelineStep]:
        return build_steps(self.config)

    def plan(self) -> list[dict[str, object]]:
        self._resolve_artifacts_if_possible()
        planned: list[dict[str, object]] = []
        for step in self.steps():
            outputs: list[str]
            try:
                outputs = [str(path) for path in step.outputs(self.context)]
            except Exception:
                outputs = []
            planned.append(
                {
                    "id": step.step_id,
                    "title": step.title,
                    "enabled": step.enabled,
                    "processor": step.processor_name(),
                    "outputs": outputs,
                }
            )
        return planned

    def run(self, *, only_step: str | None = None, from_step: str | None = None) -> int:
        if self.context.dry_run:
            return self._dry_run(only_step=only_step, from_step=from_step)
        previous_cwd = Path.cwd()
        started = dt.datetime.now(dt.UTC)
        try:
            os.chdir(self.workspace)
            steps = select_steps(self.steps(), only_step=only_step, from_step=from_step)
            self._prepare_artifacts_for_run(steps)
            self.event_bus.emit(
                EventType.pipeline_started,
                f"Pipeline started for {self.config.project.name}",
                data={"workspace": str(self.workspace), "total_steps": len(steps)},
            )
            self.event_bus.emit(
                EventType.step_progress,
                "0%",
                data={"completed": 0, "total": len(steps), "percent": 0.0},
            )
            for index, step in enumerate(steps, start=1):
                self.event_bus.emit(
                    EventType.step_planned,
                    f"{index}/{len(steps)} {step.title}",
                    step_id=step.step_id,
                )
                self._emit_progress(index - 1, len(steps), step)
                result = self._run_step(step)
                if result.status == StepStatus.failed:
                    self.event_bus.emit(
                        EventType.pipeline_failed,
                        f"Pipeline failed at {step.step_id}",
                        severity=Severity.error,
                    )
                    return 1
                self._emit_progress(index, len(steps), step)
            elapsed = (dt.datetime.now(dt.UTC) - started).total_seconds()
            self.event_bus.emit(
                EventType.pipeline_completed,
                f"Pipeline completed in {elapsed:.1f}s",
                data={"duration_seconds": elapsed},
            )
            return 0
        except Exception as exc:
            self.event_bus.emit(
                EventType.pipeline_failed,
                str(exc) or type(exc).__name__,
                severity=Severity.error,
            )
            return 1
        finally:
            os.chdir(previous_cwd)

    def _run_step(self, step: PipelineStep) -> StepResult:
        result = step.run(self.context)
        if step.step_id == "archive" and step.enabled and result.status != StepStatus.failed:
            try:
                self.context.artifacts = find_case_by_docket(self.context)
            except FileNotFoundError as exc:
                result = result.model_copy(
                    update={
                        "status": StepStatus.failed,
                        "metadata": {
                            **result.metadata,
                            "error": str(exc),
                            "hint": "Archive completed but no source matched the docket/term; check case configuration",
                        },
                    }
                )
                self.event_bus.emit(
                    EventType.step_failed,
                    str(exc),
                    severity=Severity.error,
                    step_id=step.step_id,
                )
        self.state.record(result)
        self.state_store.save(self.state)
        return result

    def _emit_progress(self, completed: int, total: int, step: PipelineStep) -> None:
        self.event_bus.emit(
            EventType.step_progress,
            f"{completed}/{total}",
            step_id=step.step_id,
            data={
                "completed": completed,
                "total": total,
                "percent": completed / max(1, total) * 100,
                "current_step": step.title,
            },
        )

    def _dry_run(self, *, only_step: str | None, from_step: str | None) -> int:
        steps = select_steps(self.steps(), only_step=only_step, from_step=from_step)
        self.event_bus.emit(EventType.pipeline_started, "Dry run", data={"total_steps": len(steps)})
        for step in steps:
            self.event_bus.emit(
                EventType.step_output,
                f"{'Would run' if step.enabled else 'Disabled'}: {step.title}",
                step_id=step.step_id,
                data={"dry_run": True, "enabled": step.enabled},
            )
        self.event_bus.emit(EventType.pipeline_completed, "Dry run complete; no artifacts written")
        return 0

    def _prepare_artifacts_for_run(self, steps: list[PipelineStep]) -> None:
        if self.config.case.case_dir:
            self.context.artifacts = build_artifacts_from_case_dir(
                self.context,
                self.config.case.case_dir,
            )
            return
        first_step_id = steps[0].step_id if steps else None
        if not self.config.steps.archive or first_step_id != "archive":
            self.context.artifacts = find_case_by_docket(self.context)

    def _resolve_artifacts_if_possible(self) -> None:
        try:
            if self.config.case.case_dir:
                self.context.artifacts = build_artifacts_from_case_dir(
                    self.context,
                    self.config.case.case_dir,
                )
            else:
                self.context.artifacts = find_case_by_docket(self.context)
        except Exception:
            return
