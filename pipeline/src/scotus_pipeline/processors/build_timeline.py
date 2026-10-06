#!/usr/bin/env python3
"""Build a viewer-ready SCOTUS case timeline JSON."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scotus_pipeline.io import atomic_write_json
from scotus_pipeline.processors.contracts import BuildTimelineProcessorInput

PALETTE = [
    "#1f77b4",
    "#d62728",
    "#2ca02c",
    "#9467bd",
    "#ff7f0e",
    "#17becf",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#264653",
    "#e76f51",
]


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload)


def find_case_dir(case_dir: Path | None) -> Path:
    if case_dir:
        if not case_dir.exists():
            raise FileNotFoundError(case_dir)
        return case_dir
    raise ValueError("case_dir is required")


def resolve_audio(metadata: dict[str, Any], case_dir: Path, explicit: Path | None) -> Path:
    if explicit:
        return explicit
    audio_file = metadata.get("audio_file")
    if isinstance(audio_file, dict) and audio_file.get("path"):
        return Path(str(audio_file["path"]))
    matches = sorted(case_dir.glob("*.mp3"))
    if not matches:
        raise FileNotFoundError(f"no MP3 found in {case_dir}")
    return matches[0]


def resolve_speaker_turns(audio_path: Path, explicit: Path | None) -> Path:
    if explicit:
        return explicit
    raise ValueError(f"speaker_turns is required for audio {audio_path}")


def resolve_optional_audio_artifact(audio_path: Path, suffix: str, explicit: Path | None) -> Path | None:
    if explicit:
        return explicit
    return None


def resolve_alignment(audio_path: Path, explicit: Path | None) -> Path | None:
    if explicit:
        return explicit
    return None


def alignment_coverage_end(path: Path) -> float:
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return 0.0
    window = data.get("window", {}) if isinstance(data, dict) else {}
    try:
        return float(window.get("end", 0.0))
    except (TypeError, ValueError):
        return 0.0


def artifact_max_end(path: Path) -> float:
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return 0.0
    segments = data.get("segments", []) if isinstance(data, dict) else data
    if not isinstance(segments, list):
        return 0.0
    ends = []
    for segment in segments:
        if isinstance(segment, dict):
            with suppress(TypeError, ValueError):
                ends.append(float(segment.get("end", 0.0)))
    return max(ends, default=0.0)


def audio_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required but was not found on PATH")
    output = subprocess.check_output(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
    )
    return float(output.strip())


def classify_speaker(speaker: str) -> str:
    if speaker.startswith("CHIEF JUSTICE") or speaker.startswith("JUSTICE"):
        return "justice"
    return "advocate"


def display_name(speaker: str) -> str:
    if speaker.startswith("MR. ") or speaker.startswith("MS. ") or speaker.startswith("MRS. "):
        prefix, name = speaker.split(" ", 1)
        return f"{prefix} {name.title()}"
    return speaker.title()


def word_count(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text))


def build_speakers(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = Counter(turn["speaker"] for turn in turns)
    speakers = []
    for index, speaker in enumerate(sorted(counts)):
        speakers.append(
            {
                "id": speaker,
                "display_name": display_name(speaker),
                "role": classify_speaker(speaker),
                "color": PALETTE[index % len(PALETTE)],
                "turn_count": counts[speaker],
            }
        )
    return speakers


def line_based_segments(turns: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    first_line = min(int(turn["start_line"]) for turn in turns)
    last_line = max(int(turn["end_line"]) for turn in turns)
    line_span = max(1, last_line - first_line)

    starts = [
        max(0.0, min(duration, ((int(turn["start_line"]) - first_line) / line_span) * duration))
        for turn in turns
    ]
    starts[0] = 0.0

    segments: list[dict[str, Any]] = []
    for index, turn in enumerate(turns):
        start = starts[index]
        end = starts[index + 1] if index + 1 < len(starts) else duration
        if end <= start:
            end = min(duration, start + 0.25)
        segments.append(
            {
                "id": f"turn-{index:04d}",
                "turn_index": int(turn["index"]),
                "speaker": turn["speaker"],
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
                "text": turn["text"],
                "word_count": word_count(turn["text"]),
                "source": {
                    "speaker_turn_start_line": turn["start_line"],
                    "speaker_turn_end_line": turn["end_line"],
                    "timing": "line_position_estimate",
                },
            }
        )
    return segments


def apply_alignment(
    segments: list[dict[str, Any]],
    alignment_path: Path | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not alignment_path:
        return segments, {"path": None, "available": False}

    alignment = load_json(alignment_path)
    aligned_turns = alignment.get("aligned_turns", []) if isinstance(alignment, dict) else []
    usable = {
        int(turn["turn_index"]): turn
        for turn in aligned_turns
        if turn.get("start") is not None
        and turn.get("end") is not None
        and turn.get("status") in {"matched", "partial_clip", "low_confidence"}
    }

    updated = []
    for segment in segments:
        aligned = usable.get(int(segment["turn_index"]))
        if not aligned:
            updated.append(segment)
            continue
        start = float(aligned["start"])
        end = float(aligned["end"])
        revised = {
            **segment,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(max(0.0, end - start), 3),
            "source": {
                **segment["source"],
                "timing": "whisperx_alignment",
                "alignment_status": aligned.get("status"),
                "alignment_confidence": aligned.get("confidence"),
            },
        }
        updated.append(revised)

    status_counts = alignment.get("alignment", {}).get("status_counts", {})
    return updated, {
        "path": str(alignment_path),
        "available": True,
        "applied_turn_count": len(usable),
        "status_counts": status_counts,
        "window": alignment.get("window", {}),
    }


def smooth_unaligned_between_anchors(segments: list[dict[str, Any]], duration: float) -> list[dict[str, Any]]:
    aligned_indexes = [
        index
        for index, segment in enumerate(segments)
        if segment["source"].get("timing") == "whisperx_alignment"
    ]
    if not aligned_indexes:
        return segments

    smoothed = [dict(segment) for segment in segments]
    anchors = [-1, *aligned_indexes, len(smoothed)]

    for left_anchor, right_anchor in zip(anchors, anchors[1:]):
        run_start = left_anchor + 1
        run_end = right_anchor
        if run_start >= run_end:
            continue

        left_time = 0.0 if left_anchor == -1 else float(smoothed[left_anchor]["end"])
        right_time = duration if right_anchor == len(smoothed) else float(smoothed[right_anchor]["start"])
        available = max(0.0, right_time - left_time)
        run = smoothed[run_start:run_end]
        weights = [max(1, int(segment.get("word_count", 1))) for segment in run]
        total_weight = sum(weights) or len(run)
        cursor = left_time

        for segment, weight in zip(run, weights):
            share = available * (weight / total_weight) if available else 0.0
            end = cursor + share
            if end <= cursor:
                end = cursor
            segment["start"] = round(cursor, 3)
            segment["end"] = round(end, 3)
            segment["duration"] = round(max(0.0, end - cursor), 3)
            segment["source"] = {
                **segment["source"],
                "timing": "interpolated_between_alignment_anchors",
            }
            cursor = end

    return smoothed


def coverage_from_segments(segments: list[dict[str, Any]], duration: float) -> dict[str, Any]:
    if not segments:
        return {"seconds": 0.0, "ratio": 0.0, "max_end": 0.0}
    max_end = max(float(segment.get("end", 0.0)) for segment in segments)
    return {
        "seconds": round(max_end, 3),
        "ratio": round(max_end / duration, 4) if duration else 0.0,
        "max_end": round(max_end, 3),
    }


def load_whisperx(path: Path | None, duration: float) -> dict[str, Any]:
    if not path:
        return {"path": None, "available": False}
    data = load_json(path)
    segments = data.get("segments", []) if isinstance(data, dict) else []
    return {
        "path": str(path),
        "available": True,
        "segment_count": len(segments),
        "coverage": coverage_from_segments(segments, duration),
        "language": data.get("language") if isinstance(data, dict) else None,
    }


def load_diarization(path: Path | None, duration: float) -> dict[str, Any]:
    if not path:
        return {"path": None, "available": False}
    segments = load_json(path)
    if not isinstance(segments, list):
        segments = []
    speakers = sorted({segment.get("speaker") for segment in segments if segment.get("speaker")})
    return {
        "path": str(path),
        "available": True,
        "segment_count": len(segments),
        "speakers": speakers,
        "coverage": coverage_from_segments(segments, duration),
    }


def build_timeline(args: BuildTimelineProcessorInput) -> Path:
    case_dir = find_case_dir(args.case_dir)
    metadata_path = args.metadata or case_dir / "metadata.json"
    metadata = load_json(metadata_path)
    audio_path = resolve_audio(metadata, case_dir, args.audio)
    duration = audio_duration(audio_path)

    speaker_turns_path = resolve_speaker_turns(audio_path, args.speaker_turns)
    speaker_turns = load_json(speaker_turns_path)
    if not isinstance(speaker_turns, list) or not speaker_turns:
        raise RuntimeError(f"no speaker turns found in {speaker_turns_path}")

    whisperx_path = resolve_optional_audio_artifact(audio_path, ".whisperx.json", args.whisperx)
    diarization_path = resolve_optional_audio_artifact(audio_path, ".diarization.json", args.diarization)
    alignment_path = resolve_alignment(audio_path, args.alignment)
    whisperx = load_whisperx(whisperx_path, duration)
    diarization = load_diarization(diarization_path, duration)

    segments = line_based_segments(speaker_turns, duration)
    segments, alignment = apply_alignment(segments, alignment_path)
    if alignment.get("available"):
        segments = smooth_unaligned_between_anchors(segments, duration)
    output = args.output

    payload = {
        "created_at": datetime.now(UTC).isoformat(),
        "case": {
            "docket": metadata.get("docket"),
            "name": metadata.get("case_name"),
            "term": metadata.get("term"),
            "date_argued": metadata.get("date_argued"),
            "date_argued_iso": metadata.get("date_argued_iso"),
            "detail_url": metadata.get("detail_url"),
            "audio_url": metadata.get("audio_url"),
            "transcript_url": metadata.get("transcript_url"),
            "audio_file": str(audio_path),
            "metadata_file": str(metadata_path),
        },
        "duration": round(duration, 3),
        "speakers": build_speakers(speaker_turns),
        "segments": segments,
        "sources": {
            "speaker_turns": str(speaker_turns_path),
            "whisperx": whisperx,
            "diarization": diarization,
            "alignment": alignment,
        },
        "timing": {
            "method": "hybrid_alignment" if alignment.get("available") else "line_position_estimate",
            "confidence": "mixed" if alignment.get("available") else "rough",
            "notes": [
                "Speaker names and text come from the official transcript PDF.",
                "Aligned turns use WhisperX timestamps when available.",
                "Unaligned turns are estimated from transcript line positions over full audio duration.",
                "Use full-case WhisperX alignment in a later pass for precise word-level timing.",
            ],
        },
    }
    write_json(output, payload)
    return output
