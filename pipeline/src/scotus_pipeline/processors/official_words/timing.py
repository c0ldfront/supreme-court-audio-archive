"""Score word matches and interpolate missing timestamps."""

from __future__ import annotations

import statistics
from typing import Any

from scotus_pipeline.processors.contracts import OfficialWordsProcessorInput
from scotus_pipeline.processors.official_words.tokens import OfficialToken, safe_float


def mean_score(words: list[dict[str, Any]]) -> float | None:
    scores = [safe_float(word.get("score")) for word in words]
    clean_scores = [score for score in scores if score is not None]
    if not clean_scores:
        return None
    return round(statistics.fmean(clean_scores), 4)


def interpolate_missing_words(
    tokens: list[OfficialToken],
    raw_words: list[dict[str, Any] | None],
    segment: dict[str, Any],
    args: OfficialWordsProcessorInput,
) -> list[dict[str, Any]]:
    if not tokens:
        return []

    start = safe_float(segment.get("start"), 0.0) or 0.0
    end = safe_float(segment.get("end"), start) or start
    if end <= start:
        end = start + args.min_word_seconds * len(tokens)

    words: list[dict[str, Any] | None] = list(raw_words)
    known = [index for index, word in enumerate(words) if word is not None]
    if not known:
        duration = max(args.min_word_seconds * len(tokens), end - start)
        cursor = start
        step = duration / len(tokens)
        return [
            {
                "word": token.word,
                "start": round(cursor + index * step, 3),
                "end": round(cursor + (index + 1) * step, 3),
                "score": None,
                "interpolated": True,
                "source": "official_block_word_interpolation",
            }
            for index, token in enumerate(tokens)
        ]

    first = known[0]
    first_start = float(words[first]["start"])  # type: ignore[index]
    leading_step = args.min_word_seconds
    if first >= args.long_interpolation_min_words:
        leading_step = max(args.min_word_seconds, 1.0 / args.long_interpolation_words_per_second)
    leading_start = max(0.0, first_start - leading_step * first)
    for index in range(first - 1, -1, -1):
        word_start = leading_start + leading_step * index
        word_end = leading_start + leading_step * (index + 1)
        words[index] = {
            "word": tokens[index].word,
            "start": round(word_start, 3),
            "end": round(word_end, 3),
            "score": None,
            "interpolated": True,
            "source": "official_block_word_interpolation",
        }

    for left, right in zip(known, known[1:]):
        left_end = float(words[left]["end"])  # type: ignore[index]
        right_start = float(words[right]["start"])  # type: ignore[index]
        gap_count = right - left - 1
        if gap_count <= 0:
            continue
        available = max(args.min_word_seconds * gap_count, right_start - left_end)
        step = available / gap_count
        cursor = left_end
        for offset in range(gap_count):
            index = left + 1 + offset
            words[index] = {
                "word": tokens[index].word,
                "start": round(cursor, 3),
                "end": round(cursor + step, 3),
                "score": None,
                "interpolated": True,
                "source": "official_block_word_interpolation",
            }
            cursor += step

    last = known[-1]
    last_end = float(words[last]["end"])  # type: ignore[index]
    for index in range(last + 1, len(tokens)):
        word_start = last_end + (index - last - 1) * args.min_word_seconds
        words[index] = {
            "word": tokens[index].word,
            "start": round(word_start, 3),
            "end": round(word_start + args.min_word_seconds, 3),
            "score": None,
            "interpolated": True,
            "source": "official_block_word_interpolation",
        }

    return [word for word in words if word is not None]


def normalize_turn_alignment(
    segment: dict[str, Any],
    tokens: list[OfficialToken],
    candidates: dict[int, dict[str, Any]],
    args: OfficialWordsProcessorInput,
) -> dict[str, Any]:
    raw_words: list[dict[str, Any] | None] = []
    timed_words = 0
    for token in tokens:
        candidate = candidates.get(token.global_index)
        if candidate:
            timed_words += 1
            raw_words.append(
                {
                    "word": token.word,
                    "start": candidate["start"],
                    "end": candidate["end"],
                    "score": candidate.get("score"),
                    "source": candidate.get("source"),
                    "block_index": candidate.get("block_index"),
                    "match_method": candidate.get("match_method"),
                    "match_ratio": candidate.get("match_ratio"),
                    "timing_source": candidate.get("timing_source"),
                }
            )
        else:
            raw_words.append(None)

    words = interpolate_missing_words(tokens, raw_words, segment, args)
    raw_scored_words = [word for word in words if not word.get("interpolated")]
    score = mean_score(raw_scored_words)
    timed_ratio = timed_words / max(1, len(tokens))
    status = "failed"
    reason = None
    if timed_words == 0:
        reason = "no_timed_words"
    elif timed_ratio < args.min_timed_word_ratio:
        status = "low_coverage"
    elif score is not None and score < args.min_mean_word_score:
        status = "low_score"
    else:
        status = "aligned"

    if not words:
        start = safe_float(segment.get("start"), 0.0) or 0.0
        end = safe_float(segment.get("end"), start) or start
    else:
        start = float(words[0]["start"])
        end = float(words[-1]["end"])
        if end <= start:
            end = start + args.min_word_seconds

    original_start = safe_float(segment.get("start"), start) or start
    original_end = safe_float(segment.get("end"), end) or end
    result: dict[str, Any] = {
        "turn_index": segment.get("turn_index"),
        "speaker": segment.get("speaker"),
        "text": segment.get("text"),
        "status": status,
        "word_count": len(tokens),
        "timed_word_count": timed_words,
        "timed_word_ratio": round(timed_ratio, 4),
        "mean_word_score": score,
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(max(0.0, end - start), 3),
        "shift": {
            "start_seconds": round(start - original_start, 3),
            "end_seconds": round(end - original_end, 3),
        },
        "words": words,
    }
    if reason:
        result["reason"] = reason
    return result
