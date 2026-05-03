#!/usr/bin/env python3
"""Merge chunked transcript/audio alignments into one timeline-ready file."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scotus_pipeline.processors.contracts import BuildTimelineProcessorInput
from scotus_pipeline.processors._native import build_timeline


DEFAULT_ARCHIVE = Path("archive/scotus-audio")
DEFAULT_ALIGNMENT_OUTPUT = Path("alignment-output")
DEFAULT_TIMELINE_OUTPUT = Path("timeline-output")
STATUS_RANK = {
    "matched": 5,
    "partial_clip": 4,
    "low_confidence": 3,
    "unmatched": 2,
    "no_audio": 1,
}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".part")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


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


def chunk_paths_from_report(report_path: Path) -> list[Path]:
    if not report_path.exists():
        return []
    report = load_json(report_path)
    chunks = report.get("chunks", []) if isinstance(report, dict) else []
    paths: list[Path] = []
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        if chunk.get("quality") in {"error", "planned", "skipped"}:
            continue
        output = chunk.get("output")
        if output:
            path = Path(str(output))
            if path.exists():
                paths.append(path)
    return paths


def resolve_chunk_paths(args: argparse.Namespace, audio_path: Path) -> list[Path]:
    if args.chunks:
        return [path for path in args.chunks if path.exists()]

    if not args.all_chunks:
        from_report = chunk_paths_from_report(args.chunk_report)
        if from_report:
            return from_report

    chunks_dir = args.chunks_dir
    patterns = [
        f"{audio_path.stem}.chunk-*.json",
        f"{audio_path.stem}.align-*.json",
    ]
    paths: list[Path] = []
    for pattern in patterns:
        paths.extend(sorted(chunks_dir.glob(pattern)))
    return [path for path in paths if path.name != "chunk-report.json"]


def rank_turn(turn: dict[str, Any]) -> tuple[int, float, float]:
    status = str(turn.get("status", ""))
    confidence = float(turn.get("confidence") or 0.0)
    duration = float(turn.get("duration") or 0.0)
    return STATUS_RANK.get(status, 0), confidence, duration


def better_turn(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
    return rank_turn(candidate) > rank_turn(current)


def source_for_turn(chunk_path: Path, chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "chunk_file": str(chunk_path),
        "chunk_window": chunk.get("window", {}),
        "chunk_turn_window": chunk.get("turn_window", {}),
    }


def merge_chunks(chunk_paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    selected: dict[int, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    chunk_summaries: list[dict[str, Any]] = []

    for chunk_path in chunk_paths:
        chunk = load_json(chunk_path)
        if not isinstance(chunk, dict):
            continue

        aligned_turns = chunk.get("aligned_turns", [])
        if not isinstance(aligned_turns, list):
            aligned_turns = []

        status_counts = Counter(str(turn.get("status")) for turn in aligned_turns if isinstance(turn, dict))
        chunk_summaries.append(
            {
                "path": str(chunk_path),
                "window": chunk.get("window", {}),
                "turn_window": chunk.get("turn_window", {}),
                "status_counts": dict(status_counts),
                "aligned_turn_count": len(aligned_turns),
            }
        )

        source = source_for_turn(chunk_path, chunk)
        for raw_turn in aligned_turns:
            if not isinstance(raw_turn, dict):
                continue
            turn_index = int(raw_turn["turn_index"])
            candidate = {**raw_turn, "source_chunk": source}
            current = selected.get(turn_index)
            if current is None:
                selected[turn_index] = candidate
                continue

            if better_turn(candidate, current):
                selected[turn_index] = candidate
                kept = candidate
                discarded = current
            else:
                kept = current
                discarded = candidate

            conflicts.append(
                {
                    "turn_index": turn_index,
                    "kept": {
                        "status": kept.get("status"),
                        "confidence": kept.get("confidence"),
                        "chunk_file": kept.get("source_chunk", {}).get("chunk_file"),
                    },
                    "discarded": {
                        "status": discarded.get("status"),
                        "confidence": discarded.get("confidence"),
                        "chunk_file": discarded.get("source_chunk", {}).get("chunk_file"),
                    },
                }
            )

    merged_turns = [selected[index] for index in sorted(selected)]
    metadata = {"chunks": chunk_summaries, "conflicts": conflicts}
    return merged_turns, conflicts, metadata


def build_window(chunks: list[dict[str, Any]]) -> dict[str, Any]:
    starts: list[float] = []
    ends: list[float] = []
    for chunk in chunks:
        window = chunk.get("window", {})
        if isinstance(window, dict):
            if window.get("start") is not None:
                starts.append(float(window["start"]))
            if window.get("end") is not None:
                ends.append(float(window["end"]))

    if not starts or not ends:
        return {"start": None, "end": None, "duration": None, "full": False}

    start = min(starts)
    end = max(ends)
    return {
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(end - start, 3),
        "full": False,
    }


def build_turn_window(merged_turns: list[dict[str, Any]]) -> dict[str, Any]:
    if not merged_turns:
        return {"turn_start": None, "turn_count": 0, "turn_end": None}
    indexes = [int(turn["turn_index"]) for turn in merged_turns]
    return {
        "turn_start": min(indexes),
        "turn_count": len(merged_turns),
        "turn_end": max(indexes),
    }


def rebuild_timeline(
    case_dir: Path,
    audio_path: Path,
    alignment_path: Path,
    speaker_turns_path: Path,
    timeline_output: Path | None,
) -> Path:
    return build_timeline.build_timeline(
        BuildTimelineProcessorInput(
            case_dir=case_dir,
            metadata=None,
            audio=audio_path,
            speaker_turns=speaker_turns_path,
            whisperx=None,
            diarization=None,
            alignment=alignment_path,
            output=timeline_output,
        ).to_namespace()
    )


def build_merged_alignment(args: argparse.Namespace) -> Path:
    case_dir = find_case_dir(args.case_dir)
    metadata_path = args.metadata or case_dir / "metadata.json"
    metadata = load_json(metadata_path)
    audio_path = resolve_audio(metadata, case_dir, args.audio)
    chunk_paths = resolve_chunk_paths(args, audio_path)
    if not chunk_paths:
        raise FileNotFoundError("no chunk alignment JSON files found; run the v2 run_alignment step first")

    merged_turns, conflicts, merge_metadata = merge_chunks(chunk_paths)
    status_counts = Counter(str(turn.get("status")) for turn in merged_turns)
    output_path = args.output or args.output_dir / f"{audio_path.stem}.merged-chunks.json"

    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case": {
            "docket": metadata.get("docket"),
            "name": metadata.get("case_name"),
            "date_argued_iso": metadata.get("date_argued_iso"),
            "audio_file": str(audio_path),
            "metadata_file": str(metadata_path),
        },
        "window": build_window(merge_metadata["chunks"]),
        "turn_window": build_turn_window(merged_turns),
        "sources": {
            "chunks": [str(path) for path in chunk_paths],
            "chunk_report": str(args.chunk_report) if args.chunk_report.exists() else None,
        },
        "alignment": {
            "method": "merged_chunked_ordered_fuzzy_whisperx_token_span_match",
            "status_counts": dict(status_counts),
            "chunk_count": len(chunk_paths),
            "conflict_count": len(conflicts),
        },
        "merge": merge_metadata,
        "aligned_turns": merged_turns,
    }
    write_json(output_path, payload)

    if args.rebuild_timeline:
        timeline_output = args.timeline_output or DEFAULT_TIMELINE_OUTPUT / f"{audio_path.stem}.case-timeline.json"
        if not args.speaker_turns:
            raise ValueError("--speaker-turns is required when rebuilding the timeline")
        rebuild_timeline(case_dir, audio_path, output_path, args.speaker_turns, timeline_output)

    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge chunk alignment files into one 05-compatible alignment.")
    parser.add_argument("--case-dir", type=Path, help="archived case directory containing metadata.json")
    parser.add_argument("--metadata", type=Path, help="metadata.json path")
    parser.add_argument("--audio", type=Path, help="MP3 path")
    parser.add_argument("--speaker-turns", type=Path, help="speaker-turns JSON from transcript extraction")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ALIGNMENT_OUTPUT)
    parser.add_argument("--output", type=Path, help="merged alignment JSON output path")
    parser.add_argument("--chunks-dir", type=Path, default=DEFAULT_ALIGNMENT_OUTPUT / "chunks")
    parser.add_argument(
        "--chunk-report",
        type=Path,
        default=DEFAULT_ALIGNMENT_OUTPUT / "chunks" / "chunk-report.json",
        help="chunk report from script 07",
    )
    parser.add_argument("--chunks", type=Path, nargs="+", help="specific chunk alignment JSON files to merge")
    parser.add_argument("--all-chunks", action="store_true", help="ignore chunk report and merge all chunk files")
    parser.add_argument("--rebuild-timeline", action="store_true", help="run script 05 with the merged alignment")
    parser.add_argument("--timeline-output", type=Path, help="timeline output path when rebuilding")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = build_merged_alignment(args)
    data = load_json(output)
    print(f"merged_alignment: {output}")
    print(f"case: {data['case']['docket']} {data['case']['name']}")
    print(f"chunks: {data['alignment']['chunk_count']}")
    print(f"turns: {data['turn_window']['turn_count']}")
    print(f"statuses: {data['alignment']['status_counts']}")
    print(f"conflicts: {data['alignment']['conflict_count']}")
    if args.rebuild_timeline:
        timeline_output = args.timeline_output or DEFAULT_TIMELINE_OUTPUT / f"{Path(data['case']['audio_file']).stem}.case-timeline.json"
        print(f"timeline: {timeline_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
