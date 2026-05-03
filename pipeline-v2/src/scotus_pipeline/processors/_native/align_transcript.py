#!/usr/bin/env python3
"""Align official SCOTUS transcript turns to audio over a testable range."""

from __future__ import annotations

import argparse
import difflib
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
DEFAULT_OUTPUT = Path("alignment-output")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".part")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


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


def clip_audio(audio_path: Path, output_dir: Path, start: float, duration: float | None, overwrite: bool) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required but was not found on PATH")

    output_dir.mkdir(parents=True, exist_ok=True)
    end_label = "full" if duration is None else f"{int(start + duration):06d}"
    clip_path = output_dir / f"{audio_path.stem}_{int(start):06d}_{end_label}.wav"
    if clip_path.exists() and not overwrite:
        return clip_path

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y" if overwrite else "-n",
        "-ss",
        str(start),
        "-i",
        str(audio_path),
    ]
    if duration is not None:
        command.extend(["-t", str(duration)])
    command.extend(["-ac", "1", "-ar", "16000", str(clip_path)])
    subprocess.run(command, check=True)
    return clip_path


def whisperx_has_word_timings(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    segments = data.get("segments", []) if isinstance(data, dict) else []
    if not isinstance(segments, list):
        return False
    return any(isinstance(segment, dict) and segment.get("words") for segment in segments)


def run_whisperx(
    clip_path: Path,
    output_path: Path,
    model_size: str,
    overwrite: bool,
    align_words: bool,
) -> Path:
    if output_path.exists() and not overwrite and (not align_words or whisperx_has_word_timings(output_path)):
        return output_path

    import torch
    import whisperx

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    model = whisperx.load_model(model_size, device=device, compute_type=compute_type, language="en")
    result = model.transcribe(str(clip_path), batch_size=8, language="en")
    if align_words and result.get("segments"):
        align_model, metadata = whisperx.load_align_model(language_code="en", device=device)
        audio = whisperx.load_audio(str(clip_path))
        result = whisperx.align(
            result["segments"],
            align_model,
            metadata,
            audio,
            device,
            return_char_alignments=False,
        )
    write_json(output_path, result)
    return output_path


def normalize_text(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"\bs[\.\s-]*b[\.\s-]*8\b", "sb 8", value)
    value = re.sub(r"\bsba\b", "sb 8", value)
    value = re.sub(r"\bcalais\b", "callais", value)
    value = re.sub(r"\bcouncil\b", "counsel", value)
    value = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def tokens(value: str) -> list[str]:
    return normalize_text(value).split()


def score_candidate(turn_text: str, candidate_text: str) -> dict[str, float]:
    turn_norm = normalize_text(turn_text)
    cand_norm = normalize_text(candidate_text)
    if not cand_norm:
        return {"score": 0.0, "full_ratio": 0.0, "prefix_ratio": 0.0, "coverage": 0.0}

    full_ratio = difflib.SequenceMatcher(None, turn_norm, cand_norm).ratio()
    prefix = turn_norm[: max(len(cand_norm), 1)]
    prefix_ratio = difflib.SequenceMatcher(None, prefix, cand_norm).ratio()
    turn_words = tokens(turn_text)
    cand_words = tokens(candidate_text)
    coverage = min(1.0, len(cand_words) / max(1, len(turn_words)))
    token_precision, token_recall, token_f1 = ordered_token_scores(turn_words, cand_words)
    score = max(full_ratio, prefix_ratio, token_f1)
    return {
        "score": round(score, 4),
        "full_ratio": round(full_ratio, 4),
        "prefix_ratio": round(prefix_ratio, 4),
        "coverage": round(coverage, 4),
        "token_precision": round(token_precision, 4),
        "token_recall": round(token_recall, 4),
        "token_f1": round(token_f1, 4),
    }


def ordered_token_scores(turn_words: list[str], candidate_words: list[str]) -> tuple[float, float, float]:
    if not turn_words or not candidate_words:
        return 0.0, 0.0, 0.0
    matcher = difflib.SequenceMatcher(None, turn_words, candidate_words, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    precision = matched / max(1, len(candidate_words))
    recall = matched / max(1, len(turn_words))
    f1 = 0.0 if precision + recall == 0 else (2 * precision * recall) / (precision + recall)
    return precision, recall, f1


def choose_status(score: dict[str, float], consumed_all_segments: bool) -> str:
    if score["token_f1"] >= 0.82 and score["token_recall"] >= 0.8:
        return "matched"
    if score["score"] >= 0.82 and score["coverage"] < 0.8 and consumed_all_segments:
        return "partial_clip"
    if score["score"] >= 0.65 and (
        score.get("token_f1", 0.0) >= 0.35
        or score.get("token_precision", 0.0) >= 0.35
        or score.get("token_recall", 0.0) >= 0.35
    ):
        return "low_confidence"
    return "unmatched"


def flatten_whisperx_tokens(
    whisperx_segments: list[dict[str, Any]],
    absolute_offset: float,
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for segment_index, segment in enumerate(whisperx_segments):
        word_entries = segment.get("words")
        if isinstance(word_entries, list) and word_entries:
            for token_index, word in enumerate(word_entries):
                if not isinstance(word, dict):
                    continue
                token_values = tokens(str(word.get("word", "")))
                if not token_values:
                    continue
                start = safe_float(word.get("start"))
                end = safe_float(word.get("end"))
                if start is None or end is None:
                    continue
                if end < start:
                    end = start
                token_duration = max(0.001, (end - start) / len(token_values))
                for split_index, token in enumerate(token_values):
                    flattened.append(
                        {
                            "token": token,
                            "segment_index": segment_index,
                            "token_index": token_index,
                            "start": absolute_offset + start + token_duration * split_index,
                            "end": absolute_offset + start + token_duration * (split_index + 1),
                            "source": "whisperx_word_alignment",
                        }
                    )
            if flattened and int(flattened[-1]["segment_index"]) == segment_index:
                continue

        segment_tokens = tokens(str(segment.get("text", "")))
        if not segment_tokens:
            continue

        segment_start = absolute_offset + float(segment.get("start", 0.0))
        segment_end = absolute_offset + float(segment.get("end", segment_start - absolute_offset))
        segment_duration = max(0.0, segment_end - segment_start)
        token_duration = segment_duration / len(segment_tokens) if segment_tokens else 0.0

        for token_index, token in enumerate(segment_tokens):
            start = segment_start + token_duration * token_index
            end = segment_start + token_duration * (token_index + 1)
            flattened.append(
                {
                    "token": token,
                    "segment_index": segment_index,
                    "token_index": token_index,
                    "start": start,
                    "end": end,
                    "source": "whisperx_segment_interpolation",
                }
            )
    return flattened


def token_search_limit(
    flattened_tokens: list[dict[str, Any]],
    cursor: int,
    max_segments_per_turn: int,
) -> int:
    if cursor >= len(flattened_tokens):
        return len(flattened_tokens)

    start_segment = int(flattened_tokens[cursor]["segment_index"])
    max_segment = start_segment + max_segments_per_turn
    limit = cursor
    while limit < len(flattened_tokens) and int(flattened_tokens[limit]["segment_index"]) < max_segment:
        limit += 1
    return max(limit, min(len(flattened_tokens), cursor + 1))


def token_span_lengths(turn_word_count: int, remaining_tokens: int) -> list[int]:
    if turn_word_count <= 3:
        values = range(1, turn_word_count + 9)
    elif turn_word_count <= 12:
        values = [
            max(1, int(turn_word_count * 0.45)),
            max(1, int(turn_word_count * 0.7)),
            turn_word_count,
            turn_word_count + 4,
            turn_word_count + 8,
            turn_word_count + 12,
        ]
    else:
        values = [
            max(1, int(turn_word_count * 0.65)),
            max(1, int(turn_word_count * 0.8)),
            turn_word_count,
            int(turn_word_count * 1.15),
            int(turn_word_count * 1.3) + 10,
        ]
    lengths = sorted({length for length in values if 1 <= length <= remaining_tokens})
    return lengths or ([remaining_tokens] if remaining_tokens > 0 else [])


def score_token_span(
    turn_text: str,
    flattened_tokens: list[dict[str, Any]],
    start: int,
    end: int,
) -> dict[str, float]:
    candidate_text = " ".join(token["token"] for token in flattened_tokens[start:end])
    return score_candidate(turn_text, candidate_text)


def find_best_token_span(
    turn_text: str,
    flattened_tokens: list[dict[str, Any]],
    cursor: int,
    max_segments_per_turn: int,
) -> dict[str, Any] | None:
    if cursor >= len(flattened_tokens):
        return None

    turn_words = tokens(turn_text)
    if not turn_words:
        return None

    limit = token_search_limit(flattened_tokens, cursor, max_segments_per_turn)
    max_start = min(limit, cursor + max(12, min(80, len(turn_words) * 2)))
    best: dict[str, Any] | None = None

    for start in range(cursor, max_start):
        remaining = limit - start
        if remaining <= 0:
            break

        for length in token_span_lengths(len(turn_words), remaining):
            end = start + length
            score = score_token_span(turn_text, flattened_tokens, start, end)
            skip_penalty = min(0.15, (start - cursor) * 0.003)
            adjusted_score = score["score"] - skip_penalty
            candidate = {
                "start": start,
                "end": end,
                "score": score,
                "adjusted_score": adjusted_score,
            }
            if best is None or is_better_token_candidate(candidate, best):
                best = candidate

    return best


def is_better_token_candidate(candidate: dict[str, Any], best: dict[str, Any]) -> bool:
    candidate_score = candidate["score"]
    best_score = best["score"]
    if candidate["adjusted_score"] > best["adjusted_score"] + 0.02:
        return True
    near_tie = candidate["adjusted_score"] >= best["adjusted_score"] - 0.02
    if near_tie and candidate_score["token_recall"] > best_score["token_recall"] + 0.03:
        return True
    if near_tie and candidate_score["token_precision"] > best_score["token_precision"] + 0.03:
        return True
    if near_tie:
        candidate_skip = candidate["start"]
        best_skip = best["start"]
        if candidate_skip < best_skip:
            return True
    return False


def matching_suffix_prefix(left_tokens: list[str], right_tokens: list[str], max_len: int = 8) -> int:
    limit = min(len(left_tokens), len(right_tokens), max_len)
    for size in range(limit, 0, -1):
        if left_tokens[-size:] == right_tokens[:size]:
            return size
    return 0


def matching_prefix_suffix(left_tokens: list[str], right_tokens: list[str], max_len: int = 8) -> int:
    limit = min(len(left_tokens), len(right_tokens), max_len)
    for size in range(limit, 0, -1):
        if right_tokens[:size] == left_tokens[-size:]:
            return size
    return 0


def rebuild_alignment_from_token_span(
    aligned_turn: dict[str, Any],
    flattened_tokens: list[dict[str, Any]],
    start_token: int,
    end_token: int,
    status: str | None = None,
) -> None:
    start_token = max(0, min(start_token, len(flattened_tokens) - 1))
    end_token = max(start_token, min(end_token, len(flattened_tokens) - 1))
    first_token = flattened_tokens[start_token]
    last_token = flattened_tokens[end_token]
    candidate_text = " ".join(token["token"] for token in flattened_tokens[start_token : end_token + 1])
    score = score_candidate(str(aligned_turn.get("text", "")), candidate_text)

    aligned_turn["start"] = round(float(first_token["start"]), 3)
    aligned_turn["end"] = round(float(last_token["end"]), 3)
    aligned_turn["duration"] = round(max(0.0, float(last_token["end"]) - float(first_token["start"])), 3)
    aligned_turn["confidence"] = score["score"]
    if status:
        aligned_turn["status"] = status
    else:
        aligned_turn["status"] = choose_status(score, end_token >= len(flattened_tokens) - 1)
    aligned_turn["match"] = {
        "whisperx_start_segment": first_token["segment_index"],
        "whisperx_end_segment": last_token["segment_index"],
        "whisperx_start_token": start_token,
        "whisperx_end_token": end_token,
        "whisperx_text": candidate_text,
        **score,
    }


def refine_adjacent_boundaries(
    aligned: list[dict[str, Any]],
    flattened_tokens: list[dict[str, Any]],
) -> None:
    for previous, current in zip(aligned, aligned[1:]):
        previous_match = previous.get("match")
        current_match = current.get("match")
        if not isinstance(previous_match, dict) or not isinstance(current_match, dict):
            continue

        previous_start = int(previous_match["whisperx_start_token"])
        previous_end = int(previous_match["whisperx_end_token"])
        current_start = int(current_match["whisperx_start_token"])
        current_end = int(current_match["whisperx_end_token"])
        if previous_end >= current_start:
            continue

        previous_match_tokens = [token["token"] for token in flattened_tokens[previous_start : previous_end + 1]]
        current_match_tokens = [token["token"] for token in flattened_tokens[current_start : current_end + 1]]
        previous_turn_tokens = tokens(str(previous.get("text", "")))
        current_turn_tokens = tokens(str(current.get("text", "")))

        stolen_from_current = matching_suffix_prefix(previous_match_tokens, current_turn_tokens)
        if stolen_from_current >= 2:
            new_current_start = previous_end - stolen_from_current + 1
            new_previous_end = new_current_start - 1
            if new_previous_end >= previous_start:
                rebuild_alignment_from_token_span(previous, flattened_tokens, previous_start, new_previous_end)
            rebuild_alignment_from_token_span(current, flattened_tokens, new_current_start, current_end)
            current_start = new_current_start
            previous_end = new_previous_end
            current_match_tokens = [token["token"] for token in flattened_tokens[current_start : current_end + 1]]

        stolen_from_previous = matching_prefix_suffix(previous_turn_tokens, current_match_tokens)
        if stolen_from_previous >= 1:
            new_previous_end = current_start + stolen_from_previous - 1
            new_current_start = new_previous_end + 1
            if new_previous_end > previous_end and new_current_start <= current_end:
                rebuild_alignment_from_token_span(previous, flattened_tokens, previous_start, new_previous_end)
                rebuild_alignment_from_token_span(current, flattened_tokens, new_current_start, current_end)


def align_turns(
    turns: list[dict[str, Any]],
    whisperx_segments: list[dict[str, Any]],
    absolute_offset: float,
    max_segments_per_turn: int,
    min_score: float,
) -> list[dict[str, Any]]:
    aligned: list[dict[str, Any]] = []
    flattened_tokens = flatten_whisperx_tokens(whisperx_segments, absolute_offset)
    token_cursor = 0

    for turn in turns:
        if token_cursor >= len(flattened_tokens):
            aligned.append(
                {
                    "turn_index": turn["index"],
                    "speaker": turn["speaker"],
                    "text": turn["text"],
                    "start": None,
                    "end": None,
                    "status": "no_audio",
                    "confidence": 0.0,
                    "match": None,
                }
            )
            continue

        best = find_best_token_span(turn["text"], flattened_tokens, token_cursor, max_segments_per_turn)
        if best is None:
            aligned.append(
                {
                    "turn_index": turn["index"],
                    "speaker": turn["speaker"],
                    "text": turn["text"],
                    "start": None,
                    "end": None,
                    "status": "no_audio",
                    "confidence": 0.0,
                    "match": None,
                }
            )
            continue

        first_token = flattened_tokens[best["start"]]
        last_token = flattened_tokens[best["end"] - 1]
        consumed_all = best["end"] >= len(flattened_tokens)
        status = choose_status(best["score"], consumed_all)
        if best["score"]["score"] < min_score:
            status = "unmatched"

        start = float(first_token["start"])
        end = float(last_token["end"])
        candidate_text = " ".join(token["token"] for token in flattened_tokens[best["start"] : best["end"]])
        aligned.append(
            {
                "turn_index": turn["index"],
                "speaker": turn["speaker"],
                "text": turn["text"],
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(max(0.0, end - start), 3),
                "status": status,
                "confidence": best["score"]["score"],
                "match": {
                    "whisperx_start_segment": first_token["segment_index"],
                    "whisperx_end_segment": last_token["segment_index"],
                    "whisperx_start_token": best["start"],
                    "whisperx_end_token": best["end"] - 1,
                    "whisperx_text": candidate_text,
                    **best["score"],
                },
            }
        )

        if status != "unmatched":
            token_cursor = best["end"]

    refine_adjacent_boundaries(aligned, flattened_tokens)
    return aligned


def is_better_candidate(score: dict[str, float], best_score: dict[str, float]) -> bool:
    """Prefer materially better matches, or longer near-tie prefix matches."""
    if (
        score["token_precision"] >= 0.7
        and score["token_f1"] > best_score.get("token_f1", 0.0) + 0.05
    ):
        return True
    if score["score"] > best_score["score"] + 0.03:
        return True
    near_tie = score["score"] >= best_score["score"] - 0.03
    if near_tie and score["coverage"] > best_score["coverage"] + 0.03:
        return True
    return False


def select_turns(turns: list[dict[str, Any]], turn_start: int, turn_count: int | None) -> list[dict[str, Any]]:
    selected = turns[turn_start:]
    if turn_count is not None:
        selected = selected[:turn_count]
    return selected


def build_alignment(args: argparse.Namespace) -> Path:
    case_dir = find_case_dir(args.case_dir)
    metadata_path = args.metadata or case_dir / "metadata.json"
    metadata = load_json(metadata_path)
    audio_path = resolve_audio(metadata, case_dir, args.audio)
    speaker_turns_path = resolve_speaker_turns(audio_path, args.speaker_turns)
    all_turns = load_json(speaker_turns_path)
    if not isinstance(all_turns, list) or not all_turns:
        raise RuntimeError(f"no speaker turns found in {speaker_turns_path}")

    total_duration = audio_duration(audio_path)
    if args.full:
        start = 0.0
        duration = None
        turn_count = None
    else:
        start = args.start
        duration = args.duration
        turn_count = args.turn_count

    if duration is not None and duration <= 0:
        raise ValueError("--duration must be positive")
    if start < 0 or start >= total_duration:
        raise ValueError("--start must fall within the audio duration")

    output_dir = args.output_dir.resolve()
    cache_dir = output_dir / "cache"
    clip_path = clip_audio(audio_path, cache_dir, start, duration, args.overwrite)
    end = total_duration if duration is None else min(total_duration, start + duration)
    label = f"{int(start):06d}-{int(end):06d}"
    whisperx_path = args.whisperx or cache_dir / f"{audio_path.stem}.{label}.whisperx.json"
    run_whisperx(clip_path, whisperx_path, args.model_size, args.overwrite, align_words=not args.no_word_alignment)
    whisperx = load_json(whisperx_path)
    whisperx_segments = whisperx.get("segments", []) if isinstance(whisperx, dict) else []

    selected_turns = select_turns(all_turns, args.turn_start, turn_count)
    aligned = align_turns(
        selected_turns,
        whisperx_segments,
        absolute_offset=start,
        max_segments_per_turn=args.max_segments_per_turn,
        min_score=args.min_score,
    )
    status_counts = Counter(turn["status"] for turn in aligned)

    output_path = args.output or output_dir / f"{audio_path.stem}.align-{label}.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case": {
            "docket": metadata.get("docket"),
            "name": metadata.get("case_name"),
            "date_argued_iso": metadata.get("date_argued_iso"),
            "audio_file": str(audio_path),
            "metadata_file": str(metadata_path),
        },
        "window": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "full": bool(args.full),
        },
        "turn_window": {
            "turn_start": args.turn_start,
            "turn_count": len(selected_turns),
        },
        "sources": {
            "speaker_turns": str(speaker_turns_path),
            "clip": str(clip_path),
            "whisperx": str(whisperx_path),
        },
        "whisperx": {
            "language": whisperx.get("language") if isinstance(whisperx, dict) else None,
            "segment_count": len(whisperx_segments),
            "max_end": max((float(segment.get("end", 0.0)) for segment in whisperx_segments), default=0.0),
        },
        "alignment": {
            "method": "ordered_fuzzy_whisperx_token_span_match",
            "min_score": args.min_score,
            "max_segments_per_turn": args.max_segments_per_turn,
            "status_counts": dict(status_counts),
        },
        "aligned_turns": aligned,
    }
    write_json(output_path, payload)
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Align official transcript turns to a clipped audio range.")
    parser.add_argument("--case-dir", type=Path, help="archived case directory containing metadata.json")
    parser.add_argument("--metadata", type=Path, help="metadata.json path")
    parser.add_argument("--audio", type=Path, help="MP3 path")
    parser.add_argument("--speaker-turns", type=Path, help="speaker-turns JSON from script 02")
    parser.add_argument("--whisperx", type=Path, help="reuse an existing WhisperX JSON instead of generating one")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help="alignment output directory")
    parser.add_argument("--output", type=Path, help="alignment JSON output path")
    parser.add_argument("--start", type=float, default=0.0, help="audio start offset in seconds")
    parser.add_argument("--duration", type=float, default=300.0, help="audio duration to process in seconds")
    parser.add_argument("--full", action="store_true", help="process the full audio and all selected turns")
    parser.add_argument("--turn-start", type=int, default=0, help="first transcript turn index to align")
    parser.add_argument("--turn-count", type=int, default=40, help="number of transcript turns to align")
    parser.add_argument("--model-size", default="tiny.en", help="WhisperX model size")
    parser.add_argument("--max-segments-per-turn", type=int, default=30)
    parser.add_argument("--min-score", type=float, default=0.45)
    parser.add_argument("--overwrite", action="store_true", help="overwrite cached clip/WhisperX output")
    parser.add_argument("--no-word-alignment", action="store_true", help="skip WhisperX word alignment in the cache")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = build_alignment(args)
    data = load_json(output)
    print(f"alignment: {output}")
    print(f"case: {data['case']['docket']} {data['case']['name']}")
    print(f"window: {data['window']['start']}s - {data['window']['end']}s")
    print(f"turns: {data['turn_window']['turn_count']}")
    print(f"whisperx_segments: {data['whisperx']['segment_count']}")
    print(f"statuses: {data['alignment']['status_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
