from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

from scotus_pipeline.artifacts import SpeakerProfilesV1, TimelineV1, ViewerBundleV1
from scotus_pipeline.processors._native._io import atomic_write_json
from scotus_pipeline.processors.contracts import ViewerBundleProcessorResult, ViewerProcessorInput


def relative_asset(path: Path, output_path: Path) -> str:
    try:
        rel = os.path.relpath(path.resolve(), output_path.parent.resolve())
        return Path(rel).as_posix()
    except ValueError:
        return path.resolve().as_uri()


def build_viewer_bundle(input: ViewerProcessorInput) -> ViewerBundleProcessorResult:
    timeline = TimelineV1.model_validate_json(input.timeline.read_text(encoding="utf-8"))
    profiles = SpeakerProfilesV1.model_validate_json(input.profiles.read_text(encoding="utf-8"))
    audio_file = timeline.case.audio_file
    if not audio_file:
        raise ValueError("timeline case metadata has no audio_file")

    audio_path = Path(audio_file)
    timeline.case.assert_audio(audio_path)
    profiles.case.assert_audio(audio_path)

    output = input.output
    outputs = [output]
    audio_src_path = audio_path
    if input.public_audio is not None:
        input.public_audio.parent.mkdir(parents=True, exist_ok=True)
        if not input.public_audio.exists() or input.public_audio.resolve() != audio_path.resolve():
            shutil.copy2(audio_path, input.public_audio)
        audio_src_path = input.public_audio
        outputs.append(input.public_audio)
    bundle = ViewerBundleV1(
        case=timeline.case,
        timeline=timeline,
        profiles=profiles,
        audio_src=relative_asset(audio_src_path, output),
        created_at=datetime.now(UTC).isoformat(),
    )
    atomic_write_json(output, bundle.model_dump(mode="json"))
    return ViewerBundleProcessorResult(
        bundle=output,
        outputs=outputs,
        metrics={
            "segment_count": len(timeline.segments),
            "profile_count": len(profiles.profiles),
        },
    )
