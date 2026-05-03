from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from enum import IntEnum, StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class Severity(IntEnum):
    debug = 10
    info = 20
    warning = 30
    error = 40


class EventType(StrEnum):
    pipeline_started = "pipeline.started"
    pipeline_completed = "pipeline.completed"
    pipeline_failed = "pipeline.failed"
    step_planned = "step.planned"
    step_started = "step.started"
    step_output = "step.output"
    step_progress = "step.progress"
    step_skipped = "step.skipped"
    step_completed = "step.completed"
    step_failed = "step.failed"


class PipelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid4().hex)
    type: EventType
    severity: Severity = Severity.info
    timestamp: dt.datetime = Field(default_factory=lambda: dt.datetime.now(dt.UTC))
    message: str
    step_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


Subscriber = Callable[[PipelineEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.append(subscriber)

    def publish(self, event: PipelineEvent) -> None:
        for subscriber in list(self._subscribers):
            subscriber(event)

    def emit(
        self,
        event_type: EventType,
        message: str,
        *,
        severity: Severity = Severity.info,
        step_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.publish(
            PipelineEvent(
                type=event_type,
                severity=severity,
                message=message,
                step_id=step_id,
                data=data or {},
            )
        )
