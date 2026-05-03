from __future__ import annotations

from pathlib import Path
from typing import Any

from scotus_pipeline.context import PipelineContext
from scotus_pipeline.executor import CommandResult
from scotus_pipeline.steps.base import PipelineStep


class ArchiveStep(PipelineStep):
    step_id = "archive"
    title = "Archive case audio and transcript"

    def outputs(self, context: PipelineContext) -> list[Path]:
        return [context.resolve(context.config.paths.archive_dir)]

    def command(self, context: PipelineContext) -> list[str]:
        archive = context.config.archive
        command = [
            *context.processor_command("scripts/01_archive_scotus_audio.py"),
            "--output",
            str(context.resolve(context.config.paths.archive_dir)),
            "--retries",
            str(archive.retries),
            "--timeout",
            str(archive.timeout_seconds),
            "--rate-limit",
            str(archive.rate_limit_seconds),
        ]
        if context.config.case.docket:
            command.extend(["--docket", context.config.case.docket])
        for term in context.config.case.term:
            command.extend(["--term", str(term)])
        if archive.include_transcripts:
            command.append("--include-transcripts")
        if archive.overwrite:
            command.append("--overwrite")
        return command


class ExtractTranscriptStep(PipelineStep):
    step_id = "extract_transcript"
    title = "Extract transcript PDF"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.speaker_turns]

    def command(self, context: PipelineContext) -> list[str]:
        assert context.artifacts is not None
        return [
            *context.processor_command("scripts/02_extract_transcript_pdf.py"),
            "--pdf",
            str(context.artifacts.transcript_pdf),
            "--output",
            str(context.resolve(context.config.paths.transcript_output_dir)),
        ]


class BuildTimelineStep(PipelineStep):
    step_id = "build_timeline"
    title = "Build initial timeline"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.timeline]

    def command(self, context: PipelineContext) -> list[str]:
        assert context.artifacts is not None
        return [
            *context.processor_command("scripts/05_build_case_timeline.py"),
            "--case-dir",
            str(context.artifacts.case_dir),
            "--audio",
            str(context.artifacts.audio),
            "--speaker-turns",
            str(context.artifacts.speaker_turns),
            "--output",
            str(context.artifacts.timeline),
        ]


class RunAlignmentStep(PipelineStep):
    step_id = "run_alignment"
    title = "Run chunk alignment"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.chunk_report]

    def command(self, context: PipelineContext) -> list[str]:
        assert context.artifacts is not None
        alignment = context.config.alignment
        command = [
            *context.processor_command("scripts/07_run_alignment_chunks.py"),
            "--case-dir",
            str(context.artifacts.case_dir),
            "--audio",
            str(context.artifacts.audio),
            "--timeline",
            str(context.artifacts.timeline),
            "--output-dir",
            str(context.resolve(context.config.paths.alignment_output_dir)),
            "--report",
            str(context.artifacts.chunk_report),
            "--chunk-seconds",
            str(alignment.chunk_seconds),
            "--workers",
            str(alignment.workers),
            "--model-size",
            alignment.model_size,
            "--max-bad-ratio",
            str(alignment.max_bad_ratio),
        ]
        if alignment.max_chunks:
            command.extend(["--max-chunks", str(alignment.max_chunks)])
        if alignment.stop_on_warning:
            command.append("--stop-on-warning")
        if alignment.overwrite:
            command.append("--overwrite")
        return command

    def validate_success(
        self,
        context: PipelineContext,
        result: CommandResult,
    ) -> tuple[bool, str | None, dict[str, object]]:
        assert context.artifacts is not None
        report_path = context.artifacts.chunk_report
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
        metadata: dict[str, object] = {
            "report": str(report_path),
            "planned_chunks": planned,
            "completed_chunks": completed,
            "quality_counts": quality_counts,
        }
        if planned <= 0:
            return False, "alignment produced no planned chunks", metadata
        if completed < planned:
            return False, f"alignment completed only {completed}/{planned} chunks", metadata
        if error_count:
            metadata["first_error"] = first_chunk_error(report)
            return False, f"alignment failed: {error_count} chunk(s) errored", metadata
        return True, None, metadata


