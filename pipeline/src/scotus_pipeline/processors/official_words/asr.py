"""Match official tokens to the timestamped ASR cache."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from scotus_pipeline.io import read_json
from scotus_pipeline.processors.contracts import OfficialWordsProcessorInput
from scotus_pipeline.processors.official_words.progress import emit_progress
from scotus_pipeline.processors.official_words.timing import normalize_turn_alignment
from scotus_pipeline.processors.official_words.tokens import (
    build_turn_tokens,
    flatten_official_tokens,
    iter_timeline_segments,
    normalize_token,
    safe_float,
    text_tokens,
)

WHISPERX_LABEL_RE = re.compile(r"(?P<start>\d{6})-(?P<end>\d{6})\.whisperx\.json$")


def resolve_whisperx_paths(timeline: dict[str, Any], cache_dir: Path) -> list[Path]:
    return sorted(cache_dir.glob("*.whisperx.json"))


def whisperx_path_offset(path: Path) -> tuple[float, float] | None:
    match = WHISPERX_LABEL_RE.search(path.name)
    if not match:
        return None
    return float(match.group("start")), float(match.group("end"))


def collect_asr_tokens(paths: list[Path], args: OfficialWordsProcessorInput) -> list[dict[str, Any]]:
    asr_tokens: list[dict[str, Any]] = []
    chunk_paths = []
    for path in paths:
        offset = whisperx_path_offset(path)
        if not offset:
            continue
        start_offset, end_offset = offset
        if end_offset - start_offset >= args.min_asr_chunk_seconds:
            chunk_paths.append((path, start_offset))
    if not chunk_paths:
        chunk_paths = [(path, whisperx_path_offset(path)[0]) for path in paths if whisperx_path_offset(path)]

    for path, start_offset in chunk_paths:
        try:
            data = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        segments = data.get("segments", []) if isinstance(data, dict) else []
        if not isinstance(segments, list):
            continue
        for segment_index, segment in enumerate(segments):
            if not isinstance(segment, dict):
                continue
            word_entries = segment.get("words")
            if isinstance(word_entries, list) and word_entries:
                appended = False
                for word_index, word in enumerate(word_entries):
                    if not isinstance(word, dict):
                        continue
                    split_words = text_tokens(str(word.get("word", "")))
                    if not split_words:
                        continue
                    word_start = safe_float(word.get("start"))
                    word_end = safe_float(word.get("end"))
                    if word_start is None or word_end is None:
                        continue
                    start = start_offset + word_start
                    end = start_offset + max(word_start, word_end)
                    duration = max(args.min_word_seconds * len(split_words), end - start)
                    step = duration / len(split_words)
                    for split_index, split_word in enumerate(split_words):
                        normalized = normalize_token(split_word)
                        if not normalized:
                            continue
                        asr_tokens.append(
                            {
                                "normalized": normalized,
                                "word": split_word,
                                "start": round(start + split_index * step, 3),
                                "end": round(start + (split_index + 1) * step, 3),
                                "source": str(path),
                                "segment_index": segment_index,
                                "word_index": word_index,
                                "timing_source": "whisperx_word_alignment",
                            }
                        )
                        appended = True
                if appended:
                    continue

            words = text_tokens(str(segment.get("text", "")))
            if not words:
                continue
            segment_start = start_offset + (safe_float(segment.get("start"), 0.0) or 0.0)
            segment_end = start_offset + (safe_float(segment.get("end"), segment_start - start_offset) or 0.0)
            duration = max(args.min_word_seconds * len(words), segment_end - segment_start)
            step = duration / len(words)
            for word_index, word in enumerate(words):
                normalized = normalize_token(word)
                if not normalized:
                    continue
                asr_tokens.append(
                    {
                        "normalized": normalized,
                        "word": word,
                        "start": round(segment_start + word_index * step, 3),
                        "end": round(segment_start + (word_index + 1) * step, 3),
                        "source": str(path),
                        "segment_index": segment_index,
                        "timing_source": "whisperx_segment_interpolation",
                    }
                )
    return asr_tokens


def build_asr_word_candidates(
    timeline: dict[str, Any],
    args: OfficialWordsProcessorInput,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    segments = iter_timeline_segments(timeline)
    official_tokens = flatten_official_tokens(segments)
    paths = resolve_whisperx_paths(timeline, args.cache_dir)
    asr_tokens = collect_asr_tokens(paths, args)
    if not asr_tokens:
        raise RuntimeError("no cached WhisperX ASR tokens found; run chunk alignment first")

    emit_progress(args, 0, 1, f"asr_alignment_official_tokens: {len(official_tokens)}")
    emit_progress(args, 0, 1, f"asr_alignment_asr_tokens: {len(asr_tokens)}")
    matcher = SequenceMatcher(
        None,
        [token.normalized for token in official_tokens],
        [token["normalized"] for token in asr_tokens],
        autojunk=False,
    )

    candidates: dict[int, dict[str, Any]] = {}
    exact_blocks = 0
    for tag, official_start, official_end, asr_start, _asr_end in matcher.get_opcodes():
        if tag != "equal":
            continue
        exact_blocks += 1
        for offset in range(official_end - official_start):
            official = official_tokens[official_start + offset]
            asr = asr_tokens[asr_start + offset]
            candidates[official.global_index] = {
                "word": official.word,
                "aligned_word": asr["word"],
                "start": asr["start"],
                "end": asr["end"],
                "score": 1.0,
                "match_method": "global_asr_exact",
                "match_ratio": 1.0,
                "rank_score": 1.0,
                "source": "official_asr_sequence_alignment",
                "timing_source": asr.get("timing_source", "whisperx_segment_interpolation"),
            }

    return candidates, {
        "method": "official_transcript_asr_sequence_alignment",
        "cache_dir": str(args.cache_dir),
        "whisperx_file_count": len(paths),
        "official_token_count": len(official_tokens),
        "asr_token_count": len(asr_tokens),
        "matched_word_count": len(candidates),
        "matched_word_ratio": round(len(candidates) / max(1, len(official_tokens)), 4),
        "sequence_ratio": round(matcher.ratio(), 4),
        "exact_blocks": exact_blocks,
    }


def build_asr_word_alignment(
    timeline: dict[str, Any], audio_path: Path, args: OfficialWordsProcessorInput
) -> dict[str, Any]:
    segments = iter_timeline_segments(timeline)
    candidates, asr_summary = build_asr_word_candidates(timeline, args)
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

    return {
        "created_at": datetime.now(UTC).isoformat(),
        "case": timeline.get("case", {}),
        "sources": {
            "timeline": str(args.timeline),
            "audio_file": str(audio_path),
        },
        "settings": {
            "method": "official_transcript_asr_sequence_alignment",
            "cache_dir": str(args.cache_dir),
            "min_asr_chunk_seconds": args.min_asr_chunk_seconds,
            "min_timed_word_ratio": args.min_timed_word_ratio,
            "min_mean_word_score": args.min_mean_word_score,
            "min_apply_mean_word_score": args.min_apply_mean_word_score,
        },
        "summary": {
            "turns": len(turns),
            "status_counts": status_counts,
            **asr_summary,
        },
        "turns": turns,
    }
