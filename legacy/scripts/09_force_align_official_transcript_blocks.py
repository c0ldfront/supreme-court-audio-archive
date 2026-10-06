#!/usr/bin/env python3
"""Block-align official transcript words to audio and update timeline timings."""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


DEFAULT_ALIGNMENT_OUTPUT = Path("alignment-output")
DEFAULT_TIMELINE_OUTPUT = Path("timeline-output")
TOKEN_RE = re.compile(r"\S+")
NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
WHISPERX_LABEL_RE = re.compile(r"\.(?P<start>\d{6})-(?P<end>\d{6})\.whisperx\.json$")


@dataclass(frozen=True)
class OfficialToken:
    global_index: int
    turn_index: int
    word_index: int
    word: str
    normalized: str


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
    matches = sorted(DEFAULT_TIMELINE_OUTPUT.glob("*.case-timeline.json"))
    if not matches:
        raise FileNotFoundError("timeline JSON not found; run scripts/05_build_case_timeline.py first")
    return matches[0]


def resolve_audio(timeline: dict[str, Any], explicit: Path | None) -> Path:
    if explicit:
        if not explicit.exists():
            raise FileNotFoundError(explicit)
        return explicit
    case = timeline.get("case", {})
    audio_file = case.get("audio_file") if isinstance(case, dict) else None
    if not audio_file:
        raise RuntimeError("timeline case metadata has no audio_file")
    path = Path(str(audio_file))
    if not path.exists():
        raise FileNotFoundError(path)
    return path


def default_alignment_output(audio_path: Path) -> Path:
    return DEFAULT_ALIGNMENT_OUTPUT / f"{audio_path.stem}.official-asr-word-alignment.json"


def normalize_token(value: str) -> str:
    normalized = NORMALIZE_RE.sub("", value.casefold())
    return normalized


def text_tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(str(text or ""))


