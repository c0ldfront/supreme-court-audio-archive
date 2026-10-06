#!/usr/bin/env python3
"""Force-align official transcript words to audio and update timeline timings."""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_ALIGNMENT_OUTPUT = Path("alignment-output")
DEFAULT_TIMELINE_OUTPUT = Path("timeline-output")


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
    return DEFAULT_ALIGNMENT_OUTPUT / f"{audio_path.stem}.official-word-alignment.json"


def valid_words(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    valid: list[dict[str, Any]] = []
    for word in words:
        if not isinstance(word, dict):
            continue
        if word.get("start") is None or word.get("end") is None:
            continue
        try:
            start = float(word["start"])
            end = float(word["end"])
        except (TypeError, ValueError):
            continue
        if end < start:
            continue
        normalized = {
            "word": str(word.get("word", "")),
            "start": round(start, 3),
            "end": round(end, 3),
        }
        if word.get("score") is not None:
            try:
                normalized["score"] = round(float(word["score"]), 4)
            except (TypeError, ValueError):
                pass
        valid.append(normalized)
    return valid


def mean_score(words: list[dict[str, Any]]) -> float | None:
    scores = []
    for word in words:
        if word.get("score") is None:
            continue
        try:
            scores.append(float(word["score"]))
        except (TypeError, ValueError):
            pass
    if not scores:
        return None
    return round(statistics.fmean(scores), 4)


def expanded_window(segment: dict[str, Any], duration: float, args: argparse.Namespace) -> tuple[float, float]:
    start = float(segment.get("start", 0.0))
    end = float(segment.get("end", start))
    segment_duration = max(0.0, end - start)
    pad = max(args.pad_seconds, min(args.max_pad_seconds, segment_duration * args.relative_pad))
    padded_start = max(0.0, start - pad)
    padded_end = min(duration, end + pad)

    if padded_end - padded_start < args.min_window_seconds:
        center = (start + end) / 2
        half = args.min_window_seconds / 2
        padded_start = max(0.0, center - half)
        padded_end = min(duration, center + half)
    if padded_end <= padded_start:
        padded_end = min(duration, padded_start + args.min_window_seconds)
    return round(padded_start, 3), round(padded_end, 3)


def normalize_aligned_turn(
    segment: dict[str, Any],
    aligned_words: list[dict[str, Any]],
    window: tuple[float, float],
    args: argparse.Namespace,
) -> dict[str, Any]:
    words = valid_words(aligned_words)
    text_word_count = max(1, len(str(segment.get("text", "")).split()))
    timing_ratio = len(words) / text_word_count
    score = mean_score(words)

    result: dict[str, Any] = {
        "turn_index": segment.get("turn_index"),
        "speaker": segment.get("speaker"),
        "text": segment.get("text"),
        "status": "failed",
        "window": {"start": window[0], "end": window[1]},
        "word_count": text_word_count,
        "timed_word_count": len(words),
        "timed_word_ratio": round(timing_ratio, 4),
        "mean_word_score": score,
        "words": words,
    }

    if not words:
        result["reason"] = "no_timed_words"
        return result

    start = float(words[0]["start"])
    end = float(words[-1]["end"])
    if end <= start:
        result["reason"] = "non_positive_aligned_duration"
        return result

    if timing_ratio < args.min_timed_word_ratio:
        result["status"] = "low_coverage"
    elif score is not None and score < args.min_mean_word_score:
        result["status"] = "low_score"
    else:
        result["status"] = "aligned"

    original_start = float(segment.get("start", start))
    original_end = float(segment.get("end", end))
    result.update(
        {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "shift": {
                "start_seconds": round(start - original_start, 3),
                "end_seconds": round(end - original_end, 3),
            },
        }
    )
    return result


def build_word_alignment(timeline: dict[str, Any], audio_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    import torch
    import whisperx

    duration = float(timeline.get("duration") or 0.0)
    segments = timeline.get("segments", [])
    if not isinstance(segments, list) or not segments:
        raise RuntimeError("timeline has no segments")

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    print(f"forced_alignment_device: {device}", flush=True)
    model, metadata = whisperx.load_align_model(
        language_code=args.language,
        device=device,
        model_name=args.align_model,
    )
    audio = whisperx.load_audio(str(audio_path))

    turns: list[dict[str, Any]] = []
    total = len(segments)
    for index, segment in enumerate(segments, start=1):
        if not isinstance(segment, dict):
            continue
        text = str(segment.get("text", "")).strip()
        if not text:
            continue

        window = expanded_window(segment, duration, args)
        transcript = [{"start": window[0], "end": window[1], "text": text}]
        try:
            aligned = whisperx.align(
                transcript,
                model,
                metadata,
                audio,
                device,
                interpolate_method=args.interpolate_method,
                return_char_alignments=False,
            )
            words = aligned.get("word_segments", []) if isinstance(aligned, dict) else []
            if not isinstance(words, list):
                words = []
            turn = normalize_aligned_turn(segment, words, window, args)
        except Exception as exc:  # noqa: BLE001 - keep batch alignment moving and report the failed turn.
            turn = {
                "turn_index": segment.get("turn_index"),
                "speaker": segment.get("speaker"),
                "text": text,
                "status": "error",
                "window": {"start": window[0], "end": window[1]},
                "reason": f"{type(exc).__name__}: {exc}",
                "words": [],
            }
        turns.append(turn)
        if index == total or index % args.progress_every == 0:
            print(f"forced_alignment: {index}/{total}", flush=True)

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
            "language": args.language,
            "align_model": args.align_model,
            "device": device,
            "pad_seconds": args.pad_seconds,
            "max_pad_seconds": args.max_pad_seconds,
            "relative_pad": args.relative_pad,
            "min_window_seconds": args.min_window_seconds,
            "min_timed_word_ratio": args.min_timed_word_ratio,
            "min_mean_word_score": args.min_mean_word_score,
            "interpolate_method": args.interpolate_method,
        },
        "summary": {
            "turns": len(turns),
            "status_counts": status_counts,
        },
        "turns": turns,
    }


