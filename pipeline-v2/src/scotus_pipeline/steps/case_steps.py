from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from scotus_pipeline.context import PipelineContext
from scotus_pipeline.events import EventType, Severity
from scotus_pipeline.processors.contracts import (
    AlignmentChunksProcessorInput,
    ArchiveProcessorInput,
    BuildTimelineProcessorInput,
    MergeAlignmentProcessorInput,
    OfficialWordsProcessorInput,
    QaMarkersProcessorInput,
    SpeakerProfilesProcessorInput,
    SpeechGapsProcessorInput,
    ViewerProcessorInput,
)
from scotus_pipeline.processors._native import (
    alignment_chunks,
    archive_audio,
    build_timeline,
    case_viewer,
    extract_transcript,
    merge_alignments,
    official_words,
    qa_markers,
    speaker_profiles,
    speech_gaps,
)
from scotus_pipeline.steps.base import PipelineStep


class ArchiveStep(PipelineStep):
    step_id = "archive"
    title = "Archive case audio and transcript"

    def outputs(self, context: PipelineContext) -> list[Path]:
        return [context.resolve(context.config.paths.archive_dir)]

    def execute(self, context: PipelineContext) -> dict[str, Any]:
        archive = context.config.archive
        result = archive_audio.archive_records(
            ArchiveProcessorInput(
                output=context.resolve(context.config.paths.archive_dir),
                term=context.config.case.term or None,
                from_term=None,
                to_term=None,
                docket=[context.config.case.docket] if context.config.case.docket else None,
                query=None,
                list_terms=False,
                include_transcripts=archive.include_transcripts,
                dry_run=False,
                overwrite=archive.overwrite,
                limit=None,
                rate_limit=archive.rate_limit_seconds,
                retry_delay=2.0,
                retries=archive.retries,
                timeout=archive.timeout_seconds,
            ).to_namespace()
        )
        if result != 0:
            raise RuntimeError(f"archive processor returned {result}")
        return {"archive_dir": str(context.resolve(context.config.paths.archive_dir))}


class ExtractTranscriptStep(PipelineStep):
    step_id = "extract_transcript"
    title = "Extract transcript PDF"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.speaker_turns]

    def execute(self, context: PipelineContext) -> dict[str, Any]:
        assert context.artifacts is not None
        output_dir = context.resolve(context.config.paths.transcript_output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        text = extract_transcript.extract_pdf_text(context.artifacts.transcript_pdf, layout=True)
        turns = extract_transcript.parse_speaker_turns(text)

        text_path = output_dir / f"{context.artifacts.transcript_pdf.stem}.txt"
        turns_path = context.artifacts.speaker_turns
        text_path.write_text(text, encoding="utf-8")
        turns_path.parent.mkdir(parents=True, exist_ok=True)
        turns_path.write_text(
            json.dumps([asdict(turn) for turn in turns], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {"text": str(text_path), "speaker_turns": str(turns_path), "turn_count": len(turns)}


class BuildTimelineStep(PipelineStep):
    step_id = "build_timeline"
    title = "Build initial timeline"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.timeline]

    def execute(self, context: PipelineContext) -> dict[str, Any]:
        assert context.artifacts is not None
        output = build_timeline.build_timeline(
            BuildTimelineProcessorInput(
                case_dir=context.artifacts.case_dir,
                metadata=context.artifacts.metadata,
                audio=context.artifacts.audio,
                speaker_turns=context.artifacts.speaker_turns,
                whisperx=None,
                diarization=None,
                alignment=None,
                output=context.artifacts.timeline,
            ).to_namespace()
        )
        return {"timeline": str(output)}


class RunAlignmentStep(PipelineStep):
    step_id = "run_alignment"
    title = "Run chunk alignment"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.chunk_report]

    def execute(self, context: PipelineContext) -> dict[str, Any]:
        assert context.artifacts is not None
        alignment = context.config.alignment
        workers = 1
        if alignment.workers != workers:
            context.event_bus.emit(
                EventType.step_output,
                "native WhisperX alignment is serialized to avoid shared GPU/model state",
                severity=Severity.warning,
                step_id=self.step_id,
                data={"requested_workers": alignment.workers, "effective_workers": workers},
            )
        report = alignment_chunks.build_report(
            AlignmentChunksProcessorInput(
                case_dir=context.artifacts.case_dir,
                metadata=context.artifacts.metadata,
                audio=context.artifacts.audio,
                timeline=context.artifacts.timeline,
                speaker_turns=context.artifacts.speaker_turns,
                output_dir=context.resolve(context.config.paths.alignment_output_dir),
                report=context.artifacts.chunk_report,
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
                workers=workers,
            ).to_namespace()
        )
        return {"report": str(report), "requested_workers": alignment.workers, "effective_workers": workers}

    def validate_success(
        self,
        context: PipelineContext,
        metadata: dict[str, Any],
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
        return True, None, validation_metadata


class MergeAlignmentStep(PipelineStep):
    step_id = "merge_alignment"
    title = "Merge chunks and rebuild timeline"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.merged_alignment, context.artifacts.timeline]

    def execute(self, context: PipelineContext) -> dict[str, Any]:
        assert context.artifacts is not None
        merged = merge_alignments.build_merged_alignment(
            MergeAlignmentProcessorInput(
                case_dir=context.artifacts.case_dir,
                metadata=context.artifacts.metadata,
                audio=context.artifacts.audio,
                speaker_turns=context.artifacts.speaker_turns,
                output_dir=context.resolve(context.config.paths.alignment_output_dir),
                output=context.artifacts.merged_alignment,
                chunks_dir=context.resolve(context.config.paths.alignment_output_dir) / "chunks",
                chunk_report=context.artifacts.chunk_report,
                chunks=None,
                all_chunks=False,
                rebuild_timeline=False,
                timeline_output=None,
            ).to_namespace()
        )
        timeline = build_timeline.build_timeline(
            BuildTimelineProcessorInput(
                case_dir=context.artifacts.case_dir,
                metadata=context.artifacts.metadata,
                audio=context.artifacts.audio,
                speaker_turns=context.artifacts.speaker_turns,
                whisperx=None,
                diarization=None,
                alignment=merged,
                output=context.artifacts.timeline,
            ).to_namespace()
        )
        return {"merged_alignment": str(merged), "timeline": str(timeline)}


class AlignOfficialWordsStep(PipelineStep):
    step_id = "align_official_words"
    title = "Align official transcript words"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.official_word_alignment, context.artifacts.timeline]

    def execute(self, context: PipelineContext) -> dict[str, Any]:
        assert context.artifacts is not None
        official = context.config.official_words
        alignment, timeline = official_words.run(
            OfficialWordsProcessorInput(
                timeline=context.artifacts.timeline,
                audio=context.artifacts.audio,
                output_alignment=context.artifacts.official_word_alignment,
                output_timeline=context.artifacts.timeline,
                method=official.method,
                cache_dir=context.resolve(context.config.paths.alignment_output_dir) / "cache",
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
            ).to_namespace()
        )
        return {"official_word_alignment": str(alignment), "timeline": str(timeline)}


