#!/usr/bin/env python3
"""Run transcript/audio alignment in cacheable chunks."""

from __future__ import annotations

import json
import shutil
import subprocess
import traceback
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scotus_pipeline.asr.client import AsrWorkerClient
from scotus_pipeline.asr.types import WorkerRequest
from scotus_pipeline.io import atomic_write_json, sha256_file
from scotus_pipeline.processors import align_transcript
from scotus_pipeline.processors.contracts import AlignmentChunksProcessorInput, AlignTranscriptProcessorInput

GOOD_STATUSES = {"matched", "partial_clip", "low_confidence"}
BAD_STATUSES = {"unmatched", "no_audio"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload)


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


def resolve_timeline(audio_path: Path, explicit: Path | None) -> Path:
    if explicit:
        if not explicit.exists():
            raise FileNotFoundError(explicit)
        return explicit
    raise ValueError(f"timeline is required for audio {audio_path}")


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


def chunk_windows(duration: float, chunk_seconds: float, max_chunks: int | None) -> list[tuple[float, float]]:
    if chunk_seconds <= 0:
        raise ValueError("--chunk-seconds must be positive")

    windows: list[tuple[float, float]] = []
    start = 0.0
    while start < duration:
        end = min(duration, start + chunk_seconds)
        windows.append((start, end))
        if max_chunks is not None and len(windows) >= max_chunks:
            break
        start = end
    return windows


def segment_overlaps_window(segment: dict[str, Any], start: float, end: float) -> bool:
    segment_start = float(segment.get("start", 0.0))
    segment_end = float(segment.get("end", segment_start))
    return segment_end > start and segment_start < end


def turns_for_window(
    segments: list[dict[str, Any]],
    start: float,
    end: float,
    padding: int,
) -> tuple[int, int, list[int]]:
    overlapping = [segment for segment in segments if segment_overlaps_window(segment, start, end)]
    if not overlapping:
        return 0, 0, []

    indexes = sorted(int(segment["turn_index"]) for segment in overlapping)
    first = max(0, indexes[0] - padding)
    last = min(max(int(segment["turn_index"]) for segment in segments), indexes[-1] + padding)
    selected = list(range(first, last + 1))
    return first, len(selected), selected


def alignment_output_path(output_dir: Path, start: float, end: float) -> Path:
    label = f"{int(start):06d}-{int(end):06d}"
    return output_dir / "chunks" / f"{label}.json"


def chunk_cache_key(
    args: AlignmentChunksProcessorInput, audio_path: Path, speaker_turns_path: Path | None
) -> dict[str, Any]:
    return {
        "audio_sha256": sha256_file(audio_path) if audio_path.exists() else None,
        "speaker_turns_sha256": (
            sha256_file(speaker_turns_path)
            if speaker_turns_path is not None and speaker_turns_path.exists()
            else None
        ),
        "model_size": args.model_size,
        "chunk_seconds": args.chunk_seconds,
        "max_segments_per_turn": args.max_segments_per_turn,
        "min_score": args.min_score,
    }


def run_chunk(
    args: AlignmentChunksProcessorInput,
    case_dir: Path,
    audio_path: Path,
    start: float,
    end: float,
    turn_start: int,
    turn_count: int,
    output_path: Path,
    client: AsrWorkerClient | None = None,
) -> dict[str, Any]:
    try:
        if client is not None:
            client.request(
                WorkerRequest(
                    request_id=f"{int(start):06d}-{int(end):06d}",
                    case_dir=case_dir,
                    metadata=args.metadata,
                    audio=audio_path,
                    audio_sha256=sha256_file(audio_path),
                    speaker_turns=args.speaker_turns,
                    output_dir=args.output_dir,
                    cache_dir=args.cache_dir,
                    output=output_path,
                    start=start,
                    duration=end - start,
                    turn_start=turn_start,
                    turn_count=turn_count,
                    model_size=args.model_size,
                    max_segments_per_turn=args.max_segments_per_turn,
                    min_score=args.min_score,
                    overwrite=args.overwrite,
                    no_word_alignment=False,
                )
            )
        else:
            align_transcript.build_alignment(
                AlignTranscriptProcessorInput(
                    case_dir=case_dir,
                    metadata=args.metadata,
                    audio=audio_path,
                    speaker_turns=args.speaker_turns,
                    whisperx=None,
                    output_dir=args.output_dir,
                    cache_dir=args.cache_dir,
                    output=output_path,
                    start=start,
                    duration=end - start,
                    full=False,
                    turn_start=turn_start,
                    turn_count=turn_count,
                    model_size=args.model_size,
                    max_segments_per_turn=args.max_segments_per_turn,
                    min_score=args.min_score,
                    overwrite=args.overwrite,
                    no_word_alignment=False,
                )
            )
    except Exception:
        return {"returncode": 1, "stdout": [], "stderr": traceback.format_exc().splitlines()}
    return {"returncode": 0, "stdout": [f"alignment: {output_path}"], "stderr": []}