def token_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def iter_timeline_segments(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    segments = timeline.get("segments", [])
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("timeline has no segments")
    return [segment for segment in segments if isinstance(segment, dict)]


def build_blocks(
    segments: list[dict[str, Any]],
    duration: float,
    args: argparse.Namespace,
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
            tokens = [token for turn_index in turn_indexes for token in token_index_by_turn.get(turn_index, [])]
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
    args: argparse.Namespace,
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
    args: argparse.Namespace,
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    import torch
    import whisperx

    duration = float(timeline.get("duration") or 0.0)
    segments = iter_timeline_segments(timeline)
    blocks = build_blocks(segments, duration, args)
    if args.max_blocks:
        blocks = blocks[: args.max_blocks]

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"block_alignment_device: {device}", flush=True)
    print(f"block_alignment_blocks: {len(blocks)}", flush=True)
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
            aligned_words = valid_aligned_words(aligned.get("word_segments", []) if isinstance(aligned, dict) else [])
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
            print(f"block_alignment: {index}/{len(blocks)}", flush=True)

    return all_candidates, block_reports, {"device": device, "block_count": len(blocks)}


def build_turn_tokens(segments: list[dict[str, Any]]) -> dict[int, list[OfficialToken]]:
    by_turn: dict[int, list[OfficialToken]] = {}
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
        by_turn[turn_index] = tokens
    return by_turn


def resolve_whisperx_paths(timeline: dict[str, Any], cache_dir: Path) -> list[Path]:
    case = timeline.get("case", {}) if isinstance(timeline, dict) else {}
    audio_file = case.get("audio_file") if isinstance(case, dict) else None
    stem = Path(str(audio_file)).stem if audio_file else "*"
    return sorted(cache_dir.glob(f"{stem}.*.whisperx.json"))


def whisperx_path_offset(path: Path) -> tuple[float, float] | None:
    match = WHISPERX_LABEL_RE.search(path.name)
    if not match:
        return None
    return float(match.group("start")), float(match.group("end"))


def collect_asr_tokens(paths: list[Path], args: argparse.Namespace) -> list[dict[str, Any]]:
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
            data = load_json(path)
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


def flatten_official_tokens(segments: list[dict[str, Any]]) -> list[OfficialToken]:
    tokens: list[OfficialToken] = []
    global_index = 0
    for segment in segments:
        if segment.get("turn_index") is None:
            continue
        turn_index = int(segment["turn_index"])
        for word_index, word in enumerate(text_tokens(str(segment.get("text", "")))):
            normalized = normalize_token(word)
            if normalized:
                tokens.append(
                    OfficialToken(
                        global_index=global_index,
                        turn_index=turn_index,
                        word_index=word_index,
                        word=word,
                        normalized=normalized,
                    )
                )
            global_index += 1
    return tokens


def build_asr_word_candidates(
    timeline: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    segments = iter_timeline_segments(timeline)
    official_tokens = flatten_official_tokens(segments)
    paths = resolve_whisperx_paths(timeline, args.cache_dir)
    asr_tokens = collect_asr_tokens(paths, args)
    if not asr_tokens:
        raise RuntimeError("no cached WhisperX ASR tokens found; run chunk alignment first")

    print(f"asr_alignment_official_tokens: {len(official_tokens)}", flush=True)
    print(f"asr_alignment_asr_tokens: {len(asr_tokens)}", flush=True)
    matcher = SequenceMatcher(
        None,
        [token.normalized for token in official_tokens],
        [token["normalized"] for token in asr_tokens],
        autojunk=False,
    )

    candidates: dict[int, dict[str, Any]] = {}
    exact_blocks = 0
    for tag, official_start, official_end, asr_start, asr_end in matcher.get_opcodes():
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
    args: argparse.Namespace,
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
    args: argparse.Namespace,
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


def build_block_word_alignment(timeline: dict[str, Any], audio_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    segments = iter_timeline_segments(timeline)
    candidates, block_reports, runtime = build_word_candidates(timeline, audio_path, args)
    tokens_by_turn = build_turn_tokens(segments)
    turns = [
        normalize_turn_alignment(segment, tokens_by_turn.get(int(segment["turn_index"]), []), candidates, args)
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
        "created_at": datetime.now(timezone.utc).isoformat(),
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
            "mean_block_mapped_token_ratio": round(statistics.fmean(mapped_ratios), 4) if mapped_ratios else 0.0,
        },
        "blocks": block_reports,
        "turns": turns,
    }


def build_asr_word_alignment(timeline: dict[str, Any], audio_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    segments = iter_timeline_segments(timeline)
    candidates, asr_summary = build_asr_word_candidates(timeline, args)
    tokens_by_turn = build_turn_tokens(segments)
    turns = [
        normalize_turn_alignment(segment, tokens_by_turn.get(int(segment["turn_index"]), []), candidates, args)
        for segment in segments
        if segment.get("turn_index") is not None
    ]

    status_counts: dict[str, int] = {}
    for turn in turns:
        status = str(turn.get("status"))
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
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


def build_word_alignment(timeline: dict[str, Any], audio_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    if args.method == "block":
        return build_block_word_alignment(timeline, audio_path, args)
    return build_asr_word_alignment(timeline, audio_path, args)


def apply_word_alignment(timeline: dict[str, Any], alignment: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
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
        elif alignment_score is not None and float(alignment_score) < args.min_apply_mean_word_score and not args.apply_low_quality:
            reject_reason = "word_alignment_low_apply_score"
        elif previous_applied_end is not None and float(aligned["start"]) < previous_applied_end - args.max_turn_overlap_seconds:
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

    updated_segments, fallback_adjustments = constrain_fallback_segments(updated_segments, timing_method, args)
    qa_flags.extend(fallback_adjustments)
    timeline["segments"] = updated_segments
    timeline["created_at"] = datetime.now(timezone.utc).isoformat()
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
    return isinstance(source, dict) and source.get("timing") == timing_method and source.get("word_alignment_status") == "aligned"


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
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    available = max(0.0, window_end - window_start)
    weights = [max(1, int(segment.get("word_count") or len(str(segment.get("text", "")).split()) or 1)) for segment in group]
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
    args: argparse.Namespace,
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

        previous_aligned = next((item for item in reversed(revised) if isinstance(item, dict) and is_applied_word_aligned(item, timing_method)), None)
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


def run(args: argparse.Namespace) -> tuple[Path, Path]:
    timeline_path = resolve_timeline(args.timeline)
    args.timeline = timeline_path
    timeline = load_json(timeline_path)
    if not isinstance(timeline, dict):
        raise RuntimeError(f"timeline must be a JSON object: {timeline_path}")
    audio_path = resolve_audio(timeline, args.audio)
    args.output_alignment = args.output_alignment or default_alignment_output(audio_path)

    if args.output_alignment.exists() and not args.overwrite:
        alignment = load_json(args.output_alignment)
    else:
        alignment = build_word_alignment(timeline, audio_path, args)
        write_json(args.output_alignment, alignment)

    refined = apply_word_alignment(timeline, alignment, args)
    output_timeline = args.output_timeline or timeline_path
    write_json(output_timeline, refined)
    return args.output_alignment, output_timeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Block-align official transcript words to audio.")
    parser.add_argument("--timeline", type=Path, help="timeline JSON to refine")
    parser.add_argument("--audio", type=Path, help="MP3 path; defaults to timeline case audio_file")
    parser.add_argument("--output-alignment", type=Path, help="official block word alignment JSON output")
    parser.add_argument("--output-timeline", type=Path, help="timeline output; defaults to overwriting --timeline")
    parser.add_argument("--method", choices=["asr", "block"], default="asr")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_ALIGNMENT_OUTPUT / "cache")
    parser.add_argument("--min-asr-chunk-seconds", type=float, default=120.0)
    parser.add_argument("--language", default="en")
    parser.add_argument("--align-model", help="optional WhisperX/torchaudio alignment model name")
    parser.add_argument("--device", choices=["cuda", "cpu"], help="alignment device")
    parser.add_argument("--block-seconds", type=float, default=180.0)
    parser.add_argument("--overlap-seconds", type=float, default=45.0)
    parser.add_argument("--pad-seconds", type=float, default=12.0)
    parser.add_argument("--min-timed-word-ratio", type=float, default=0.45)
    parser.add_argument("--min-mean-word-score", type=float, default=0.35)
    parser.add_argument("--min-apply-mean-word-score", type=float, default=0.35)
    parser.add_argument("--min-word-seconds", type=float, default=0.08)
    parser.add_argument("--long-interpolation-min-words", type=int, default=12)
    parser.add_argument("--long-interpolation-words-per-second", type=float, default=3.0)
    parser.add_argument("--max-turn-overlap-seconds", type=float, default=1.0)
    parser.add_argument("--max-token-scan", type=int, default=12)
    parser.add_argument("--min-token-match-ratio", type=float, default=0.78)
    parser.add_argument("--interpolate-method", default="nearest", choices=["nearest", "linear", "ignore"])
    parser.add_argument("--progress-every", type=int, default=5)
    parser.add_argument("--max-blocks", type=int, help="debug/testing limit")
    parser.add_argument("--apply-low-quality", action="store_true", help="also apply low-score/low-coverage turns")
    parser.add_argument("--overwrite", action="store_true", help="rebuild existing official block word alignment output")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.progress_every < 1:
        raise SystemExit("error: --progress-every must be at least 1")
    if args.block_seconds <= 0:
        raise SystemExit("error: --block-seconds must be positive")
    if args.overlap_seconds < 0:
        raise SystemExit("error: --overlap-seconds cannot be negative")
    alignment_path, timeline_path = run(args)
    alignment = load_json(alignment_path)
    print(f"official_word_alignment: {alignment_path}")
    print(f"timeline: {timeline_path}")
    print(f"turns: {alignment['summary']['turns']}")
    print(f"statuses: {alignment['summary']['status_counts']}")
    if "block_status_counts" in alignment["summary"]:
        print(f"blocks: {alignment['summary']['block_status_counts']}")
    if "matched_word_ratio" in alignment["summary"]:
        print(f"matched_word_ratio: {alignment['summary']['matched_word_ratio']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
