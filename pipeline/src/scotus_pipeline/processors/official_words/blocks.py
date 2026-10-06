"""Align overlapping transcript blocks using a WhisperX alignment model."""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scotus_pipeline.asr.dependencies import require_asr_dependencies
from scotus_pipeline.processors.contracts import OfficialWordsProcessorInput
from scotus_pipeline.processors.official_words.progress import emit_progress
from scotus_pipeline.processors.official_words.timing import normalize_turn_alignment
from scotus_pipeline.processors.official_words.tokens import (
    OfficialToken,
    build_turn_tokens,
    iter_timeline_segments,
    normalize_token,
    safe_float,
    text_tokens,
    token_similarity,
)


@dataclass(frozen=True)
class AlignmentBlock:
    index: int
    start: float
    end: float
    align_start: float
    align_end: float
    turn_indexes: list[int]
    text: str
    tokens: list[OfficialToken]


def build_blocks(
    segments: list[dict[str, Any]],
    duration: float,
    args: OfficialWordsProcessorInput,
) -> list[AlignmentBlock]:
    step = args.block_seconds - args.overlap_seconds
    if step <= 0:
        raise ValueError("--overlap-seconds must be smaller than --block-seconds")

    token_index_by_turn: dict[int, list[OfficialToken]] = {}
    global_index = 0
    for segment in segments:
        if segment.get("turn_index") is None:
            continue
        turn_index = int(segment["turn_index"])
        tokens: list[OfficialToken] = []
        for word_index, word in enumerate(text_tokens(str(segment.get("text", "")))):
            tokens.append(
                OfficialToken(
                    global_index=global_index,
                    turn_index=turn_index,
                    word_index=word_index,
                    word=word,
                    normalized=normalize_token(word),
                )
            )
            global_index += 1
        token_index_by_turn[turn_index] = tokens

    blocks: list[AlignmentBlock] = []
    start = 0.0
    block_index = 0
    while start < duration:
        end = min(duration, start + args.block_seconds)
        selected: list[dict[str, Any]] = []
        for segment in segments:
            if segment.get("turn_index") is None:
                continue
            segment_start = safe_float(segment.get("start"), 0.0) or 0.0
            segment_end = safe_float(segment.get("end"), segment_start) or segment_start
            center = (segment_start + segment_end) / 2
            overlaps_window = segment_end > start and segment_start < end
            center_in_window = start <= center < end
            if center_in_window or overlaps_window:
                selected.append(segment)

        if selected:
            turn_indexes = [int(segment["turn_index"]) for segment in selected]
            tokens = [
                token for turn_index in turn_indexes for token in token_index_by_turn.get(turn_index, [])
            ]
            if tokens:
                text = " ".join(token.word for token in tokens)
                blocks.append(
                    AlignmentBlock(
                        index=block_index,
                        start=round(start, 3),
                        end=round(end, 3),
                        align_start=round(max(0.0, start - args.pad_seconds), 3),
                        align_end=round(min(duration, end + args.pad_seconds), 3),
                        turn_indexes=turn_indexes,
                        text=text,
                        tokens=tokens,
                    )
                )
                block_index += 1

        if end >= duration:
            break
        start += step

    return blocks


def valid_aligned_words(words: Any) -> list[dict[str, Any]]:
    if not isinstance(words, list):
        return []
    valid: list[dict[str, Any]] = []
    for word in words:
        if not isinstance(word, dict):
            continue
        start = safe_float(word.get("start"))
        end = safe_float(word.get("end"))
        if start is None or end is None or end < start:
            continue
        normalized = {
            "word": str(word.get("word", "")),
            "start": round(start, 3),
            "end": round(end, 3),
        }
        score = safe_float(word.get("score"))
        if score is not None:
            normalized["score"] = round(score, 4)
        valid.append(normalized)
    return valid


