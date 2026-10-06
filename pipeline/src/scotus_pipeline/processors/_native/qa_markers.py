#!/usr/bin/env python3
"""Apply exported QA markers as timing anchors to a case timeline."""

from __future__ import annotations

import json
import re
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scotus_pipeline.processors._native._io import atomic_write_json
from scotus_pipeline.processors.contracts import QaMarkersProcessorInput


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload)


def resolve_timeline(explicit: Path | None) -> Path:
    if explicit:
        if not explicit.exists():
            raise FileNotFoundError(explicit)
        return explicit
    raise ValueError("timeline is required")


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))


def marker_turn_index(marker: dict[str, Any]) -> int | None:
    turn = marker.get("turn", {})
    if not isinstance(turn, dict) or turn.get("turn_index") is None:
        return None
    try:
        return int(turn["turn_index"])
    except (TypeError, ValueError):
        return None


def marker_anchor(marker: dict[str, Any]) -> tuple[float, float] | None:
    selection = marker.get("selection", {})
    if not isinstance(selection, dict):
        return None
    try:
        word_index = float(selection["word_start_index"])
        expected = float(marker["expected_time_seconds"])
    except (KeyError, TypeError, ValueError):
        return None
    return word_index, expected


def fit_line(points: list[tuple[float, float]]) -> tuple[float, float] | None:
    if len(points) < 2:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / denominator
    intercept = mean_y - slope * mean_x
    return slope, intercept


def estimated_word_times(start: float, end: float, count: int) -> list[dict[str, Any]]:
    if count <= 0:
        return []
    duration = max(0.001, end - start)
    step = duration / count
    return [
        {
            "start": round(start + step * index, 3),
            "end": round(start + step * (index + 1), 3),
        }
        for index in range(count)
    ]


def word_texts(text: str) -> list[str]:
    return re.findall(r"\S+", text)


