from __future__ import annotations

import datetime as dt
import os
from pathlib import Path
from uuid import uuid4

from scotus_pipeline.config import NotificationConfig, NotificationType, PipelineConfig
from scotus_pipeline.context import (
    PipelineContext,
    build_artifacts_from_case_dir,
    case_output_key,
    find_case_by_docket,
)
from scotus_pipeline.events import EventBus, EventType, Severity
from scotus_pipeline.models import StepStatus
from scotus_pipeline.notifications import build_notifiers
from scotus_pipeline.state import PipelineState, StateStore
from scotus_pipeline.steps.base import PipelineStep
from scotus_pipeline.steps.case_steps import (
    AlignOfficialWordsStep,
    ApplyQaMarkersStep,
    ArchiveStep,
    BuildProfilesStep,
    BuildTimelineStep,
    BuildViewerStep,
    ExtractTranscriptStep,
    MergeAlignmentStep,
    RefineSpeechGapsStep,
    RunAlignmentStep,
)


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
        steps = self.config.steps
        qa_enabled = self.config.qa.enabled and bool(self.config.qa.markers)
        return [
            ArchiveStep(enabled=steps.archive and not self.config.case.case_dir),
            ExtractTranscriptStep(enabled=steps.extract_transcript),
            BuildTimelineStep(enabled=steps.build_timeline),
            RunAlignmentStep(enabled=steps.run_alignment and self.config.alignment.enabled),
            MergeAlignmentStep(enabled=steps.merge_alignment and self.config.alignment.enabled),
            AlignOfficialWordsStep(enabled=steps.align_official_words and self.config.alignment.enabled),
            RefineSpeechGapsStep(enabled=steps.refine_speech_gaps and self.config.alignment.enabled),
            BuildProfilesStep(enabled=steps.build_profiles),
            ApplyQaMarkersStep(enabled=steps.apply_qa_markers and qa_enabled),
            BuildViewerStep(enabled=steps.build_viewer),
        ]

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
            steps = self._filter_steps(self.steps(), only_step=only_step, from_step=from_step)
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
                self.event_bus.emit(
                    EventType.step_progress,
                    f"{index - 1}/{len(steps)} {step.title}",
                    step_id=step.step_id,
                    data={
                        "completed": index - 1,
                        "total": len(steps),
                        "percent": ((index - 1) / max(1, len(steps))) * 100,
                        "current_step": step.title,
                    },
                )
                result = step.run(self.context)
                if result.status == StepStatus.failed:
                    if not self.context.dry_run:
                        self.state.record(result)
                        self.state_store.save(self.state)
                    self.event_bus.emit(
                        EventType.pipeline_failed,
                        f"Pipeline failed at {step.step_id}",
                        severity=Severity.error,
                    )
                    return 1
                if step.step_id == "archive" and step.enabled and not self.context.dry_run:
                    try:
                        self.context.artifacts = find_case_by_docket(self.context)
                    except FileNotFoundError as exc:
                        failed_result = result.model_copy(
                            update={
                                "status": StepStatus.failed,
                                "metadata": {
                                    **result.metadata,
                                    "error": str(exc),
                                    "hint": (
                                        "Archive completed, but no case metadata matched the configured "
                                        "docket/term filters. Verify case.docket and case.term."
                                    ),
                                },
                            }
                        )
                        self.state.record(failed_result)
                        self.state_store.save(self.state)
                        self.event_bus.emit(
                            EventType.step_failed,
                            str(exc),
                            severity=Severity.error,
                            step_id=step.step_id,
                        )
                        self.event_bus.emit(
                            EventType.pipeline_failed,
                            str(exc),
                            severity=Severity.error,
                        )
                        return 1
                if not self.context.dry_run:
                    self.state.record(result)
                    self.state_store.save(self.state)
                self.event_bus.emit(
                    EventType.step_progress,
                    f"{index}/{len(steps)}",
                    step_id=step.step_id,
                    data={
                        "completed": index,
                        "total": len(steps),
                        "percent": (index / max(1, len(steps))) * 100,
                        "current_step": step.title,
                    },
                )
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

    def _dry_run(self, *, only_step: str | None, from_step: str | None) -> int:
        steps = self._filter_steps(self.steps(), only_step=only_step, from_step=from_step)
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

    def _filter_steps(
        self,
        steps: list[PipelineStep],
        *,
        only_step: str | None,
        from_step: str | None,
    ) -> list[PipelineStep]:
        if only_step and from_step:
            raise ValueError("use only one of --only-step and --from-step")
        if only_step:
            selected = [step for step in steps if step.step_id == only_step]
            if not selected:
                raise ValueError(f"unknown step: {only_step}")
            return selected
        if from_step:
            for index, step in enumerate(steps):
                if step.step_id == from_step:
                    return steps[index:]
            raise ValueError(f"unknown step: {from_step}")
        return steps
