from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scotus_pipeline.config import PipelineConfig
from scotus_pipeline.context import PipelineContext
from scotus_pipeline.events import EventBus, EventType
from scotus_pipeline.io import hash_existing
from scotus_pipeline.models import StepStatus
from scotus_pipeline.processors.contracts import ProcessorResult
from scotus_pipeline.state import PipelineState
from scotus_pipeline.steps.base import PipelineStep


class FileStep(PipelineStep):
    step_id = "file"
    title = "Process file"

    def inputs(self, context: PipelineContext) -> list[Path]:
        return [context.workspace / "input.txt"]

    def outputs(self, context: PipelineContext) -> list[Path]:
        return [context.workspace / "output.txt"]

    def execute(self, context: PipelineContext) -> ProcessorResult:
        self.outputs(context)[0].write_text("processed")
        return ProcessorResult(outputs=self.outputs(context), metrics={"count": 1})


class StepLifecycleTests(unittest.TestCase):
    def context(self, root: Path) -> PipelineContext:
        (root / "input.txt").write_text("input")
        return PipelineContext(
            config=PipelineConfig(case={"docket": "24-624"}),
            workspace=root,
            config_path=root / "case.toml",
            event_bus=EventBus(),
            state=PipelineState(run_id="test", config_path=root / "case.toml"),
        )

    def test_resume_reuses_hashes_and_does_not_execute_again(self) -> None:
        with TemporaryDirectory() as tmp:
            context = self.context(Path(tmp))
            step = FileStep()
            first = step.run(context)
            self.assertEqual(StepStatus.succeeded, first.status)
            context.state.record(first)
            with (
                patch.object(step, "execute", side_effect=AssertionError("executed")),
                patch("scotus_pipeline.steps.base.hash_existing", wraps=hash_existing) as hashing,
            ):
                resumed = step.run(context)
            self.assertEqual(StepStatus.skipped, resumed.status)
            self.assertEqual(first.input_hashes, resumed.input_hashes)
            self.assertEqual(first.output_hashes, resumed.output_hashes)
            self.assertEqual(2, hashing.call_count)

    def test_execution_and_validation_failures_have_one_failure_event(self) -> None:
        for failure in ("execute", "validate_success"):
            with self.subTest(failure=failure), TemporaryDirectory() as tmp:
                context = self.context(Path(tmp))
                events = []
                context.event_bus.subscribe(events.append)
                step = FileStep()
                kwargs = (
                    {"side_effect": ValueError("broken processor")}
                    if failure == "execute"
                    else {"return_value": (False, "invalid output", {"count": 0})}
                )
                with patch.object(step, failure, **kwargs):
                    result = step.run(context)
                self.assertEqual(StepStatus.failed, result.status)
                self.assertTrue(result.input_hashes)
                self.assertTrue(result.metadata["error"])
                self.assertIn("processor", result.metadata)
                self.assertGreaterEqual(result.duration_seconds, 0)
                self.assertEqual(1, sum(event.type == EventType.step_failed for event in events))
                self.assertFalse(any(event.type == EventType.step_completed for event in events))
