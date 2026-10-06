"""Download the official audio, transcript, and case metadata."""

from __future__ import annotations

from pathlib import Path

from scotus_pipeline.context import PipelineContext
from scotus_pipeline.processors import (
    archive_audio,
)
from scotus_pipeline.processors.contracts import (
    ArchiveProcessorInput,
    ArchiveProcessorResult,
)
from scotus_pipeline.steps.base import PipelineStep


class ArchiveStep(PipelineStep):
    step_id = "archive"
    title = "Archive case audio and transcript"

    def outputs(self, context: PipelineContext) -> list[Path]:
        source = context.resolve(context.config.paths.sources_dir)
        if context.config.case.docket:
            from scotus_pipeline.cases import case_output_key

            source /= case_output_key({}, context.config.case.docket, Path("case"))
        return [source]

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
