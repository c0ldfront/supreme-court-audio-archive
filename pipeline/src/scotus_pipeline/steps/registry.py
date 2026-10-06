"""Define pipeline order, configuration gates, and CLI step selection."""

from __future__ import annotations

from scotus_pipeline.config import PipelineConfig
from scotus_pipeline.steps.alignment import AlignOfficialWordsStep, RunAlignmentStep
from scotus_pipeline.steps.archive import ArchiveStep
from scotus_pipeline.steps.base import PipelineStep
from scotus_pipeline.steps.publishing import BuildProfilesStep, BuildViewerStep
from scotus_pipeline.steps.qa import ApplyQaMarkersStep
from scotus_pipeline.steps.timeline import BuildTimelineStep, MergeAlignmentStep, RefineSpeechGapsStep
from scotus_pipeline.steps.transcript import ExtractTranscriptStep


def build_steps(config: PipelineConfig) -> list[PipelineStep]:
    steps = config.steps
    alignment = config.alignment.enabled
    return [
        ArchiveStep(enabled=steps.archive and not config.case.case_dir),
        ExtractTranscriptStep(enabled=steps.extract_transcript),
        BuildTimelineStep(enabled=steps.build_timeline),
        RunAlignmentStep(enabled=steps.run_alignment and alignment),
        MergeAlignmentStep(enabled=steps.merge_alignment and alignment),
        AlignOfficialWordsStep(enabled=steps.align_official_words and alignment),
        RefineSpeechGapsStep(enabled=steps.refine_speech_gaps and alignment),
        ApplyQaMarkersStep(enabled=steps.apply_qa_markers and config.qa.enabled and bool(config.qa.markers)),
        BuildProfilesStep(enabled=steps.build_profiles),
        BuildViewerStep(enabled=steps.build_viewer),
    ]


def select_steps(
    steps: list[PipelineStep], *, only_step: str | None = None, from_step: str | None = None
) -> list[PipelineStep]:
    if only_step and from_step:
        raise ValueError("use only one of --only-step and --from-step")
    selected_id = only_step or from_step
    if selected_id is None:
        return steps
    for index, step in enumerate(steps):
        if step.step_id == selected_id:
            return [step] if only_step else steps[index:]
    raise ValueError(f"unknown step: {selected_id}")
