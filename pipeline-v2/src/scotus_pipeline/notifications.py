from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, TaskID, TaskProgressColumn, TextColumn, TimeElapsedColumn

from scotus_pipeline.config import NotificationConfig, NotificationLevel, NotificationType
from scotus_pipeline.events import EventType, PipelineEvent, Severity

LEVELS = {
    NotificationLevel.debug: Severity.debug,
    NotificationLevel.info: Severity.info,
    NotificationLevel.warning: Severity.warning,
    NotificationLevel.error: Severity.error,
}


class Notifier(ABC):
    def __init__(self, min_level: Severity = Severity.info) -> None:
        self.min_level = min_level

    def notify(self, event: PipelineEvent) -> None:
        if event.severity >= self.min_level:
            self._notify(event)

    @abstractmethod
    def _notify(self, event: PipelineEvent) -> None:
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    def __init__(self, min_level: Severity = Severity.info) -> None:
        super().__init__(min_level)
        self.console = Console()
        self._progress: Progress | None = None
        self._task_id: TaskID | None = None

    def _notify(self, event: PipelineEvent) -> None:
        if event.type == EventType.pipeline_started:
            self._start_progress(event)
            return
        if event.type == EventType.step_progress:
            self._update_progress(event)
            return
        if event.type == EventType.pipeline_completed:
            self._finish_progress(event, failed=False)
            return
        if event.type == EventType.pipeline_failed:
            self._finish_progress(event, failed=True)
            return
        if event.type in {
            EventType.step_planned,
            EventType.step_started,
            EventType.step_completed,
            EventType.step_skipped,
        }:
            return

        style = {
            Severity.debug: "dim",
            Severity.info: "cyan",
            Severity.warning: "yellow",
            Severity.error: "bold red",
        }.get(event.severity, "white")
        prefix = event.type.value
        if event.step_id:
            prefix = f"{prefix}:{event.step_id}"
        self.console.print(f"[{style}]{prefix}[/{style}] {event.message}")

    def _start_progress(self, event: PipelineEvent) -> None:
        if self._progress:
            self._progress.stop()
        self._progress = Progress(
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=None),
            TaskProgressColumn(),
            TextColumn("[dim]{task.fields[status]}"),
            TimeElapsedColumn(),
            console=self.console,
            transient=False,
        )
        self._progress.start()
        total_steps = event.data.get("total_steps", 0)
        status = f"0/{total_steps} steps"
        self._task_id = self._progress.add_task("Pipeline", total=100, completed=0, status=status)

    def _update_progress(self, event: PipelineEvent) -> None:
        if not self._progress or self._task_id is None:
            self._start_progress(
                PipelineEvent(
                    type=EventType.pipeline_started,
                    message="Pipeline started",
                    data={"total_steps": event.data.get("total", 0)},
                )
            )
        assert self._progress is not None
        assert self._task_id is not None
        completed = int(event.data.get("completed", 0) or 0)
        total = int(event.data.get("total", 0) or 0)
        percent = float(event.data.get("percent", 0.0) or 0.0)
        current_step = str(event.data.get("current_step") or "")
        status = f"{completed}/{total} steps"
        if current_step and completed < total:
            status = f"{status} - {current_step}"
        self._progress.update(
            self._task_id,
            completed=max(0.0, min(100.0, percent)),
            status=status,
        )

    def _finish_progress(self, event: PipelineEvent, *, failed: bool) -> None:
        if self._progress and self._task_id is not None:
            if failed:
                self._progress.update(self._task_id, status="failed")
            else:
                self._progress.update(self._task_id, completed=100, status="complete")
            self._progress.stop()
            self._progress = None
            self._task_id = None
        style = "bold red" if failed else "green"
        self.console.print(f"[{style}]{event.message}[/{style}]")


class JsonlNotifier(Notifier):
    def __init__(self, path: Path, min_level: Severity = Severity.info) -> None:
        super().__init__(min_level)
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _notify(self, event: PipelineEvent) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")


class WebhookNotifier(Notifier):
    def __init__(self, url: str, timeout_seconds: int, min_level: Severity = Severity.info) -> None:
        super().__init__(min_level)
        self.url = url
        self.timeout_seconds = timeout_seconds

    def _notify(self, event: PipelineEvent) -> None:
        payload = json.dumps(event.model_dump(mode="json")).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=payload,
            headers={"content-type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=self.timeout_seconds).close()
        except urllib.error.URLError:
            # Notification failures must not fail the data pipeline.
            return


def build_notifiers(configs: list[NotificationConfig]) -> list[Notifier]:
    notifiers: list[Notifier] = []
    for config in configs:
        min_level = LEVELS[config.level]
        if config.type == NotificationType.console:
            notifiers.append(ConsoleNotifier(min_level=min_level))
        elif config.type == NotificationType.jsonl:
            assert config.path is not None
            notifiers.append(JsonlNotifier(config.path, min_level=min_level))
        elif config.type == NotificationType.webhook:
            assert config.url is not None
            notifiers.append(
                WebhookNotifier(config.url, config.timeout_seconds, min_level=min_level)
            )
    return notifiers
