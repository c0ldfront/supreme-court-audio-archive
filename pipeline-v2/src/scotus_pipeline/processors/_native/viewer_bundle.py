from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scotus_pipeline.artifacts import SpeakerProfilesV1, TimelineV1, ViewerBundleV1
from scotus_pipeline.processors._native._io import atomic_write_json
from scotus_pipeline.processors.contracts import ViewerBundleProcessorResult, ViewerProcessorInput

PATH_KEYS = {
    "alignment",
    "audio_file",
    "cache_dir",
    "chunk_file",
    "chunk_report",
    "diarization",
    "metadata_file",
    "official_word_alignment",
    "path",
    "profiles",
    "speaker_turns",
    "timeline",
    "transcript_file",
    "transcript_pdf",
    "whisperx",
}


def relative_asset(path: Path, output_path: Path) -> str:
    try:
        rel = os.path.relpath(path.resolve(), output_path.parent.resolve())
        return Path(rel).as_posix()
    except ValueError:
        return path.resolve().as_uri()


def public_bundle_payload(
    *,
    timeline: TimelineV1,
    profiles: SpeakerProfilesV1,
    audio_src: str,
) -> dict[str, Any]:
    timeline_payload = timeline.model_dump(mode="json")
    profiles_payload = profiles.model_dump(mode="json")
    scrub_local_paths(timeline_payload)
    scrub_local_paths(profiles_payload)
    for case in (timeline_payload.get("case"), profiles_payload.get("case")):
        if isinstance(case, dict):
            case["audio_file"] = audio_src
            case["metadata_file"] = None
    return {
        "schema_version": "1",
        "case": timeline_payload["case"],
        "timeline": timeline_payload,
        "profiles": profiles_payload,
        "audio_src": audio_src,
        "created_at": datetime.now(UTC).isoformat(),
    }


def scrub_local_paths(value: Any, key: str | None = None) -> None:
    if isinstance(value, dict):
        for child_key, child_value in list(value.items()):
            if isinstance(child_value, str) and is_local_path_key(child_key) and not is_url(child_value):
                value[child_key] = None
            else:
                scrub_local_paths(child_value, child_key)
        return
    if isinstance(value, list):
        for item in value:
            scrub_local_paths(item, key)


def is_local_path_key(key: str) -> bool:
    return key in PATH_KEYS or key.endswith("_path") or (key.endswith("_file") and not key.endswith("_url"))


def is_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


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
    audio_src = relative_asset(audio_src_path, output)
    bundle = ViewerBundleV1.model_validate(
        public_bundle_payload(timeline=timeline, profiles=profiles, audio_src=audio_src)
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
