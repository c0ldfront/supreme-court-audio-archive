#!/usr/bin/env python3
"""Build a viewer-ready SCOTUS case timeline JSON."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ARCHIVE = Path("archive/scotus-audio")
DEFAULT_TRANSCRIPT_OUTPUT = Path("transcript-output")
DEFAULT_AUDIO_OUTPUT = Path("audio-test-output")
DEFAULT_ALIGNMENT_OUTPUT = Path("alignment-output")
DEFAULT_OUTPUT = Path("timeline-output")
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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".part")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def find_case_dir(case_dir: Path | None) -> Path:
    if case_dir:
        if not case_dir.exists():
            raise FileNotFoundError(case_dir)
        return case_dir

    matches = sorted(DEFAULT_ARCHIVE.glob("*/**/metadata.json"))
    if not matches:
        raise FileNotFoundError(f"no metadata.json files found under {DEFAULT_ARCHIVE}")
    return matches[0].parent


def find_first(patterns: list[str], roots: list[Path]) -> Path | None:
    for root in roots:
        for pattern in patterns:
            matches = sorted(root.glob(pattern))
            if matches:
                return matches[0]
    return None


def find_matches(patterns: list[str], roots: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    matches: list[Path] = []
    for root in roots:
        for pattern in patterns:
            for path in sorted(root.glob(pattern)):
                if path not in seen:
                    matches.append(path)
                    seen.add(path)
    return matches


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
    stem = audio_path.stem
    found = find_first([f"{stem}.speaker-turns.json", "*.speaker-turns.json"], [DEFAULT_TRANSCRIPT_OUTPUT])
    if not found:
        raise FileNotFoundError(
            "speaker-turns JSON not found; run scripts/02_extract_transcript_pdf.py first"
        )
    return found


def resolve_optional_audio_artifact(audio_path: Path, suffix: str, explicit: Path | None) -> Path | None:
    if explicit:
        return explicit
    stem = audio_path.stem
    matches = find_matches([f"{stem}*{suffix}", f"*{suffix}"], [DEFAULT_AUDIO_OUTPUT])
    if not matches:
        return None
    return max(matches, key=artifact_max_end)


def resolve_alignment(audio_path: Path, explicit: Path | None) -> Path | None:
    if explicit:
        return explicit
    matches = find_matches(
        [f"{audio_path.stem}.align-*.json", "*.align-*.json"],
        [DEFAULT_ALIGNMENT_OUTPUT],
    )
    if not matches:
        return None
    return max(matches, key=alignment_coverage_end)


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
            try:
                ends.append(float(segment.get("end", 0.0)))
            except (TypeError, ValueError):
                pass
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
        right_time = (
            duration if right_anchor == len(smoothed) else float(smoothed[right_anchor]["start"])
        )
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


def build_timeline(args: argparse.Namespace) -> Path:
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
    output = args.output or DEFAULT_OUTPUT / f"{audio_path.stem}.case-timeline.json"

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a SCOTUS case timeline JSON.")
    parser.add_argument("--case-dir", type=Path, help="archived case directory containing metadata.json")
    parser.add_argument("--metadata", type=Path, help="metadata.json path")
    parser.add_argument("--audio", type=Path, help="MP3 path")
    parser.add_argument("--speaker-turns", type=Path, help="speaker-turns JSON from script 02")
    parser.add_argument("--whisperx", type=Path, help="optional WhisperX JSON")
    parser.add_argument("--diarization", type=Path, help="optional pyannote diarization JSON")
    parser.add_argument("--alignment", type=Path, help="optional alignment JSON from script 06")
    parser.add_argument("--output", type=Path, help="timeline JSON output path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = build_timeline(args)
    data = load_json(output)
    print(f"timeline: {output}")
    print(f"case: {data['case']['docket']} {data['case']['name']}")
    print(f"duration: {data['duration']}s")
    print(f"speakers: {len(data['speakers'])}")
    print(f"segments: {len(data['segments'])}")
    print(f"timing: {data['timing']['method']} ({data['timing']['confidence']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