def apply_word_alignment(timeline: dict[str, Any], alignment: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    turns = alignment.get("turns", []) if isinstance(alignment, dict) else []
    by_index = {
        int(turn["turn_index"]): turn
        for turn in turns
        if isinstance(turn, dict) and turn.get("turn_index") is not None
    }

    updated_segments: list[dict[str, Any]] = []
    applied = 0
    qa_flags: list[dict[str, Any]] = []
    accepted_statuses = {"aligned", "low_coverage", "low_score"} if args.apply_low_quality else {"aligned"}

    for segment in timeline.get("segments", []):
        if not isinstance(segment, dict) or segment.get("turn_index") is None:
            updated_segments.append(segment)
            continue
        aligned = by_index.get(int(segment["turn_index"]))
        alignment_score = aligned.get("mean_word_score") if aligned else None
        if (
            not aligned
            or aligned.get("status") not in accepted_statuses
            or (
                alignment_score is not None
                and float(alignment_score) < args.min_apply_mean_word_score
                and not args.apply_low_quality
            )
        ):
            updated_segments.append(segment)
            if aligned:
                qa_flags.append(
                    {
                        "turn_index": segment.get("turn_index"),
                        "speaker": segment.get("speaker"),
                        "reason": (
                            "word_alignment_low_apply_score"
                            if alignment_score is not None and float(alignment_score) < args.min_apply_mean_word_score
                            else f"word_alignment_{aligned.get('status')}"
                        ),
                    }
                )
            continue

        start = float(aligned["start"])
        end = float(aligned["end"])
        source = segment.get("source", {})
        if not isinstance(source, dict):
            source = {}
        revised = {
            **segment,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(max(0.0, end - start), 3),
            "words": aligned.get("words", []),
            "source": {
                **source,
                "previous_timing": source.get("timing"),
                "timing": "official_transcript_forced_alignment",
                "word_alignment_status": aligned.get("status"),
                "word_alignment_mean_score": aligned.get("mean_word_score"),
                "word_alignment_timed_ratio": aligned.get("timed_word_ratio"),
                "word_alignment_shift": aligned.get("shift"),
            },
        }
        updated_segments.append(revised)
        applied += 1

    updated_segments, order_flags = enforce_transcript_order(updated_segments, args.min_ordered_segment_seconds)
    qa_flags.extend(order_flags)
    timeline["segments"] = updated_segments
    timeline["created_at"] = datetime.now(timezone.utc).isoformat()
    timeline.setdefault("sources", {})["official_word_alignment"] = {
        "available": True,
        "path": str(args.output_alignment),
        "applied_turn_count": applied,
        "summary": alignment.get("summary", {}),
        "qa_flags": qa_flags,
    }
    timeline.setdefault("timing", {})["method"] = "official_transcript_forced_alignment"
    timeline.setdefault("timing", {})["confidence"] = "word_aligned"
    timeline.setdefault("timing", {}).setdefault("notes", []).append(
        "Official transcript text is force-aligned to audio with WhisperX's alignment model for word-level timestamps."
    )
    return timeline


def enforce_transcript_order(
    segments: list[dict[str, Any]],
    min_segment_seconds: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    previous: dict[str, Any] | None = None

    for raw_segment in segments:
        segment = dict(raw_segment)
        if previous is None:
            ordered.append(segment)
            previous = segment
            continue

        try:
            previous_end = float(previous.get("end", 0.0))
            start = float(segment.get("start", 0.0))
            end = float(segment.get("end", start))
        except (TypeError, ValueError):
            ordered.append(segment)
            previous = segment
            continue

        if start < previous_end:
            original = {"start": round(start, 3), "end": round(end, 3)}
            start = previous_end
            if end <= start:
                end = start + min_segment_seconds
            source = segment.get("source", {})
            if not isinstance(source, dict):
                source = {}
            segment["start"] = round(start, 3)
            segment["end"] = round(end, 3)
            segment["duration"] = round(max(0.0, end - start), 3)
            segment["source"] = {
                **source,
                "transcript_order_adjustment": {
                    "reason": "prevent_turn_overlap",
                    "previous_turn_index": previous.get("turn_index"),
                    "before": original,
                },
            }
            flags.append(
                {
                    "turn_index": segment.get("turn_index"),
                    "speaker": segment.get("speaker"),
                    "reason": "prevented_turn_overlap",
                    "previous_turn_index": previous.get("turn_index"),
                }
            )

        ordered.append(segment)
        previous = segment

    return ordered, flags


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
    parser = argparse.ArgumentParser(description="Force-align official transcript words to audio.")
    parser.add_argument("--timeline", type=Path, help="timeline JSON to refine")
    parser.add_argument("--audio", type=Path, help="MP3 path; defaults to timeline case audio_file")
    parser.add_argument("--output-alignment", type=Path, help="official word alignment JSON output")
    parser.add_argument("--output-timeline", type=Path, help="timeline output; defaults to overwriting --timeline")
    parser.add_argument("--language", default="en")
    parser.add_argument("--align-model", help="optional WhisperX/torchaudio alignment model name")
    parser.add_argument("--device", choices=["cuda", "cpu"], help="alignment device")
    parser.add_argument("--pad-seconds", type=float, default=8.0)
    parser.add_argument("--max-pad-seconds", type=float, default=20.0)
    parser.add_argument("--relative-pad", type=float, default=0.35)
    parser.add_argument("--min-window-seconds", type=float, default=2.0)
    parser.add_argument("--min-timed-word-ratio", type=float, default=0.5)
    parser.add_argument("--min-mean-word-score", type=float, default=0.5)
    parser.add_argument("--min-apply-mean-word-score", type=float, default=0.5)
    parser.add_argument("--min-ordered-segment-seconds", type=float, default=0.2)
    parser.add_argument("--interpolate-method", default="nearest", choices=["nearest", "linear", "ignore"])
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--apply-low-quality", action="store_true", help="also apply low-score/low-coverage turns")
    parser.add_argument("--overwrite", action="store_true", help="rebuild existing official word alignment output")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.progress_every < 1:
        raise SystemExit("error: --progress-every must be at least 1")
    alignment_path, timeline_path = run(args)
    alignment = load_json(alignment_path)
    print(f"official_word_alignment: {alignment_path}")
    print(f"timeline: {timeline_path}")
    print(f"turns: {alignment['summary']['turns']}")
    print(f"statuses: {alignment['summary']['status_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