def validate_existing_chunk(
    path: Path,
    args: AlignmentChunksProcessorInput,
    audio_path: Path,
    start: float,
    end: float,
    turn_start: int,
    turn_count: int,
    speaker_turns_path: Path | None,
) -> bool:
    if not path.exists():
        return False
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False

    window = data.get("window", {})
    turn_window = data.get("turn_window", {})
    alignment = data.get("alignment", {})
    aligned_turns = data.get("aligned_turns", [])
    if not isinstance(window, dict) or not isinstance(turn_window, dict):
        return False
    if not isinstance(alignment, dict) or not isinstance(aligned_turns, list):
        return False
    if not isinstance(alignment.get("status_counts"), dict):
        return False
    expected_cache_key = chunk_cache_key(args, audio_path, speaker_turns_path)
    if data.get("cache_key") != expected_cache_key:
        return False
    try:
        output_start = float(window.get("start"))
        output_end = float(window.get("end"))
        output_turn_start = int(turn_window.get("turn_start"))
        output_turn_count = int(turn_window.get("turn_count"))
    except (TypeError, ValueError):
        return False

    if not (
        abs(output_start - start) < 0.01
        and abs(output_end - end) < 0.01
        and output_turn_start == turn_start
        and output_turn_count == turn_count
    ):
        return False

    label = f"{int(start):06d}-{int(end):06d}"
    whisperx_path = args.cache_dir / f"{label}.whisperx.json"
    return whisperx_cache_has_word_timings(whisperx_path)


def whisperx_cache_has_word_timings(path: Path) -> bool:
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


def summarize_alignment(path: Path, max_bad_ratio: float) -> dict[str, Any]:
    data = load_json(path)
    aligned_turns = data.get("aligned_turns", []) if isinstance(data, dict) else []
    status_counts = Counter(str(turn.get("status")) for turn in aligned_turns)
    total = sum(status_counts.values())
    bad = sum(status_counts.get(status, 0) for status in BAD_STATUSES)
    good = sum(status_counts.get(status, 0) for status in GOOD_STATUSES)
    bad_ratio = bad / total if total else 1.0
    quality = "warning" if bad_ratio >= max_bad_ratio else "ok"

    return {
        "output": str(path),
        "turn_count": total,
        "status_counts": dict(status_counts),
        "good_count": good,
        "bad_count": bad,
        "bad_ratio": round(bad_ratio, 4),
        "quality": quality,
    }


def build_report_payload(
    args: AlignmentChunksProcessorInput,
    metadata: dict[str, Any],
    metadata_path: Path,
    audio_path: Path,
    timeline_path: Path,
    windows: list[tuple[float, float]],
    chunks: list[dict[str, Any]],
) -> dict[str, Any]:
    quality_counts = Counter(str(chunk.get("quality")) for chunk in chunks)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "case": {
            "docket": metadata.get("docket"),
            "name": metadata.get("case_name"),
            "date_argued_iso": metadata.get("date_argued_iso"),
            "audio_file": str(audio_path),
            "metadata_file": str(metadata_path),
        },
        "sources": {
            "timeline": str(timeline_path),
            "speaker_turns": str(args.speaker_turns) if args.speaker_turns else None,
        },
        "settings": {
            "chunk_seconds": args.chunk_seconds,
            "turn_padding": args.turn_padding,
            "model_size": args.model_size,
            "max_segments_per_turn": args.max_segments_per_turn,
            "min_score": args.min_score,
            "max_bad_ratio": args.max_bad_ratio,
            "dry_run": args.dry_run,
            "stop_on_warning": args.stop_on_warning,
            "overwrite": args.overwrite,
            "workers": 1,
        },
        "summary": {
            "planned_chunks": len(windows),
            "completed_chunks": len(chunks),
            "quality_counts": dict(quality_counts),
        },
        "chunks": sorted(chunks, key=lambda chunk: int(chunk.get("index", 0))),
    }


def write_progress_report(
    args: AlignmentChunksProcessorInput,
    metadata: dict[str, Any],
    metadata_path: Path,
    audio_path: Path,
    timeline_path: Path,
    windows: list[tuple[float, float]],
    chunks: list[dict[str, Any]],
) -> Path:
    report_path = args.report or args.output_dir / "chunks" / "chunk-report.json"
    payload = build_report_payload(args, metadata, metadata_path, audio_path, timeline_path, windows, chunks)
    write_json(report_path, payload)
    return report_path


