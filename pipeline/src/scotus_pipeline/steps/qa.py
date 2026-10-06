"""Apply reviewed timing markers to the timeline."""

from __future__ import annotations

from pathlib import Path

from scotus_pipeline.artifacts import (
    validate_timeline_file,
)
from scotus_pipeline.context import PipelineContext
from scotus_pipeline.processors import (
    qa_markers,
)
from scotus_pipeline.processors.contracts import (
    QaMarkersProcessorInput,
    QaMarkersProcessorResult,
)
from scotus_pipeline.steps.base import PipelineStep


class ApplyQaMarkersStep(PipelineStep):
    step_id = "apply_qa_markers"
    title = "Apply QA markers"

    def report_path(self, context: PipelineContext) -> Path:
        artifacts = context.require_artifacts()
        return artifacts.work_dir / "qa" / "qa-marker-apply-report.json"

    def outputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.timeline, self.report_path(context)]

    def inputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.timeline, *[context.resolve(path) for path in context.config.qa.markers]]

    def execute(self, context: PipelineContext) -> QaMarkersProcessorResult:
        artifacts = context.require_artifacts()
        markers = context.config.qa.markers
        if not markers:
            raise RuntimeError("QA marker step enabled without qa.markers")
        marker = context.resolve(markers[0])
        report = self.report_path(context)
        output = qa_markers.apply_markers(
            QaMarkersProcessorInput(
                progress=self.progress_callback(context),
                timeline=artifacts.timeline,
                markers=marker,
                output=artifacts.timeline,
                report=report,
                default_words_per_second=2.8,
                min_segment_duration=0.2,
                enforce_order=context.config.qa.enforce_order,
                min_order_gap=context.config.qa.min_order_gap,
            )
        )
        validate_timeline_file(output, audio=artifacts.audio)
        return QaMarkersProcessorResult(
            timeline=output,
            report=report,
            outputs=[output, report],
            metrics={"markers": str(marker)},
        )
