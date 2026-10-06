"""Apply accepted word timings and constrain fallback turns."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from scotus_pipeline.processors.contracts import OfficialWordsProcessorInput
from scotus_pipeline.processors.official_words.tokens import safe_float


def apply_word_alignment(
    timeline: dict[str, Any], alignment: dict[str, Any], args: OfficialWordsProcessorInput
) -> dict[str, Any]:
    turns = alignment.get("turns", []) if isinstance(alignment, dict) else []
    by_index = {
        int(turn["turn_index"]): turn
        for turn in turns
        if isinstance(turn, dict) and turn.get("turn_index") is not None
    }
    accepted_statuses = {"aligned", "low_coverage", "low_score"} if args.apply_low_quality else {"aligned"}
    timing_method = alignment.get("settings", {}).get("method", "official_transcript_asr_sequence_alignment")
    applied = 0
    qa_flags: list[dict[str, Any]] = []
    updated_segments: list[dict[str, Any]] = []
    previous_applied_end: float | None = None

    for segment in timeline.get("segments", []):
        if not isinstance(segment, dict) or segment.get("turn_index") is None:
            updated_segments.append(segment)
            continue
        aligned = by_index.get(int(segment["turn_index"]))
        alignment_score = aligned.get("mean_word_score") if aligned else None
        reject_reason = None
        if not aligned:
            reject_reason = "missing_word_alignment"
        elif aligned.get("status") not in accepted_statuses:
            reject_reason = f"word_alignment_{aligned.get('status')}"
        elif (
            alignment_score is not None
            and float(alignment_score) < args.min_apply_mean_word_score
            and not args.apply_low_quality
        ):
            reject_reason = "word_alignment_low_apply_score"
        elif (
            previous_applied_end is not None
            and float(aligned["start"]) < previous_applied_end - args.max_turn_overlap_seconds
        ):
            reject_reason = "word_alignment_out_of_order"

        if reject_reason:
            updated_segments.append(segment)
            if aligned:
                qa_flags.append(
                    {
                        "turn_index": segment.get("turn_index"),
                        "speaker": segment.get("speaker"),
                        "reason": reject_reason,
                        "timed_word_ratio": aligned.get("timed_word_ratio"),
                        "mean_word_score": alignment_score,
                    }
                )
            continue

        source = segment.get("source", {})
        if not isinstance(source, dict):
            source = {}
        start = float(aligned["start"])
        end = float(aligned["end"])
        revised = {
            **segment,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(max(0.0, end - start), 3),
            "words": aligned.get("words", []),
            "source": {
                **source,
                "previous_timing": source.get("timing"),
                "timing": timing_method,
                "word_alignment_status": aligned.get("status"),
                "word_alignment_mean_score": aligned.get("mean_word_score"),
                "word_alignment_timed_ratio": aligned.get("timed_word_ratio"),
                "word_alignment_shift": aligned.get("shift"),
            },
        }
        updated_segments.append(revised)
        applied += 1
        previous_applied_end = max(previous_applied_end or 0.0, end)

    updated_segments, fallback_adjustments = constrain_fallback_segments(
        updated_segments, timing_method, args
    )
    qa_flags.extend(fallback_adjustments)
    timeline["segments"] = updated_segments
    timeline["created_at"] = datetime.now(UTC).isoformat()
    timeline.setdefault("sources", {})["official_word_alignment"] = {
        "available": True,
        "method": timing_method,
        "path": str(args.output_alignment),
        "applied_turn_count": applied,
        "summary": alignment.get("summary", {}),
        "qa_flags": qa_flags,
    }
    timeline.setdefault("timing", {})["method"] = timing_method
    timeline.setdefault("timing", {})["confidence"] = "word_aligned"
    timeline.setdefault("timing", {}).setdefault("notes", []).append(
        "Official transcript text is aligned to cached WhisperX ASR tokens for word-level timestamps; block forced alignment remains available as an explicit fallback."
    )
    return timeline


def is_applied_word_aligned(segment: dict[str, Any], timing_method: str) -> bool:
    source = segment.get("source")
    return (
        isinstance(source, dict)
        and source.get("timing") == timing_method
        and source.get("word_alignment_status") == "aligned"
    )


def rescale_words(segment: dict[str, Any], new_start: float, new_end: float) -> list[dict[str, Any]]:
    words = segment.get("words")
    if not isinstance(words, list) or not words:
        return []
    old_start = safe_float(segment.get("start"), new_start) or new_start
    old_end = safe_float(segment.get("end"), old_start) or old_start
    old_duration = old_end - old_start
    new_duration = max(0.0, new_end - new_start)
    revised_words: list[dict[str, Any]] = []
    for index, word in enumerate(words):
        if not isinstance(word, dict):
            continue
        word_start = safe_float(word.get("start"), None)
        word_end = safe_float(word.get("end"), None)
        if word_start is None or word_end is None or old_duration <= 0:
            count = max(1, len(words))
            start = new_start + new_duration * (index / count)
            end = new_start + new_duration * ((index + 1) / count)
        else:
            start_ratio = max(0.0, min(1.0, (word_start - old_start) / old_duration))
            end_ratio = max(start_ratio, min(1.0, (word_end - old_start) / old_duration))
            start = new_start + new_duration * start_ratio
            end = new_start + new_duration * end_ratio
        revised_words.append(
            {
                **word,
                "start": round(start, 3),
                "end": round(max(start, end), 3),
                "source": word.get("source", "fallback_word_timing"),
                "fallback_timing_adjusted": True,
            }
        )
    return revised_words


def redistribute_group(
    group: list[dict[str, Any]],
    window_start: float,
    window_end: float,
    args: OfficialWordsProcessorInput,
) -> list[dict[str, Any]]:
    available = max(0.0, window_end - window_start)
    weights = [
        max(1, int(segment.get("word_count") or len(str(segment.get("text", "")).split()) or 1))
        for segment in group
    ]
    total_weight = max(1, sum(weights))
    cursor = window_start
    revised: list[dict[str, Any]] = []
    for index, segment in enumerate(group):
        if available > 0:
            if index == len(group) - 1:
                end = window_end
            else:
                end = cursor + available * (weights[index] / total_weight)
                remaining = len(group) - index - 1
                end = min(end, window_end - remaining * args.min_word_seconds)
                end = max(cursor + args.min_word_seconds, end)
        else:
            end = cursor + args.min_word_seconds
        start = cursor
        if end <= start:
            end = start + args.min_word_seconds
        source = segment.get("source")
        if not isinstance(source, dict):
            source = {}
        updated = {
            **segment,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(max(0.0, end - start), 3),
            "source": {
                **source,
                "fallback_timing_adjusted": True,
                "fallback_timing_adjustment": "redistributed_between_word_aligned_neighbors",
                "pre_fallback_timing": {
                    "start": segment.get("start"),
                    "end": segment.get("end"),
                },
            },
        }
        scaled_words = rescale_words(segment, start, end)
        if scaled_words:
            updated["words"] = scaled_words
        revised.append(updated)
        cursor = end
    return revised


def constrain_fallback_segments(
    segments: list[dict[str, Any]],
    timing_method: str,
    args: OfficialWordsProcessorInput,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    revised: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    index = 0
    while index < len(segments):
        segment = segments[index]
        if not isinstance(segment, dict) or is_applied_word_aligned(segment, timing_method):
            revised.append(segment)
            index += 1
            continue

        group_start = index
        group: list[dict[str, Any]] = []
        while index < len(segments):
            candidate = segments[index]
            if isinstance(candidate, dict) and is_applied_word_aligned(candidate, timing_method):
                break
            if isinstance(candidate, dict):
                group.append(candidate)
            else:
                revised.append(candidate)
            index += 1

        previous_aligned = next(
            (
                item
                for item in reversed(revised)
                if isinstance(item, dict) and is_applied_word_aligned(item, timing_method)
            ),
            None,
        )
        next_aligned = next(
            (
                item
                for item in segments[index:]
                if isinstance(item, dict) and is_applied_word_aligned(item, timing_method)
            ),
            None,
        )
        if not group or previous_aligned is None or next_aligned is None:
            revised.extend(group)
            continue

        window_start = safe_float(previous_aligned.get("end"), None)
        window_end = safe_float(next_aligned.get("start"), None)
        if window_start is None or window_end is None:
            revised.extend(group)
            continue

        group_min_start = min(safe_float(item.get("start"), window_start) or window_start for item in group)
        group_max_end = max(safe_float(item.get("end"), group_min_start) or group_min_start for item in group)
        outside_window = (
            group_min_start < window_start - args.max_turn_overlap_seconds
            or group_max_end > window_end + args.max_turn_overlap_seconds
        )
        if not outside_window:
            revised.extend(group)
            continue

        adjusted = redistribute_group(group, window_start, max(window_start, window_end), args)
        revised.extend(adjusted)
        flags.extend(
            {
                "turn_index": item.get("turn_index"),
                "speaker": item.get("speaker"),
                "reason": "fallback_timing_redistributed_between_word_aligned_neighbors",
                "previous_aligned_turn_index": previous_aligned.get("turn_index"),
                "next_aligned_turn_index": next_aligned.get("turn_index"),
                "group_start_index": group_start,
            }
            for item in adjusted
        )

    return revised, flags
