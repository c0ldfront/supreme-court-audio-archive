"""Build speaker profiles and a portable viewer bundle."""

from __future__ import annotations

from pathlib import Path

from scotus_pipeline.artifacts import (
    validate_profiles_file,
)
from scotus_pipeline.context import PipelineContext
from scotus_pipeline.processors import (
    speaker_profiles,
    viewer_bundle,
)
from scotus_pipeline.processors.contracts import (
    SpeakerProfilesProcessorInput,
    SpeakerProfilesProcessorResult,
    ViewerBundleProcessorResult,
    ViewerProcessorInput,
)
from scotus_pipeline.steps.base import PipelineStep


class BuildProfilesStep(PipelineStep):
    step_id = "build_profiles"
    title = "Build speaker profiles"

    def outputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.profiles]

    def inputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.timeline]

    def execute(self, context: PipelineContext) -> SpeakerProfilesProcessorResult:
        artifacts = context.require_artifacts()
        output = speaker_profiles.build_profiles(
            SpeakerProfilesProcessorInput(
                progress=self.progress_callback(context),
                timeline=artifacts.timeline,
                output=artifacts.profiles,
            )
        )
        validate_profiles_file(output, audio=artifacts.audio)
        return SpeakerProfilesProcessorResult(profiles=output, outputs=[output])


class BuildViewerStep(PipelineStep):
    step_id = "build_viewer"
    title = "Build viewer bundle"

    def outputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.viewer, artifacts.public_audio]

    def inputs(self, context: PipelineContext) -> list[Path]:
        artifacts = context.require_artifacts()
        return [artifacts.timeline, artifacts.profiles]

    def execute(self, context: PipelineContext) -> ViewerBundleProcessorResult:
        artifacts = context.require_artifacts()
        result = viewer_bundle.build_viewer_bundle(
            ViewerProcessorInput(
                progress=self.progress_callback(context),
                timeline=artifacts.timeline,
                profiles=artifacts.profiles,
                output=artifacts.viewer,
                public_audio=artifacts.public_audio,
            )
        )
        return result