def calibrate_segment(
    segment: dict[str, Any],
    markers: list[dict[str, Any]],
    default_words_per_second: float,
    min_duration: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    anchors = [anchor for marker in markers if (anchor := marker_anchor(marker)) is not None]
    if not anchors:
        return segment, []

    texts = word_texts(str(segment.get("text", "")))
    count = max(1, len(texts))
    flags: list[dict[str, Any]] = []
    line = fit_line(anchors)

    if line:
        slope, intercept = line
        if slope <= 0 or slope > 2.5:
            flags.append(
                {
                    "turn_index": segment.get("turn_index"),
                    "reason": "rejected_unreasonable_marker_slope",
                    "slope": round(slope, 4),
                }
            )
            line = None

    if line:
        slope, intercept = line
        start = intercept
        end = intercept + slope * count
        method = "qa_marker_linear_fit"
    else:
        word_index, expected = anchors[0]
        seconds_per_word = 1.0 / default_words_per_second
        start = expected - word_index * seconds_per_word
        end = start + count * seconds_per_word
        method = "qa_marker_single_anchor"

    if end <= start:
        end = start + min_duration
    if end - start < min_duration:
        end = start + min_duration
    if start < 0:
        end -= start
        start = 0.0

    words = []
    for index, timing in enumerate(estimated_word_times(start, end, count)):
        word = texts[index] if index < len(texts) else ""
        words.append(
            {
                "word": word,
                "start": timing["start"],
                "end": timing["end"],
                "score": None,
                "source": method,
            }
        )

    source = segment.get("source", {})
    if not isinstance(source, dict):
        source = {}
    revised = {
        **segment,
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(end - start, 3),
        "words": words,
        "source": {
            **source,
            "previous_timing": source.get("timing"),
            "timing": "qa_marker_calibrated",
            "qa_marker_count": len(markers),
            "qa_marker_method": method,
            "qa_marker_ids": [marker.get("id") for marker in markers],
        },
    }
    return revised, flags


def enforce_order(segments: list[dict[str, Any]], min_gap: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    for segment in segments:
        current = dict(segment)
        if ordered:
            previous = ordered[-1]
            previous_end = float(previous.get("end", 0.0))
            start = float(current.get("start", 0.0))
            if start < previous_end:
                if is_qa_calibrated(current):
                    trim_segment_end(previous, max(0.0, start - min_gap))
                    flags.append(
                        {
                            "turn_index": previous.get("turn_index"),
                            "reason": "trimmed_before_qa_marker_turn",
                            "next_turn_index": current.get("turn_index"),
                        }
                    )
                else:
                    shift = previous_end - start + min_gap
                    shift_segment(current, shift)
                    flags.append(
                        {
                            "turn_index": current.get("turn_index"),
                            "reason": "qa_order_shift",
                            "shift_seconds": round(shift, 3),
                            "previous_turn_index": previous.get("turn_index"),
                        }
                    )
        ordered.append(current)
    return ordered, flags


def is_qa_calibrated(segment: dict[str, Any]) -> bool:
    source = segment.get("source", {})
    return isinstance(source, dict) and source.get("timing") == "qa_marker_calibrated"


def trim_segment_end(segment: dict[str, Any], end: float) -> None:
    start = float(segment.get("start", 0.0))
    end = max(start, end)
    segment["end"] = round(end, 3)
    segment["duration"] = round(max(0.0, end - start), 3)
    source = segment.get("source", {})
    if not isinstance(source, dict):
        source = {}
    segment["source"] = {**source, "qa_trimmed_end": round(end, 3)}


def shift_segment(segment: dict[str, Any], shift: float) -> None:
    segment["start"] = round(float(segment.get("start", 0.0)) + shift, 3)
    segment["end"] = round(float(segment.get("end", segment["start"])) + shift, 3)
    segment["duration"] = round(max(0.0, float(segment["end"]) - float(segment["start"])), 3)
    source = segment.get("source", {})
    if not isinstance(source, dict):
        source = {}
    segment["source"] = {**source, "qa_order_shift_seconds": round(shift, 3)}
    if isinstance(segment.get("words"), list):
        shifted_words = []
        for word in segment["words"]:
            if not isinstance(word, dict):
                continue
            shifted = dict(word)
            if shifted.get("start") is not None:
                shifted["start"] = round(float(shifted["start"]) + shift, 3)
            if shifted.get("end") is not None:
                shifted["end"] = round(float(shifted["end"]) + shift, 3)
            shifted_words.append(shifted)
        segment["words"] = shifted_words


def apply_markers(args: QaMarkersProcessorInput) -> Path:
    timeline_path = resolve_timeline(args.timeline)
    timeline = load_json(timeline_path)
    markers_payload = load_json(args.markers)
    markers = markers_payload.get("markers", markers_payload)
    if not isinstance(markers, list):
        raise RuntimeError("marker input must be a marker export object or marker list")

    grouped: dict[int, list[dict[str, Any]]] = {}
    for marker in markers:
        if not isinstance(marker, dict):
            continue
        turn_index = marker_turn_index(marker)
        if turn_index is None:
            continue
        grouped.setdefault(turn_index, []).append(marker)

    revised_segments = []
    flags: list[dict[str, Any]] = []
    applied = 0
    for segment in timeline.get("segments", []):
        if not isinstance(segment, dict) or segment.get("turn_index") is None:
            revised_segments.append(segment)
            continue
        turn_index = int(segment["turn_index"])
        turn_markers = grouped.get(turn_index, [])
        if not turn_markers:
            revised_segments.append(segment)
            continue
        revised, segment_flags = calibrate_segment(
            segment,
            turn_markers,
            args.default_words_per_second,
            args.min_segment_duration,
        )
        revised_segments.append(revised)
        flags.extend(segment_flags)
        applied += 1

    if args.enforce_order:
        revised_segments, order_flags = enforce_order(revised_segments, args.min_order_gap)
        flags.extend(order_flags)

    timeline["segments"] = revised_segments
    timeline["created_at"] = datetime.now(UTC).isoformat()
    timeline.setdefault("sources", {})["qa_markers"] = {
        "available": True,
        "path": str(args.markers),
        "marker_count": len(markers),
        "applied_turn_count": applied,
        "method": "qa_marker_word_index_calibration",
        "flags": flags,
    }
    timeline.setdefault("timing", {})["qa_marker_calibrated"] = True

    output = args.output or timeline_path
    write_json(output, timeline)

    report = {
        "created_at": datetime.now(UTC).isoformat(),
        "timeline": str(output),
        "markers": str(args.markers),
        "marker_count": len(markers),
        "applied_turn_count": applied,
        "flags": flags,
    }
    report_path = args.report
    write_json(report_path, report)
    print(f"qa_marker_report: {report_path}")
    return output