def match_aligned_words_to_tokens(
    block: AlignmentBlock,
    aligned_words: list[dict[str, Any]],
    args: OfficialWordsProcessorInput,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    candidates: dict[int, dict[str, Any]] = {}
    cursor = 0
    exact = 0
    fuzzy = 0
    fallback = 0
    unmatched = 0

    for aligned_word in aligned_words:
        aligned_norm = normalize_token(str(aligned_word.get("word", "")))
        if not aligned_norm:
            unmatched += 1
            continue

        best_index: int | None = None
        best_score = 0.0
        scan_end = min(len(block.tokens), cursor + args.max_token_scan)
        for index in range(cursor, scan_end):
            token = block.tokens[index]
            if not token.normalized:
                continue
            score = token_similarity(aligned_norm, token.normalized)
            if score > best_score:
                best_score = score
                best_index = index
            if score >= 1.0:
                break

        method = "unmatched"
        if best_index is not None and best_score >= 1.0:
            method = "exact"
            exact += 1
        elif best_index is not None and best_score >= args.min_token_match_ratio:
            method = "fuzzy"
            fuzzy += 1
        else:
            for index in range(cursor, scan_end):
                if block.tokens[index].normalized:
                    best_index = index
                    best_score = 0.0
                    method = "ordered_fallback"
                    fallback += 1
                    break

        if best_index is None:
            unmatched += 1
            continue

        token = block.tokens[best_index]
        start = float(aligned_word["start"])
        end = float(aligned_word["end"])
        midpoint = (start + end) / 2
        half = max(0.001, (block.align_end - block.align_start) / 2)
        centrality = max(0.0, min(1.0, min(midpoint - block.align_start, block.align_end - midpoint) / half))
        word_score = safe_float(aligned_word.get("score"), 0.5) or 0.5
        candidate = {
            "word": token.word,
            "aligned_word": aligned_word.get("word"),
            "start": round(start, 3),
            "end": round(end, 3),
            "score": round(word_score, 4) if word_score is not None else None,
            "block_index": block.index,
            "block_start": block.start,
            "block_end": block.end,
            "match_method": method,
            "match_ratio": round(best_score, 4),
            "rank_score": round((word_score or 0.5) + centrality * 0.1 + best_score * 0.05, 4),
            "source": "official_block_forced_alignment",
        }
        existing = candidates.get(token.global_index)
        if not existing or float(candidate["rank_score"]) > float(existing.get("rank_score", 0.0)):
            candidates[token.global_index] = candidate
        cursor = best_index + 1

    return candidates, {
        "aligned_words": len(aligned_words),
        "mapped_words": len(candidates),
        "exact_matches": exact,
        "fuzzy_matches": fuzzy,
        "fallback_matches": fallback,
        "unmatched_words": unmatched,
    }


def build_word_candidates(
    timeline: dict[str, Any],
    audio_path: Path,
    args: OfficialWordsProcessorInput,
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    require_asr_dependencies()
    import torch
    import whisperx

    duration = float(timeline.get("duration") or 0.0)
    segments = iter_timeline_segments(timeline)
    blocks = build_blocks(segments, duration, args)
    if args.max_blocks:
        blocks = blocks[: args.max_blocks]

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    emit_progress(args, 0, max(1, len(blocks)), f"block_alignment_device: {device}", device=device)
    emit_progress(args, 0, max(1, len(blocks)), f"block_alignment_blocks: {len(blocks)}")
    model, metadata = whisperx.load_align_model(
        language_code=args.language,
        device=device,
        model_name=args.align_model,
    )
    audio = whisperx.load_audio(str(audio_path))

    all_candidates: dict[int, dict[str, Any]] = {}
    block_reports: list[dict[str, Any]] = []
    for index, block in enumerate(blocks, start=1):
        report: dict[str, Any] = {
            "block_index": block.index,
            "start": block.start,
            "end": block.end,
            "align_start": block.align_start,
            "align_end": block.align_end,
            "turn_count": len(block.turn_indexes),
            "token_count": len(block.tokens),
            "status": "error",
        }
        try:
            aligned = whisperx.align(
                [{"start": block.align_start, "end": block.align_end, "text": block.text}],
                model,
                metadata,
                audio,
                device,
                interpolate_method=args.interpolate_method,
                return_char_alignments=False,
            )
            aligned_words = valid_aligned_words(
                aligned.get("word_segments", []) if isinstance(aligned, dict) else []
            )
            candidates, mapping = match_aligned_words_to_tokens(block, aligned_words, args)
            for global_index, candidate in candidates.items():
                existing = all_candidates.get(global_index)
                if not existing or float(candidate["rank_score"]) > float(existing.get("rank_score", 0.0)):
                    all_candidates[global_index] = candidate
            report.update(
                {
                    "status": "aligned",
                    "aligned_word_count": len(aligned_words),
                    "mapped_word_count": len(candidates),
                    "mapped_token_ratio": round(len(candidates) / max(1, len(block.tokens)), 4),
                    "mapping": mapping,
                }
            )
        except Exception as exc:  # noqa: BLE001 - report block failures without losing the full run.
            report["reason"] = f"{type(exc).__name__}: {exc}"

        block_reports.append(report)
        if index == len(blocks) or index % args.progress_every == 0:
            emit_progress(args, index, len(blocks), f"block_alignment: {index}/{len(blocks)}")

    return all_candidates, block_reports, {"device": device, "block_count": len(blocks)}


def build_block_word_alignment(
    timeline: dict[str, Any], audio_path: Path, args: OfficialWordsProcessorInput
) -> dict[str, Any]:
    segments = iter_timeline_segments(timeline)
    candidates, block_reports, runtime = build_word_candidates(timeline, audio_path, args)
    tokens_by_turn = build_turn_tokens(segments)
    turns = [
        normalize_turn_alignment(
            segment, tokens_by_turn.get(int(segment["turn_index"]), []), candidates, args
        )
        for segment in segments
        if segment.get("turn_index") is not None
    ]

    status_counts: dict[str, int] = {}
    for turn in turns:
        status = str(turn.get("status"))
        status_counts[status] = status_counts.get(status, 0) + 1

    mapped_blocks = [block for block in block_reports if block.get("status") == "aligned"]
    mapped_ratios = [float(block.get("mapped_token_ratio", 0.0)) for block in mapped_blocks]

    return {
        "created_at": datetime.now(UTC).isoformat(),
        "case": timeline.get("case", {}),
        "sources": {
            "timeline": str(args.timeline),
            "audio_file": str(audio_path),
        },
        "settings": {
            "method": "official_transcript_block_forced_alignment",
            "language": args.language,
            "align_model": args.align_model,
            "device": runtime["device"],
            "block_seconds": args.block_seconds,
            "overlap_seconds": args.overlap_seconds,
            "pad_seconds": args.pad_seconds,
            "min_timed_word_ratio": args.min_timed_word_ratio,
            "min_mean_word_score": args.min_mean_word_score,
            "min_apply_mean_word_score": args.min_apply_mean_word_score,
            "interpolate_method": args.interpolate_method,
            "max_token_scan": args.max_token_scan,
            "min_token_match_ratio": args.min_token_match_ratio,
        },
        "summary": {
            "turns": len(turns),
            "status_counts": status_counts,
            "blocks": len(block_reports),
            "block_status_counts": {
                "aligned": len(mapped_blocks),
                "error": sum(1 for block in block_reports if block.get("status") == "error"),
            },
            "mean_block_mapped_token_ratio": round(statistics.fmean(mapped_ratios), 4)
            if mapped_ratios
            else 0.0,
        },
        "blocks": block_reports,
        "turns": turns,
    }
