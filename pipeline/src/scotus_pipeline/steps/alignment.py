"""Adapt alignment processors and validate chunk quality."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scotus_pipeline.artifacts import (
    validate_chunk_report_file,
    validate_official_word_alignment_file,
    validate_timeline_file,
)
from scotus_pipeline.context import PipelineContext
from scotus_pipeline.events import EventType, Severity
from scotus_pipeline.processors import (
    alignment_chunks,
    official_words,
)
from scotus_pipeline.processors.contracts import (
    AlignmentChunksProcessorInput,
    AlignmentChunksProcessorResult,
    OfficialWordsProcessorInput,
    OfficialWordsProcessorResult,
)
from scotus_pipeline.steps.base import PipelineStep


class RunAlignmentStep(PipelineStep):
    step_id = "run_alignment"
    title = "Run chunk alignment"

    def outputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [
            artifacts.chunk_report,
            *sorted((artifacts.chunk_report.parent / "chunks").glob("*.json")),
            *sorted((artifacts.cache_dir / "asr").glob("*.whisperx.json")),
        ]

    def inputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.audio, context.input_timeline(self.step_id), artifacts.speaker_turns]

    def execute(self, context: PipelineContext) -> AlignmentChunksProcessorResult:
        artifacts = context.require_artifacts()
        alignment = context.config.alignment
        report = alignment_chunks.build_report(
            AlignmentChunksProcessorInput(
                progress=self.progress_callback(context),
                case_dir=artifacts.case_dir,
                metadata=artifacts.metadata,
                audio=artifacts.audio,
                timeline=context.input_timeline(self.step_id),
                speaker_turns=artifacts.speaker_turns,
                output_dir=artifacts.merged_alignment.parent,
                cache_dir=artifacts.cache_dir / "asr",
                report=artifacts.chunk_report,
                chunk_seconds=alignment.chunk_seconds,
                max_chunks=alignment.max_chunks,
                turn_padding=3,
                model_size=alignment.model_size,
                max_segments_per_turn=30,
                min_score=0.45,
                max_bad_ratio=alignment.max_bad_ratio,
                stop_on_warning=alignment.stop_on_warning,
                dry_run=False,
                overwrite=alignment.overwrite,
                request_timeout_seconds=alignment.request_timeout_seconds,
                use_asr_worker=True,
            )
        )
        validate_chunk_report_file(report, audio=artifacts.audio)
        return AlignmentChunksProcessorResult(
            report=report,
            outputs=[report],
            metrics={"effective_workers": 1},
        )

    def validate_success(
        self,
        context: PipelineContext,
        metadata: dict[str, Any],
    ) -> tuple[bool, str | None, dict[str, object]]:
        artifacts = context.require_artifacts()
        report_path = artifacts.chunk_report
        if not report_path.exists():
            return False, f"alignment report was not written: {report_path}", {}
        report = context.read_json(report_path)
        summary = report.get("summary", {})
        quality_counts = summary.get("quality_counts", {})
        if not isinstance(quality_counts, dict):
            return False, "alignment report is missing summary.quality_counts", {"report": str(report_path)}
        error_count = int(quality_counts.get("error", 0) or 0)
        planned = int(summary.get("planned_chunks", 0) or 0)
        completed = int(summary.get("completed_chunks", 0) or 0)
        validation_metadata: dict[str, object] = {
            "report": str(report_path),
            "planned_chunks": planned,
            "completed_chunks": completed,
            "quality_counts": quality_counts,
        }
        if planned <= 0:
            return False, "alignment produced no planned chunks", validation_metadata
        if completed < planned:
            return False, f"alignment completed only {completed}/{planned} chunks", validation_metadata
        if error_count:
            validation_metadata["first_error"] = first_chunk_error(report)
            return False, f"alignment failed: {error_count} chunk(s) errored", validation_metadata
        warning_count = int(quality_counts.get("warning", 0) or 0)
        if warning_count:
            message = f"Alignment quality warnings in {warning_count}/{planned} chunks; review timings before publication"
            context.event_bus.emit(
                EventType.step_output, message, severity=Severity.warning, step_id=self.step_id
            )
            if context.config.alignment.stop_on_warning:
                return False, message, validation_metadata
        return True, None, validation_metadata


class AlignOfficialWordsStep(PipelineStep):
    step_id = "align_official_words"
    title = "Align official transcript words"

    def outputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.official_word_alignment, context.stage_timeline(self.step_id)]

    def inputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [
            artifacts.audio,
            context.input_timeline(self.step_id),
            *sorted((artifacts.cache_dir / "asr").glob("*.whisperx.json")),
        ]

    def execute(self, context: PipelineContext) -> OfficialWordsProcessorResult:
        artifacts = context.require_artifacts()
        official = context.config.official_words
        alignment, timeline = official_words.run(
            OfficialWordsProcessorInput(
                progress=self.progress_callback(context),
                timeline=context.input_timeline(self.step_id),
                audio=artifacts.audio,
                output_alignment=artifacts.official_word_alignment,
                output_timeline=context.stage_timeline(self.step_id),
                method=official.method,
                cache_dir=artifacts.cache_dir / "asr",
                min_asr_chunk_seconds=120.0,
                language="en",
                align_model=None,
                device=None,
                block_seconds=180.0,
                overlap_seconds=45.0,
                pad_seconds=12.0,
                min_timed_word_ratio=0.45,
                min_mean_word_score=0.35,
                min_apply_mean_word_score=0.35,
                min_word_seconds=0.08,
                long_interpolation_min_words=12,
                long_interpolation_words_per_second=3.0,
                max_turn_overlap_seconds=1.0,
                max_token_scan=12,
                min_token_match_ratio=0.78,
                interpolate_method="nearest",
                progress_every=official.progress_every,
                max_blocks=None,
                apply_low_quality=official.apply_low_quality,
                overwrite=official.overwrite,
            )
        )
        validate_official_word_alignment_file(alignment, audio=artifacts.audio)
        validate_timeline_file(timeline, audio=artifacts.audio)
        return OfficialWordsProcessorResult(
            official_word_alignment=alignment,
            timeline=timeline,
            outputs=[alignment, timeline],
        )


def first_chunk_error(report: dict[str, Any]) -> str | None:
    for chunk in report.get("chunks", []):
        if not isinstance(chunk, dict) or chunk.get("quality") != "error":
            continue
        stderr = chunk.get("stderr")
        if isinstance(stderr, list) and stderr:
            return "\n".join(str(line) for line in stderr[-8:])
        return f"chunk {chunk.get('index')} returned {chunk.get('returncode')}"
    return None
