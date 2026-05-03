from __future__ import annotations

import subprocess
from collections.abc import Iterable
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from scotus_pipeline.events import EventBus, EventType, Severity


class CommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: list[str]
    return_code: int
    output_lines: list[str] = Field(default_factory=list)


class PythonProcessExecutor:
    """Run Python processors as isolated child processes.

    GPU-heavy alignment work is safer in a process boundary: imports, CUDA state,
    and model memory are released when the step process exits.
    """

    def __init__(self, *, cwd: Path, event_bus: EventBus, quiet: bool, emit_output: bool) -> None:
        self.cwd = cwd
        self.event_bus = event_bus
        self.quiet = quiet
        self.emit_output = emit_output

    def run(self, command: Iterable[str], *, step_id: str) -> CommandResult:
        command_list = [str(part) for part in command]
        output_lines: list[str] = []
        process = subprocess.Popen(
            command_list,
            cwd=self.cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            clean = line.rstrip()
            output_lines.append(clean)
            if self.emit_output and not self.quiet:
                self.event_bus.emit(
                    EventType.step_output,
                    clean,
                    severity=Severity.debug,
                    step_id=step_id,
                )
        return_code = process.wait()
        return CommandResult(command=command_list, return_code=return_code, output_lines=output_lines)
