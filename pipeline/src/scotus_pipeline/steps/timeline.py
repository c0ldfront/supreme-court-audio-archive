"""Build, merge, and refine the argument timeline."""

from __future__ import annotations

from pathlib import Path

from scotus_pipeline.artifacts import (
    validate_merged_alignment_file,
    validate_timeline_file,
)
from scotus_pipeline.context import PipelineContext
from scotus_pipeline.processors import (
    build_timeline,
    merge_alignments,
    speech_gaps,
)
from scotus_pipeline.processors.contracts import (
    BuildTimelineProcessorInput,
    MergeAlignmentProcessorInput,
    MergeAlignmentProcessorResult,
    SpeechGapsProcessorInput,
    TimelineProcessorResult,
)
from scotus_pipeline.steps.base import PipelineStep


class BuildTimelineStep(PipelineStep):
    step_id = "build_timeline"
    title = "Build initial timeline"

    def outputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.timeline]

    def inputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.metadata, artifacts.audio, artifacts.speaker_turns]

    def execute(self, context: PipelineContext) -> TimelineProcessorResult:
        artifacts = context.require_artifacts()
        output = build_timeline.build_timeline(
            BuildTimelineProcessorInput(
                progress=self.progress_callback(context),
                case_dir=artifacts.case_dir,
                metadata=artifacts.metadata,
                audio=artifacts.audio,
                speaker_turns=artifacts.speaker_turns,
                whisperx=None,
                diarization=None,
                alignment=None,
                output=artifacts.timeline,
            )
        )
        validate_timeline_file(output, audio=artifacts.audio)
        return TimelineProcessorResult(timeline=output, outputs=[output])


class MergeAlignmentStep(PipelineStep):
    step_id = "merge_alignment"
    title = "Merge chunks and rebuild timeline"

    def outputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.merged_alignment, artifacts.timeline]

    def inputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.audio, artifacts.speaker_turns, artifacts.chunk_report]

    def execute(self, context: PipelineContext) -> MergeAlignmentProcessorResult:
        artifacts = context.require_artifacts()
        merged = merge_alignments.build_merged_alignment(
            MergeAlignmentProcessorInput(
                progress=self.progress_callback(context),
                case_dir=artifacts.case_dir,
                metadata=artifacts.metadata,
                audio=artifacts.audio,
                speaker_turns=artifacts.speaker_turns,
                output_dir=artifacts.merged_alignment.parent,
                output=artifacts.merged_alignment,
                chunks_dir=artifacts.chunk_report.parent,
                chunk_report=artifacts.chunk_report,
                chunks=None,
                all_chunks=False,
                rebuild_timeline=False,
                timeline_output=None,
            )
        )
        validate_merged_alignment_file(merged, audio=artifacts.audio)
        timeline = build_timeline.build_timeline(
            BuildTimelineProcessorInput(
                progress=self.progress_callback(context),
                case_dir=artifacts.case_dir,
                metadata=artifacts.metadata,
                audio=artifacts.audio,
                speaker_turns=artifacts.speaker_turns,
                whisperx=None,
                diarization=None,
                alignment=merged,
                output=artifacts.timeline,
            )
        )
        validate_timeline_file(timeline, audio=artifacts.audio)
        return MergeAlignmentProcessorResult(
            merged_alignment=merged, timeline=timeline, outputs=[merged, timeline]
        )


class RefineSpeechGapsStep(PipelineStep):
    step_id = "refine_speech_gaps"
    title = "Refine speech and non-speech regions"

    def outputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.timeline]

    def inputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.timeline, artifacts.official_word_alignment]

    def execute(self, context: PipelineContext) -> TimelineProcessorResult:
        artifacts = context.require_artifacts()
        output = speech_gaps.refine_timeline(
            SpeechGapsProcessorInput(
                progress=self.progress_callback(context),
                timeline=artifacts.timeline,
                output=artifacts.timeline,
                cache_dir=artifacts.cache_dir / "asr",
                whisperx=None,
                merge_gap_seconds=0.8,
                min_non_speech_seconds=1.75,
                snap_gap_seconds=1.0,
                min_region_seconds=0.05,
                min_segment_seconds=0.2,
            )
        )
        validate_timeline_file(output, audio=artifacts.audio)
        return TimelineProcessorResult(timeline=output, outputs=[output])
