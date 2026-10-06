"""Extract official transcript text and speaker turns."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from scotus_pipeline.context import PipelineContext
from scotus_pipeline.io import atomic_write_json, atomic_write_text
from scotus_pipeline.processors import (
    extract_transcript,
)
from scotus_pipeline.processors.contracts import (
    TranscriptProcessorResult,
)
from scotus_pipeline.steps.base import PipelineStep


class ExtractTranscriptStep(PipelineStep):
    step_id = "extract_transcript"
    title = "Extract transcript PDF"

    def outputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.speaker_turns, artifacts.transcript_text]

    def inputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.transcript_pdf]

    def execute(self, context: PipelineContext) -> TranscriptProcessorResult:
        artifacts = context.require_artifacts()
        output_dir = artifacts.speaker_turns.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        text = extract_transcript.extract_pdf_text(artifacts.transcript_pdf, layout=True)
        turns = extract_transcript.parse_speaker_turns(text)

        text_path = artifacts.transcript_text
        turns_path = artifacts.speaker_turns
        atomic_write_text(text_path, text)
        atomic_write_json(turns_path, [asdict(turn) for turn in turns])
        return TranscriptProcessorResult(
            text=text_path,
            speaker_turns=turns_path,
            turn_count=len(turns),
            outputs=[text_path, turns_path],
            metrics={"turn_count": len(turns)},
        )