class RefineSpeechGapsStep(PipelineStep):
    step_id = "refine_speech_gaps"
    title = "Refine speech and non-speech regions"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.timeline]

    def execute(self, context: PipelineContext) -> dict[str, Any]:
        assert context.artifacts is not None
        output = speech_gaps.refine_timeline(
            SpeechGapsProcessorInput(
                timeline=context.artifacts.timeline,
                output=context.artifacts.timeline,
                cache_dir=context.resolve(context.config.paths.alignment_output_dir) / "cache",
                whisperx=None,
                merge_gap_seconds=0.8,
                min_non_speech_seconds=1.75,
                snap_gap_seconds=1.0,
                min_region_seconds=0.05,
                min_segment_seconds=0.2,
            ).to_namespace()
        )
        return {"timeline": str(output)}


class BuildProfilesStep(PipelineStep):
    step_id = "build_profiles"
    title = "Build speaker profiles"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.profiles]

    def execute(self, context: PipelineContext) -> dict[str, Any]:
        assert context.artifacts is not None
        output = speaker_profiles.build_profiles(
            SpeakerProfilesProcessorInput(
                timeline=context.artifacts.timeline,
                output=context.artifacts.profiles,
            ).to_namespace()
        )
        return {"profiles": str(output)}


class BuildViewerStep(PipelineStep):
    step_id = "build_viewer"
    title = "Build static viewer"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.viewer]

    def execute(self, context: PipelineContext) -> dict[str, Any]:
        assert context.artifacts is not None
        output = case_viewer.build_viewer(
            ViewerProcessorInput(
                timeline=context.artifacts.timeline,
                profiles=context.artifacts.profiles,
                output=context.artifacts.viewer,
            ).to_namespace()
        )
        return {"viewer": str(output)}


class ApplyQaMarkersStep(PipelineStep):
    step_id = "apply_qa_markers"
    title = "Apply QA markers"

    def report_path(self, context: PipelineContext) -> Path:
        assert context.artifacts is not None
        return (
            context.resolve(context.config.paths.log_dir)
            / f"{context.artifacts.timeline.stem}.qa-marker-apply-report.json"
        )

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.timeline, self.report_path(context)]

    def execute(self, context: PipelineContext) -> dict[str, Any]:
        assert context.artifacts is not None
        markers = context.config.qa.markers
        if not markers:
            raise RuntimeError("QA marker step enabled without qa.markers")
        marker = context.resolve(markers[0])
        report = self.report_path(context)
        output = qa_markers.apply_markers(
            QaMarkersProcessorInput(
                timeline=context.artifacts.timeline,
                markers=marker,
                output=context.artifacts.timeline,
                report=report,
                default_words_per_second=2.8,
                min_segment_duration=0.2,
                enforce_order=context.config.qa.enforce_order,
                min_order_gap=context.config.qa.min_order_gap,
            ).to_namespace()
        )
        return {"timeline": str(output), "report": str(report), "markers": str(marker)}


def first_chunk_error(report: dict[str, Any]) -> str | None:
    for chunk in report.get("chunks", []):
        if not isinstance(chunk, dict) or chunk.get("quality") != "error":
            continue
        stderr = chunk.get("stderr")
        if isinstance(stderr, list) and stderr:
            return "\n".join(str(line) for line in stderr[-8:])
        return f"chunk {chunk.get('index')} returned {chunk.get('returncode')}"
    return None
