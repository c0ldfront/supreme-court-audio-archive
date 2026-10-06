from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError
from test_step_lifecycle import FileStep

from scotus_pipeline.cases import build_artifacts_from_case_dir
from scotus_pipeline.config import PipelineConfig
from scotus_pipeline.context import PipelineContext
from scotus_pipeline.events import EventBus
from scotus_pipeline.io import atomic_write_json
from scotus_pipeline.models import StepStatus
from scotus_pipeline.state import PipelineState
from scotus_pipeline.steps.qa import ApplyQaMarkersStep
from scotus_pipeline.steps.registry import build_steps


class ResumeAndQaTests(unittest.TestCase):
    def context(self, root: Path) -> PipelineContext:
        config = PipelineConfig(case={"docket": "24-624"})
        context = PipelineContext(
            config=config,
            workspace=root,
            config_path=root / "case.toml",
            event_bus=EventBus(),
            state=PipelineState(run_id="test", config_path=root / "case.toml"),
        )
        case = root / "source"
        atomic_write_json(case / "metadata.json", {"docket": "24-624"})
        (case / "audio.mp3").write_bytes(b"audio")
        (case / "transcript.pdf").write_bytes(b"pdf")
        context.artifacts = build_artifacts_from_case_dir(context, case)
        return context

    def test_changed_settings_invalidate_step_resume(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = self.context(root)
            (root / "input.txt").write_text("input")
            step = FileStep()
            context.state.record(step.run(context))
            self.assertEqual(StepStatus.skipped, step.run(context).status)
            context.config.alignment.model_size = "base.en"
            self.assertEqual(StepStatus.succeeded, step.run(context).status)

    def test_each_timeline_stage_has_distinct_input_and_output(self) -> None:
        with TemporaryDirectory() as tmp:
            context = self.context(Path(tmp))
            previous = None
            for step in build_steps(context.config):
                if step.step_id in {
                    "build_timeline",
                    "merge_alignment",
                    "align_official_words",
                    "refine_speech_gaps",
                }:
                    output = context.stage_timeline(step.step_id)
                    self.assertNotEqual(output, context.input_timeline(step.step_id))
                    if previous is not None:
                        self.assertEqual(previous, context.input_timeline(step.step_id))
                    previous = output
            self.assertEqual(previous, context.input_timeline("build_profiles"))
            context.config.steps.refine_speech_gaps = False
            self.assertEqual(
                context.stage_timeline("align_official_words"), context.input_timeline("build_profiles")
            )

    def test_qa_applies_all_exports_before_profiles(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = self.context(root)
            context.config.qa.enabled = True
            context.config.steps.apply_qa_markers = True
            context.config.qa.markers = [Path("first.json"), Path("second.json")]
            context.config.steps.build_timeline = False
            context.config.alignment.enabled = False
            timeline = context.require_artifacts().timeline
            atomic_write_json(
                timeline,
                {
                    "case": {"docket": "24-624", "audio_file": str(context.artifacts.audio)},
                    "duration": 30,
                    "segments": [
                        {
                            "turn_index": i,
                            "speaker": "JUSTICE",
                            "text": "The Court requires evidence.",
                            "start": i * 10.0,
                            "end": i * 10.0 + 4,
                        }
                        for i in range(2)
                    ],
                },
            )
            for i, name in enumerate(["first.json", "second.json"]):
                marker = {
                    "turn": {"turn_index": i},
                    "selection": {"word_start_index": 0},
                    "expected_time_seconds": 5 + i * 10,
                }
                atomic_write_json(root / name, {"markers": [marker]} if i == 0 else [marker])
            result = ApplyQaMarkersStep().run(context)
            self.assertEqual(StepStatus.succeeded, result.status, result.metadata)
            output = context.stage_timeline("apply_qa_markers")
            revised = json.loads(output.read_text())
            self.assertEqual([5, 15], [s["start"] for s in revised["segments"]])
            self.assertEqual(2, result.metadata["metrics"]["marker_files"])
            self.assertEqual(output, context.input_timeline("build_profiles"))
            self.assertEqual(output, context.input_timeline("build_viewer"))
            self.assertEqual([0, 10], [s["start"] for s in json.loads(timeline.read_text())["segments"]])

    def test_unsupported_config_options_are_rejected(self) -> None:
        for section, setting in [("runtime", "max_retries"), ("alignment", "workers")]:
            with self.subTest(setting=setting), self.assertRaises(ValidationError):
                PipelineConfig.model_validate({"case": {"docket": "24-624"}, section: {setting: 2}})
