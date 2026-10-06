from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scotus_pipeline.cli import main
from scotus_pipeline.config import PipelineConfig
from scotus_pipeline.models import StepResult, StepStatus
from scotus_pipeline.runner import PipelineRunner
from scotus_pipeline.state import PipelineState
from scotus_pipeline.steps.case_steps import ExtractTranscriptStep, RunAlignmentStep


class RunnerTests(unittest.TestCase):
    def make_runner(self, root: Path, *, dry_run: bool = False) -> PipelineRunner:
        config = PipelineConfig.model_validate(
            {
                "project": {"workspace": str(root)},
                "case": {"docket": "24-624"},
                "notifications": [{"type": "jsonl", "path": "events.jsonl"}],
            }
        )
        return PipelineRunner(config, config_path=root / "case.toml", dry_run=dry_run)

    def test_dry_run_new_case_does_not_execute_or_write(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "not-created"
            runner = self.make_runner(root, dry_run=True)
            with patch.object(ExtractTranscriptStep, "execute", side_effect=AssertionError("executed")):
                self.assertEqual(0, runner.run())
            self.assertFalse(root.exists())

    def test_plan_does_not_create_logs_or_state(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp) / "not-created"
            runner = self.make_runner(root, dry_run=True)
            self.assertTrue(runner.plan())
            self.assertFalse(root.exists())

    def test_disabled_step_does_not_require_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            runner = self.make_runner(Path(tmp), dry_run=True)
            result = ExtractTranscriptStep(enabled=False).run(runner.context)
            self.assertEqual(StepStatus.skipped, result.status)

    def test_explicit_case_directory_survives_disabled_archive(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            case_dir = root / "external-case"
            case_dir.mkdir()
            (case_dir / "metadata.json").write_text('{"docket": "24-624"}')
            (case_dir / "audio.mp3").write_bytes(b"audio")
            (case_dir / "transcript.pdf").write_bytes(b"pdf")
            runner = self.make_runner(root)
            runner.config.case.case_dir = case_dir
            self.assertEqual(0, runner.run(only_step="archive"))
            self.assertEqual(case_dir, runner.context.artifacts.case_dir)

    def test_unknown_step_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            runner = self.make_runner(Path(tmp), dry_run=True)
            with self.assertRaisesRegex(ValueError, "unknown step"):
                runner.run(only_step="typo")
            with self.assertRaisesRegex(ValueError, "only one"):
                runner.run(only_step="archive", from_step="archive")

    def test_missing_inputs_report_failed_step(self) -> None:
        with TemporaryDirectory() as tmp:
            runner = self.make_runner(Path(tmp), dry_run=True)
            result = ExtractTranscriptStep().run(runner.context)
            self.assertEqual(StepStatus.failed, result.status)
            self.assertTrue(result.metadata["error"])

    def test_resume_skip_preserves_successful_hashes(self) -> None:
        state = PipelineState(run_id="test", config_path=Path("case.toml"))
        success = StepResult(
            step_id="archive",
            status=StepStatus.succeeded,
            started_at="now",
            finished_at="now",
            duration_seconds=1,
            input_hashes={"input": "abc"},
            output_hashes={"output": "def"},
        )
        state.record(success)
        state.record(success.model_copy(update={"status": StepStatus.skipped}))
        self.assertTrue(
            state.completed("archive", input_hashes={"input": "abc"}, output_hashes={"output": "def"})
        )

    def test_init_config_is_portable_and_wont_overwrite(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.toml"
            self.assertEqual(0, main(["init-config", str(path)]))
            text = path.read_text()
            self.assertIn('workspace = "."', text)
            self.assertEqual(2, main(["init-config", str(path)]))
            self.assertEqual(text, path.read_text())

    def test_alignment_warning_is_visible_and_strict_mode_fails(self) -> None:
        from types import SimpleNamespace

        from scotus_pipeline.events import EventBus, Severity

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            path.write_text("{}")
            events = []
            bus = EventBus()
            bus.subscribe(events.append)
            context = SimpleNamespace(
                artifacts=SimpleNamespace(chunk_report=path),
                event_bus=bus,
                config=SimpleNamespace(alignment=SimpleNamespace(stop_on_warning=False)),
                read_json=lambda _: {
                    "summary": {
                        "planned_chunks": 2,
                        "completed_chunks": 2,
                        "quality_counts": {"warning": 1, "ok": 1},
                    }
                },
            )
            self.assertTrue(RunAlignmentStep().validate_success(context, {})[0])
            self.assertTrue(any(event.severity == Severity.warning for event in events))
            context.config.alignment.stop_on_warning = True
            self.assertFalse(RunAlignmentStep().validate_success(context, {})[0])