def planned_chunk(
    args: AlignmentChunksProcessorInput,
    segments: list[dict[str, Any]],
    audio_path: Path,
    chunk_index: int,
    start: float,
    end: float,
) -> dict[str, Any]:
    turn_start, turn_count, selected_turns = turns_for_window(segments, start, end, args.turn_padding)
    output_path = alignment_output_path(args.output_dir, start, end)
    return {
        "index": chunk_index,
        "window": {
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
        },
        "turn_window": {
            "turn_start": turn_start,
            "turn_count": turn_count,
            "selected_turns": selected_turns,
        },
        "output": str(output_path),
    }


def process_chunk(
    args: AlignmentChunksProcessorInput,
    case_dir: Path,
    audio_path: Path,
    chunk: dict[str, Any],
    client: AsrWorkerClient | None = None,
) -> dict[str, Any]:
    start = float(chunk["window"]["start"])
    end = float(chunk["window"]["end"])
    turn_start = int(chunk["turn_window"]["turn_start"])
    turn_count = int(chunk["turn_window"]["turn_count"])
    output_path = Path(str(chunk["output"]))

    if turn_count == 0:
        return {
            **chunk,
            "quality": "skipped",
            "reason": "no timeline turns overlap this audio window",
        }

    if args.dry_run:
        return {**chunk, "quality": "planned"}

    if not args.overwrite and validate_existing_chunk(
        output_path,
        args,
        audio_path,
        start,
        end,
        turn_start,
        turn_count,
        args.speaker_turns,
    ):
        summary = summarize_alignment(output_path, args.max_bad_ratio)
        return {**chunk, **summary, "cached": True}

    result = run_chunk(args, case_dir, audio_path, start, end, turn_start, turn_count, output_path, client)
    updated = {
        **chunk,
        "returncode": result["returncode"],
        "stdout": result["stdout"][-5:],
        "stderr": result["stderr"][-20:],
        "cached": False,
    }

    if result["returncode"] != 0:
        return {**updated, "quality": "error"}

    data = load_json(output_path)
    if isinstance(data, dict):
        data["cache_key"] = chunk_cache_key(args, audio_path, args.speaker_turns)
        write_json(output_path, data)

    return {**updated, **summarize_alignment(output_path, args.max_bad_ratio)}


def print_chunk_progress(args: Any, chunk: dict[str, Any], completed: int, total: int) -> None:
    window = chunk.get("window", {})
    quality = chunk.get("quality", "unknown")
    cached = " cached" if chunk.get("cached") else ""
    message = f"chunk {completed}/{total}: {window.get('start')}-{window.get('end')}s {quality}{cached}"
    progress = getattr(args, "progress", None)
    if progress:
        progress(
            current=completed,
            total=total,
            message=message,
            metrics={
                "quality": quality,
                "cached": bool(chunk.get("cached")),
                "window": window,
            },
        )
        return
    print(message, flush=True)


def build_report(args: AlignmentChunksProcessorInput) -> Path:
    case_dir = find_case_dir(args.case_dir)
    metadata_path = args.metadata or case_dir / "metadata.json"
    metadata = load_json(metadata_path)
    audio_path = resolve_audio(metadata, case_dir, args.audio)
    timeline_path = resolve_timeline(audio_path, args.timeline)
    timeline = load_json(timeline_path)
    segments = timeline.get("segments", []) if isinstance(timeline, dict) else []
    if not isinstance(segments, list) or not segments:
        raise RuntimeError(f"timeline has no segments: {timeline_path}")

    duration = float(timeline.get("duration") or audio_duration(audio_path))
    windows = chunk_windows(duration, args.chunk_seconds, args.max_chunks)
    planned_chunks = [
        planned_chunk(args, segments, audio_path, chunk_index, start, end)
        for chunk_index, (start, end) in enumerate(windows)
    ]
    chunks: list[dict[str, Any]] = []

    report_path = write_progress_report(
        args, metadata, metadata_path, audio_path, timeline_path, windows, chunks
    )

    if getattr(args, "use_asr_worker", False):
        stderr_path = args.cache_dir / "asr-worker.stderr.log"
        with AsrWorkerClient(
            cwd=Path.cwd(), stderr_path=stderr_path, request_timeout_seconds=args.request_timeout_seconds
        ) as client:
            for chunk in planned_chunks:
                completed = process_chunk(args, case_dir, audio_path, chunk, client)
                chunks.append(completed)
                print_chunk_progress(args, completed, len(chunks), len(planned_chunks))
                report_path = write_progress_report(
                    args, metadata, metadata_path, audio_path, timeline_path, windows, chunks
                )
                if completed.get("quality") in {"warning", "error"} and args.stop_on_warning:
                    break
        return report_path

    for chunk in planned_chunks:
        completed = process_chunk(args, case_dir, audio_path, chunk)
        chunks.append(completed)
        print_chunk_progress(args, completed, len(chunks), len(planned_chunks))
        report_path = write_progress_report(
            args, metadata, metadata_path, audio_path, timeline_path, windows, chunks
        )
        if completed.get("quality") in {"warning", "error"} and args.stop_on_warning:
            break
    return report_path