class MergeAlignmentStep(PipelineStep):
    step_id = "merge_alignment"
    title = "Merge chunks and rebuild timeline"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.merged_alignment, context.artifacts.timeline]

    def command(self, context: PipelineContext) -> list[str]:
        assert context.artifacts is not None
        return [
            *context.processor_command("scripts/08_merge_chunk_alignments.py"),
            "--case-dir",
            str(context.artifacts.case_dir),
            "--audio",
            str(context.artifacts.audio),
            "--chunk-report",
            str(context.artifacts.chunk_report),
            "--output",
            str(context.artifacts.merged_alignment),
            "--rebuild-timeline",
            "--timeline-output",
            str(context.artifacts.timeline),
        ]


class AlignOfficialWordsStep(PipelineStep):
    step_id = "align_official_words"
    title = "Align official transcript words"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.official_word_alignment, context.artifacts.timeline]

    def command(self, context: PipelineContext) -> list[str]:
        assert context.artifacts is not None
        official = context.config.official_words
        command = [
            *context.processor_command("scripts/09_force_align_official_transcript_blocks.py"),
            "--timeline",
            str(context.artifacts.timeline),
            "--audio",
            str(context.artifacts.audio),
            "--output-alignment",
            str(context.artifacts.official_word_alignment),
            "--output-timeline",
            str(context.artifacts.timeline),
            "--method",
            official.method,
            "--progress-every",
            str(official.progress_every),
        ]
        if official.overwrite:
            command.append("--overwrite")
        if official.apply_low_quality:
            command.append("--apply-low-quality")
        return command


class RefineSpeechGapsStep(PipelineStep):
    step_id = "refine_speech_gaps"
    title = "Refine speech and non-speech regions"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.timeline]

    def command(self, context: PipelineContext) -> list[str]:
        assert context.artifacts is not None
        return [
            *context.processor_command("scripts/10_refine_timeline_speech_gaps.py"),
            "--timeline",
            str(context.artifacts.timeline),
            "--output",
            str(context.artifacts.timeline),
            "--cache-dir",
            str(context.resolve(context.config.paths.alignment_output_dir) / "cache"),
        ]


class BuildProfilesStep(PipelineStep):
    step_id = "build_profiles"
    title = "Build speaker profiles"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.profiles]

    def command(self, context: PipelineContext) -> list[str]:
        assert context.artifacts is not None
        return [
            *context.processor_command("scripts/11_build_speaker_profiles.py"),
            "--timeline",
            str(context.artifacts.timeline),
            "--output",
            str(context.artifacts.profiles),
        ]


class BuildViewerStep(PipelineStep):
    step_id = "build_viewer"
    title = "Build static viewer"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.viewer]

    def command(self, context: PipelineContext) -> list[str]:
        assert context.artifacts is not None
        return [
            *context.processor_command("scripts/12_build_case_viewer.py"),
            "--timeline",
            str(context.artifacts.timeline),
            "--profiles",
            str(context.artifacts.profiles),
            "--output",
            str(context.artifacts.viewer),
        ]


class ApplyQaMarkersStep(PipelineStep):
    step_id = "apply_qa_markers"
    title = "Apply QA markers"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.timeline, *context.artifacts.qa_reports]

    def command(self, context: PipelineContext) -> list[str]:
        assert context.artifacts is not None
        markers = context.config.qa.markers
        marker = context.resolve(markers[0]) if markers else Path("__missing_marker_export__.json")
        report = (
            context.resolve(context.config.paths.log_dir)
            / f"{context.artifacts.timeline.stem}.qa-marker-apply-report.json"
        )
        command = [
            *context.processor_command("scripts/16_apply_qa_markers_to_timeline.py"),
            "--timeline",
            str(context.artifacts.timeline),
            "--markers",
            str(marker),
            "--output",
            str(context.artifacts.timeline),
            "--report",
            str(report),
            "--min-order-gap",
            str(context.config.qa.min_order_gap),
        ]
        if context.config.qa.enforce_order:
            command.append("--enforce-order")
        return command


def first_chunk_error(report: dict[str, Any]) -> str | None:
    for chunk in report.get("chunks", []):
        if not isinstance(chunk, dict) or chunk.get("quality") != "error":
            continue
        stderr = chunk.get("stderr")
        if isinstance(stderr, list) and stderr:
            return "\n".join(str(line) for line in stderr[-8:])
        return f"chunk {chunk.get('index')} returned {chunk.get('command_returncode')}"
    return None
