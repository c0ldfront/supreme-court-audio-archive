from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from scotus_pipeline.artifacts import (
    validate_chunk_report_file,
    validate_merged_alignment_file,
    validate_official_word_alignment_file,
    validate_profiles_file,
    validate_timeline_file,
)
from scotus_pipeline.context import PipelineContext
from scotus_pipeline.events import EventType, Severity
from scotus_pipeline.processors._native import (
    alignment_chunks,
    archive_audio,
    build_timeline,
    extract_transcript,
    merge_alignments,
    official_words,
    qa_markers,
    speaker_profiles,
    speech_gaps,
    viewer_bundle,
)
from scotus_pipeline.processors._native._io import atomic_write_json, atomic_write_text
from scotus_pipeline.processors.contracts import (
    AlignmentChunksProcessorInput,
    AlignmentChunksProcessorResult,
    ArchiveProcessorInput,
    ArchiveProcessorResult,
    BuildTimelineProcessorInput,
    MergeAlignmentProcessorInput,
    MergeAlignmentProcessorResult,
    OfficialWordsProcessorInput,
    OfficialWordsProcessorResult,
    QaMarkersProcessorInput,
    QaMarkersProcessorResult,
    SpeakerProfilesProcessorInput,
    SpeakerProfilesProcessorResult,
    SpeechGapsProcessorInput,
    TimelineProcessorResult,
    TranscriptProcessorResult,
    ViewerBundleProcessorResult,
    ViewerProcessorInput,
)
from scotus_pipeline.steps.base import PipelineStep


class ArchiveStep(PipelineStep):
    step_id = "archive"
    title = "Archive case audio and transcript"

    def outputs(self, context: PipelineContext) -> list[Path]:
        return [context.resolve(context.config.paths.sources_dir)]

    def execute(self, context: PipelineContext) -> ArchiveProcessorResult:
        archive = context.config.archive
        result = archive_audio.archive_records(
            ArchiveProcessorInput(
                progress=self.progress_callback(context),
                output=context.resolve(context.config.paths.courts_dir),
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
            )
        )
        if result != 0:
            raise RuntimeError(f"archive processor returned {result}")
        archive_dir = context.resolve(context.config.paths.sources_dir)
        return ArchiveProcessorResult(archive_dir=archive_dir, outputs=[archive_dir])


class ExtractTranscriptStep(PipelineStep):
    step_id = "extract_transcript"
    title = "Extract transcript PDF"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.speaker_turns]

    def inputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.transcript_pdf]

    def execute(self, context: PipelineContext) -> TranscriptProcessorResult:
        assert context.artifacts is not None
        output_dir = context.artifacts.speaker_turns.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        text = extract_transcript.extract_pdf_text(context.artifacts.transcript_pdf, layout=True)
        turns = extract_transcript.parse_speaker_turns(text)

        text_path = context.artifacts.transcript_text
        turns_path = context.artifacts.speaker_turns
        atomic_write_text(text_path, text)
        atomic_write_json(turns_path, [asdict(turn) for turn in turns])
        return TranscriptProcessorResult(
            text=text_path,
            speaker_turns=turns_path,
            turn_count=len(turns),
            outputs=[text_path, turns_path],
            metrics={"turn_count": len(turns)},
        )


class BuildTimelineStep(PipelineStep):
    step_id = "build_timeline"
    title = "Build initial timeline"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.timeline]

    def inputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.metadata, context.artifacts.audio, context.artifacts.speaker_turns]

    def execute(self, context: PipelineContext) -> TimelineProcessorResult:
        assert context.artifacts is not None
        output = build_timeline.build_timeline(
            BuildTimelineProcessorInput(
                progress=self.progress_callback(context),
                case_dir=context.artifacts.case_dir,
                metadata=context.artifacts.metadata,
                audio=context.artifacts.audio,
                speaker_turns=context.artifacts.speaker_turns,
                whisperx=None,
                diarization=None,
                alignment=None,
                output=context.artifacts.timeline,
            )
        )
        validate_timeline_file(output, audio=context.artifacts.audio)
        return TimelineProcessorResult(timeline=output, outputs=[output])


class RunAlignmentStep(PipelineStep):
    step_id = "run_alignment"
    title = "Run chunk alignment"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.chunk_report]

    def inputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.audio, context.artifacts.timeline, context.artifacts.speaker_turns]

    def execute(self, context: PipelineContext) -> AlignmentChunksProcessorResult:
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
                progress=self.progress_callback(context),
                case_dir=context.artifacts.case_dir,
                metadata=context.artifacts.metadata,
                audio=context.artifacts.audio,
                timeline=context.artifacts.timeline,
                speaker_turns=context.artifacts.speaker_turns,
                output_dir=context.artifacts.merged_alignment.parent,
                cache_dir=context.artifacts.cache_dir / "asr",
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
                use_asr_worker=True,
            )
        )
        validate_chunk_report_file(report, audio=context.artifacts.audio)
        return AlignmentChunksProcessorResult(
            report=report,
            outputs=[report],
            metrics={"requested_workers": alignment.workers, "effective_workers": workers},
        )

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

    def inputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.audio, context.artifacts.speaker_turns, context.artifacts.chunk_report]

    def execute(self, context: PipelineContext) -> MergeAlignmentProcessorResult:
        assert context.artifacts is not None
        merged = merge_alignments.build_merged_alignment(
            MergeAlignmentProcessorInput(
                progress=self.progress_callback(context),
                case_dir=context.artifacts.case_dir,
                metadata=context.artifacts.metadata,
                audio=context.artifacts.audio,
                speaker_turns=context.artifacts.speaker_turns,
                output_dir=context.artifacts.merged_alignment.parent,
                output=context.artifacts.merged_alignment,
                chunks_dir=context.artifacts.chunk_report.parent,
                chunk_report=context.artifacts.chunk_report,
                chunks=None,
                all_chunks=False,
                rebuild_timeline=False,
                timeline_output=None,
            )
        )
        validate_merged_alignment_file(merged, audio=context.artifacts.audio)
        timeline = build_timeline.build_timeline(
            BuildTimelineProcessorInput(
                progress=self.progress_callback(context),
                case_dir=context.artifacts.case_dir,
                metadata=context.artifacts.metadata,
                audio=context.artifacts.audio,
                speaker_turns=context.artifacts.speaker_turns,
                whisperx=None,
                diarization=None,
                alignment=merged,
                output=context.artifacts.timeline,
            )
        )
        validate_timeline_file(timeline, audio=context.artifacts.audio)
        return MergeAlignmentProcessorResult(merged_alignment=merged, timeline=timeline, outputs=[merged, timeline])


