#!/usr/bin/env python3
"""Refine a case timeline with speech and non-speech regions from WhisperX cache."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ALIGNMENT_OUTPUT = Path("alignment-output")
DEFAULT_TIMELINE_OUTPUT = Path("timeline-output")
WHISPERX_LABEL_RE = re.compile(r"\.(?P<start>\d{6})-(?P<end>\d{6})\.whisperx\.json$")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".part")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def resolve_timeline(explicit: Path | None) -> Path:
    if explicit:
        if not explicit.exists():
            raise FileNotFoundError(explicit)
        return explicit
    raise ValueError("timeline is required")


def whisperx_offset(path: Path) -> float | None:
    match = WHISPERX_LABEL_RE.search(path.name)
    if not match:
        return None
    return float(match.group("start"))


def resolve_whisperx_paths(timeline: dict[str, Any], cache_dir: Path, explicit: list[Path] | None) -> list[Path]:
    if explicit:
        return [path for path in explicit if path.exists()]

    case = timeline.get("case", {}) if isinstance(timeline, dict) else {}
    audio_file = case.get("audio_file") if isinstance(case, dict) else None
    stem = Path(str(audio_file)).stem if audio_file else "*"
    return sorted(cache_dir.glob(f"{stem}.*.whisperx.json"))


def collect_speech_regions(paths: list[Path], min_region_seconds: float) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for path in paths:
        offset = whisperx_offset(path)
        if offset is None:
            continue
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        segments = data.get("segments", []) if isinstance(data, dict) else []
        if not isinstance(segments, list):
            continue
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            try:
                start = offset + float(segment.get("start"))
                end = offset + float(segment.get("end"))
            except (TypeError, ValueError):
                continue
            if end - start < min_region_seconds:
                continue
            regions.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(end - start, 3),
                    "source": str(path),
                }
            )
    return sorted(regions, key=lambda region: (float(region["start"]), float(region["end"])))


def merge_regions(regions: list[dict[str, Any]], merge_gap_seconds: float) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for region in regions:
        start = float(region["start"])
        end = float(region["end"])
        if not merged or start - float(merged[-1]["end"]) > merge_gap_seconds:
            merged.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(end - start, 3),
                    "source_count": 1,
                }
            )
            continue
        previous = merged[-1]
        previous["end"] = round(max(float(previous["end"]), end), 3)
        previous["duration"] = round(float(previous["end"]) - float(previous["start"]), 3)
        previous["source_count"] = int(previous.get("source_count", 1)) + 1
    return merged


def non_speech_events(
    speech_regions: list[dict[str, Any]],
    duration: float,
    min_gap_seconds: float,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    cursor = 0.0
    index = 0
    for region in speech_regions:
        start = float(region["start"])
        if start - cursor >= min_gap_seconds:
            events.append(build_non_speech_event(index, cursor, start))
            index += 1
        cursor = max(cursor, float(region["end"]))
    if duration - cursor >= min_gap_seconds:
        events.append(build_non_speech_event(index, cursor, duration))
    return events


def build_non_speech_event(index: int, start: float, end: float) -> dict[str, Any]:
    return {
        "id": f"non-speech-{index:04d}",
        "type": "non_speech",
        "label": "No speech detected",
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(end - start, 3),
        "source": "whisperx_speech_gap",
    }


def transcript_word_regions(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    for segment in segments:
        words = segment.get("words")
        if not isinstance(words, list):
            continue
        for word in words:
            if not isinstance(word, dict):
                continue
            try:
                start = float(word["start"])
                end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if end <= start:
                continue
            regions.append(
                {
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "duration": round(end - start, 3),
                    "source": "official_transcript_word",
                }
            )
    return merge_regions(sorted(regions, key=lambda region: (float(region["start"]), float(region["end"]))), 0.05)


def clip_non_speech_events(
    events: list[dict[str, Any]],
    protected_regions: list[dict[str, Any]],
    min_gap_seconds: float,
) -> tuple[list[dict[str, Any]], int]:
    clipped: list[dict[str, Any]] = []
    suppressed = 0
    protected = sorted(protected_regions, key=lambda region: (float(region["start"]), float(region["end"])))

    for event in events:
        try:
            parts = [(float(event["start"]), float(event["end"]))]
        except (KeyError, TypeError, ValueError):
            continue

        for region in protected:
            region_start = float(region["start"])
            region_end = float(region["end"])
            next_parts: list[tuple[float, float]] = []
            for start, end in parts:
                if region_end <= start or region_start >= end:
                    next_parts.append((start, end))
                    continue
                if region_start > start:
                    next_parts.append((start, min(region_start, end)))
                if region_end < end:
                    next_parts.append((max(region_end, start), end))
            parts = next_parts
            if not parts:
                break

        for start, end in parts:
            if end - start < min_gap_seconds:
                suppressed += 1
                continue
            clipped.append(build_non_speech_event(len(clipped), start, end))

    return clipped, suppressed


def overlaps(region: dict[str, Any], start: float, end: float) -> bool:
    return float(region["end"]) > start and float(region["start"]) < end


def refine_segments(
    segments: list[dict[str, Any]],
    speech_regions: list[dict[str, Any]],
    snap_gap_seconds: float,
    min_segment_seconds: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    refined: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    for segment in segments:
        if not isinstance(segment, dict):
            continue
        try:
            original_start = float(segment.get("start"))
            original_end = float(segment.get("end"))
        except (TypeError, ValueError):
            refined.append(segment)
            continue

        matching = [region for region in speech_regions if overlaps(region, original_start, original_end)]
        if not matching:
            flags.append(
                {
                    "turn_index": segment.get("turn_index"),
                    "speaker": segment.get("speaker"),
                    "reason": "no_speech_region_overlaps_turn",
                    "start": round(original_start, 3),
                    "end": round(original_end, 3),
                }
            )
            refined.append(segment)
            continue

        new_start = original_start
        new_end = original_end
        first_speech_start = float(matching[0]["start"])
        last_speech_end = float(matching[-1]["end"])

        if first_speech_start > original_start and first_speech_start - original_start >= snap_gap_seconds:
            new_start = first_speech_start
        if original_end > last_speech_end and original_end - last_speech_end >= snap_gap_seconds:
            new_end = last_speech_end

        word_start, word_end = word_time_bounds(segment)
        if word_start is not None and word_start < new_start:
            new_start = word_start
        if word_end is not None and word_end > new_end:
            new_end = word_end

        if new_end - new_start < min_segment_seconds:
            flags.append(
                {
                    "turn_index": segment.get("turn_index"),
                    "speaker": segment.get("speaker"),
                    "reason": "speech_refinement_would_make_turn_too_short",
                    "start": round(original_start, 3),
                    "end": round(original_end, 3),
                }
            )
            refined.append(segment)
            continue

        source = segment.get("source", {})
        if not isinstance(source, dict):
            source = {}
        revised = {
            **segment,
            "start": round(new_start, 3),
            "end": round(new_end, 3),
            "duration": round(max(0.0, new_end - new_start), 3),
            "source": {
                **source,
                "speech_refined": True,
                "speech_region_count": len(matching),
            },
        }
        if new_start != original_start or new_end != original_end:
            revised["source"]["pre_speech_refinement"] = {
                "start": round(original_start, 3),
                "end": round(original_end, 3),
            }
        refined.append(revised)
    return refined, flags


def word_time_bounds(segment: dict[str, Any]) -> tuple[float | None, float | None]:
    words = segment.get("words")
    if not isinstance(words, list):
        return None, None
    starts: list[float] = []
    ends: list[float] = []
    for word in words:
        if not isinstance(word, dict):
            continue
        try:
            start = float(word["start"])
            end = float(word["end"])
        except (KeyError, TypeError, ValueError):
            continue
        starts.append(start)
        ends.append(end)
    if not starts or not ends:
        return None, None
    return min(starts), max(ends)


def refine_timeline(args: argparse.Namespace) -> Path:
    timeline_path = resolve_timeline(args.timeline)
    timeline = load_json(timeline_path)
    if not isinstance(timeline, dict):
        raise RuntimeError(f"timeline must be a JSON object: {timeline_path}")

    duration = float(timeline.get("duration") or 0.0)
    segments = timeline.get("segments", [])
    if not isinstance(segments, list) or not segments:
        raise RuntimeError(f"timeline has no segments: {timeline_path}")

    whisperx_paths = resolve_whisperx_paths(timeline, args.cache_dir, args.whisperx)
    raw_regions = collect_speech_regions(whisperx_paths, args.min_region_seconds)
    speech_regions = merge_regions(raw_regions, args.merge_gap_seconds)
    if not speech_regions:
        raise RuntimeError("no WhisperX speech regions found; run chunk alignment first")

    refined_segments, flags = refine_segments(
        segments,
        speech_regions,
        args.snap_gap_seconds,
        args.min_segment_seconds,
    )
    raw_events = non_speech_events(speech_regions, duration, args.min_non_speech_seconds)
    word_regions = transcript_word_regions(refined_segments)
    events, suppressed_events = clip_non_speech_events(raw_events, word_regions, args.min_non_speech_seconds)

    timeline["segments"] = refined_segments
    timeline["events"] = [event for event in timeline.get("events", []) if event.get("type") != "non_speech"] + events
    timeline["created_at"] = datetime.now(timezone.utc).isoformat()
    timeline.setdefault("sources", {})["speech_regions"] = {
        "available": True,
        "method": "whisperx_segment_gap_detection",
        "cache_dir": str(args.cache_dir),
        "whisperx_file_count": len(whisperx_paths),
        "raw_region_count": len(raw_regions),
        "merged_region_count": len(speech_regions),
        "transcript_word_region_count": len(word_regions),
        "raw_non_speech_event_count": len(raw_events),
        "non_speech_event_count": len(events),
        "suppressed_clipped_event_piece_count": suppressed_events,
        "settings": {
            "merge_gap_seconds": args.merge_gap_seconds,
            "min_non_speech_seconds": args.min_non_speech_seconds,
            "snap_gap_seconds": args.snap_gap_seconds,
            "min_region_seconds": args.min_region_seconds,
            "min_segment_seconds": args.min_segment_seconds,
        },
        "qa_flags": flags,
    }
    timeline.setdefault("timing", {})["speech_aware"] = True
    timeline.setdefault("timing", {}).setdefault("notes", []).append(
        "WhisperX speech regions are used to expose non-speech gaps and trim obvious silent turn overhangs."
    )

    output = args.output or timeline_path
    write_json(output, timeline)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refine a timeline with speech/non-speech regions.")
    parser.add_argument("--timeline", type=Path, help="case timeline JSON to refine")
    parser.add_argument("--output", type=Path, help="refined timeline output path; defaults to overwriting --timeline")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_ALIGNMENT_OUTPUT / "cache")
    parser.add_argument("--whisperx", type=Path, action="append", help="explicit WhisperX JSON file; repeat as needed")
    parser.add_argument("--merge-gap-seconds", type=float, default=0.8)
    parser.add_argument("--min-non-speech-seconds", type=float, default=1.75)
    parser.add_argument("--snap-gap-seconds", type=float, default=1.0)
    parser.add_argument("--min-region-seconds", type=float, default=0.05)
    parser.add_argument("--min-segment-seconds", type=float, default=0.2)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = refine_timeline(args)
    timeline = load_json(output)
    speech = timeline.get("sources", {}).get("speech_regions", {})
    print(f"refined_timeline: {output}")
    print(f"speech_regions: {speech.get('merged_region_count')}")
    print(f"non_speech_events: {speech.get('non_speech_event_count')}")
    print(f"qa_flags: {len(speech.get('qa_flags', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