class AlignOfficialWordsStep(PipelineStep):
    step_id = "align_official_words"
    title = "Align official transcript words"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.official_word_alignment, context.artifacts.timeline]

    def inputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.audio, context.artifacts.timeline]

    def execute(self, context: PipelineContext) -> OfficialWordsProcessorResult:
        assert context.artifacts is not None
        official = context.config.official_words
        alignment, timeline = official_words.run(
            OfficialWordsProcessorInput(
                progress=self.progress_callback(context),
                timeline=context.artifacts.timeline,
                audio=context.artifacts.audio,
                output_alignment=context.artifacts.official_word_alignment,
                output_timeline=context.artifacts.timeline,
                method=official.method,
                cache_dir=context.artifacts.cache_dir / "asr",
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
        validate_official_word_alignment_file(alignment, audio=context.artifacts.audio)
        validate_timeline_file(timeline, audio=context.artifacts.audio)
        return OfficialWordsProcessorResult(
            official_word_alignment=alignment,
            timeline=timeline,
            outputs=[alignment, timeline],
        )


class RefineSpeechGapsStep(PipelineStep):
    step_id = "refine_speech_gaps"
    title = "Refine speech and non-speech regions"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.timeline]

    def inputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.timeline, context.artifacts.official_word_alignment]

    def execute(self, context: PipelineContext) -> TimelineProcessorResult:
        assert context.artifacts is not None
        output = speech_gaps.refine_timeline(
            SpeechGapsProcessorInput(
                progress=self.progress_callback(context),
                timeline=context.artifacts.timeline,
                output=context.artifacts.timeline,
                cache_dir=context.artifacts.cache_dir / "asr",
                whisperx=None,
                merge_gap_seconds=0.8,
                min_non_speech_seconds=1.75,
                snap_gap_seconds=1.0,
                min_region_seconds=0.05,
                min_segment_seconds=0.2,
            )
        )
        validate_timeline_file(output, audio=context.artifacts.audio)
        return TimelineProcessorResult(timeline=output, outputs=[output])


class BuildProfilesStep(PipelineStep):
    step_id = "build_profiles"
    title = "Build speaker profiles"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.profiles]

    def inputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.timeline]

    def execute(self, context: PipelineContext) -> SpeakerProfilesProcessorResult:
        assert context.artifacts is not None
        output = speaker_profiles.build_profiles(
            SpeakerProfilesProcessorInput(
                progress=self.progress_callback(context),
                timeline=context.artifacts.timeline,
                output=context.artifacts.profiles,
            )
        )
        validate_profiles_file(output, audio=context.artifacts.audio)
        return SpeakerProfilesProcessorResult(profiles=output, outputs=[output])


class BuildViewerStep(PipelineStep):
    step_id = "build_viewer"
    title = "Build viewer bundle"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.viewer, context.artifacts.public_audio]

    def inputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.timeline, context.artifacts.profiles]

    def execute(self, context: PipelineContext) -> ViewerBundleProcessorResult:
        assert context.artifacts is not None
        result = viewer_bundle.build_viewer_bundle(
            ViewerProcessorInput(
                progress=self.progress_callback(context),
                timeline=context.artifacts.timeline,
                profiles=context.artifacts.profiles,
                output=context.artifacts.viewer,
                public_audio=context.artifacts.public_audio,
            )
        )
        return result


class ApplyQaMarkersStep(PipelineStep):
    step_id = "apply_qa_markers"
    title = "Apply QA markers"

    def report_path(self, context: PipelineContext) -> Path:
        assert context.artifacts is not None
        return context.artifacts.work_dir / "qa" / "qa-marker-apply-report.json"

    def outputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.timeline, self.report_path(context)]

    def inputs(self, context: PipelineContext) -> list[Path]:
        assert context.artifacts is not None
        return [context.artifacts.timeline, *[context.resolve(path) for path in context.config.qa.markers]]

    def execute(self, context: PipelineContext) -> QaMarkersProcessorResult:
        assert context.artifacts is not None
        markers = context.config.qa.markers
        if not markers:
            raise RuntimeError("QA marker step enabled without qa.markers")
        marker = context.resolve(markers[0])
        report = self.report_path(context)
        output = qa_markers.apply_markers(
            QaMarkersProcessorInput(
                progress=self.progress_callback(context),
                timeline=context.artifacts.timeline,
                markers=marker,
                output=context.artifacts.timeline,
                report=report,
                default_words_per_second=2.8,
                min_segment_duration=0.2,
                enforce_order=context.config.qa.enforce_order,
                min_order_gap=context.config.qa.min_order_gap,
            )
        )
        validate_timeline_file(output, audio=context.artifacts.audio)
        return QaMarkersProcessorResult(
            timeline=output,
            report=report,
            outputs=[output, report],
            metrics={"markers": str(marker)},
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
