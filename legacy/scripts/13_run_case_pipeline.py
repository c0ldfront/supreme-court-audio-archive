#!/usr/bin/env python3
"""Run the full SCOTUS archive-to-viewer pipeline for one case."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_ARCHIVE = Path("archive/scotus-audio")
DEFAULT_TRANSCRIPT_OUTPUT = Path("transcript-output")
DEFAULT_ALIGNMENT_OUTPUT = Path("alignment-output")
DEFAULT_TIMELINE_OUTPUT = Path("timeline-output")
DEFAULT_PROFILES_OUTPUT = Path("profiles-output")
DEFAULT_VIEWER_OUTPUT = Path("viewer-output")


@dataclass(frozen=True)
class CasePaths:
    case_dir: Path
    metadata: Path
    audio: Path
    transcript_pdf: Path
    speaker_turns: Path
    timeline: Path
    chunk_report: Path
    merged_alignment: Path
    official_word_alignment: Path
    profiles: Path
    viewer: Path


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_docket(value: str) -> str:
    return value.strip().casefold().replace(" ", "")


def metadata_docket(metadata: dict[str, Any]) -> str:
    return normalize_docket(str(metadata.get("docket", "")))


def metadata_date(metadata: dict[str, Any]) -> str:
    return str(metadata.get("date_argued_iso") or metadata.get("date_argued") or "")


def resolve_from_metadata_path(metadata_path: Path) -> CasePaths:
    metadata = load_json(metadata_path)
    case_dir = metadata_path.parent
    audio = resolve_audio(metadata, case_dir)
    transcript_pdf = resolve_transcript_pdf(metadata, case_dir)
    return build_case_paths(case_dir, metadata_path, audio, transcript_pdf)


def resolve_audio(metadata: dict[str, Any], case_dir: Path) -> Path:
    audio_file = metadata.get("audio_file")
    if isinstance(audio_file, dict) and audio_file.get("path"):
        path = Path(str(audio_file["path"]))
        if path.exists():
            return path
    matches = sorted(case_dir.glob("*.mp3"))
    if not matches:
        raise FileNotFoundError(f"no MP3 found in {case_dir}")
    return matches[0]


def resolve_transcript_pdf(metadata: dict[str, Any], case_dir: Path) -> Path:
    transcript_file = metadata.get("transcript_file")
    if isinstance(transcript_file, dict) and transcript_file.get("path"):
        path = Path(str(transcript_file["path"]))
        if path.exists():
            return path
    matches = sorted(case_dir.glob("*.pdf"))
    if not matches:
        raise FileNotFoundError(f"no transcript PDF found in {case_dir}")
    return matches[0]


def build_case_paths(case_dir: Path, metadata: Path, audio: Path, transcript_pdf: Path) -> CasePaths:
    stem = audio.stem
    return CasePaths(
        case_dir=case_dir,
        metadata=metadata,
        audio=audio,
        transcript_pdf=transcript_pdf,
        speaker_turns=DEFAULT_TRANSCRIPT_OUTPUT / f"{transcript_pdf.stem}.speaker-turns.json",
        timeline=DEFAULT_TIMELINE_OUTPUT / f"{stem}.case-timeline.json",
        chunk_report=DEFAULT_ALIGNMENT_OUTPUT / "chunks" / f"{stem}.chunk-report.json",
        merged_alignment=DEFAULT_ALIGNMENT_OUTPUT / f"{stem}.merged-chunks.json",
        official_word_alignment=DEFAULT_ALIGNMENT_OUTPUT / f"{stem}.official-asr-word-alignment.json",
        profiles=DEFAULT_PROFILES_OUTPUT / f"{stem}.speaker-profiles.json",
        viewer=DEFAULT_VIEWER_OUTPUT / f"{stem}.html",
    )


def find_case_by_docket(docket: str, terms: list[int] | None) -> CasePaths | None:
    normalized = normalize_docket(docket)
    candidates: list[tuple[str, int, Path]] = []
    for metadata_path in sorted(DEFAULT_ARCHIVE.glob("*/**/metadata.json")):
        try:
            metadata = load_json(metadata_path)
        except (OSError, json.JSONDecodeError):
            continue
        if metadata_docket(metadata) != normalized:
            continue
        term = int(metadata.get("term", 0) or 0)
        if terms and term not in terms:
            continue
        candidates.append((metadata_date(metadata), term, metadata_path))

    if not candidates:
        return None

    _, _, metadata_path = sorted(candidates, reverse=True)[0]
    return resolve_from_metadata_path(metadata_path)


def progress_bar(done: int, total: int, width: int = 28) -> str:
    filled = int(width * done / max(1, total))
    return "[" + "#" * filled + "-" * (width - filled) + f"] {done}/{total}"


def print_step_start(index: int, total: int, title: str) -> float:
    print(f"\n{progress_bar(index - 1, total)} {title}")
    return time.monotonic()


def print_step_done(index: int, total: int, title: str, started: float) -> None:
    elapsed = time.monotonic() - started
    print(f"{progress_bar(index, total)} {title} [ok] {elapsed:.1f}s")


def run_command(title: str, command: list[str], index: int, total: int, quiet: bool) -> None:
    started = print_step_start(index, total, title)
    if not quiet:
        print("  $ " + " ".join(command))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    for line in process.stdout:
        if not quiet:
            print("  | " + line.rstrip())
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError(f"{title} failed with exit code {return_code}")
    print_step_done(index, total, title, started)


def resolve_case_after_archive(args: argparse.Namespace) -> CasePaths:
    if args.case_dir:
        metadata_path = args.case_dir / "metadata.json"
        if not metadata_path.exists():
            raise FileNotFoundError(metadata_path)
        return resolve_from_metadata_path(metadata_path)

    assert args.docket
    case_paths = find_case_by_docket(args.docket, args.term)
    if not case_paths:
        raise FileNotFoundError(f"archived case not found for docket {args.docket}")
    return case_paths


def archive_command(args: argparse.Namespace) -> list[str]:
    if not args.docket:
        raise ValueError("--docket is required unless --case-dir and --skip-archive are used")
    command = [
        sys.executable,
        "scripts/01_archive_scotus_audio.py",
        "--docket",
        args.docket,
        "--include-transcripts",
        "--output",
        str(DEFAULT_ARCHIVE),
    ]
    for term in args.term or []:
        command.extend(["--term", str(term)])
    if args.overwrite_downloads:
        command.append("--overwrite")
    return command


def build_steps(args: argparse.Namespace, paths: CasePaths) -> list[tuple[str, list[str]]]:
    steps: list[tuple[str, list[str]]] = [
        (
            "Extract transcript PDF",
            [
                sys.executable,
                "scripts/02_extract_transcript_pdf.py",
                "--pdf",
                str(paths.transcript_pdf),
                "--output",
                str(DEFAULT_TRANSCRIPT_OUTPUT),
            ],
        ),
        (
            "Build initial timeline",
            [
                sys.executable,
                "scripts/05_build_case_timeline.py",
                "--case-dir",
                str(paths.case_dir),
                "--audio",
                str(paths.audio),
                "--speaker-turns",
                str(paths.speaker_turns),
                "--output",
                str(paths.timeline),
            ],
        ),
    ]

    if not args.skip_alignment:
        align = [
            sys.executable,
            "scripts/07_run_alignment_chunks.py",
            "--case-dir",
            str(paths.case_dir),
            "--audio",
            str(paths.audio),
            "--timeline",
            str(paths.timeline),
            "--report",
            str(paths.chunk_report),
            "--chunk-seconds",
            str(args.chunk_seconds),
            "--model-size",
            args.model_size,
            "--max-bad-ratio",
            str(args.max_bad_ratio),
            "--workers",
            str(args.workers),
        ]
        if args.max_chunks:
            align.extend(["--max-chunks", str(args.max_chunks)])
        if args.stop_on_warning:
            align.append("--stop-on-warning")
        if args.overwrite_alignment:
            align.append("--overwrite")
        steps.append(("Run chunk alignment", align))

        steps.append(
            (
                "Merge chunks and rebuild timeline",
                [
                    sys.executable,
                    "scripts/08_merge_chunk_alignments.py",
                    "--case-dir",
                    str(paths.case_dir),
                    "--audio",
                    str(paths.audio),
                    "--chunk-report",
                    str(paths.chunk_report),
                    "--output",
                    str(paths.merged_alignment),
                    "--rebuild-timeline",
                    "--timeline-output",
                    str(paths.timeline),
                ],
            )
        )

        steps.append(
            (
                "Align official transcript words",
                [
                    sys.executable,
                    "scripts/09_force_align_official_transcript_blocks.py",
                    "--timeline",
                    str(paths.timeline),
                    "--audio",
                    str(paths.audio),
                    "--output-alignment",
                    str(paths.official_word_alignment),
                    "--output-timeline",
                    str(paths.timeline),
                ],
            )
        )
        if args.overwrite_word_alignment:
            steps[-1][1].append("--overwrite")
        if args.apply_low_quality_word_alignment:
            steps[-1][1].append("--apply-low-quality")

        steps.append(
            (
                "Refine speech and non-speech regions",
                [
                    sys.executable,
                    "scripts/10_refine_timeline_speech_gaps.py",
                    "--timeline",
                    str(paths.timeline),
                    "--output",
                    str(paths.timeline),
                ],
            )
        )

    steps.extend(
        [
            (
                "Build speaker profiles",
                [
                    sys.executable,
                    "scripts/11_build_speaker_profiles.py",
                    "--timeline",
                    str(paths.timeline),
                    "--output",
                    str(paths.profiles),
                ],
            ),
            (
                "Build static viewer",
                [
                    sys.executable,
                    "scripts/12_build_case_viewer.py",
                    "--timeline",
                    str(paths.timeline),
                    "--profiles",
                    str(paths.profiles),
                    "--output",
                    str(paths.viewer),
                ],
            ),
        ]
    )
    return steps


def run_pipeline(args: argparse.Namespace) -> int:
    planned_archive = not args.skip_archive and not args.case_dir
    total = 1 + len(build_steps(args, placeholder_paths())) if planned_archive else len(build_steps(args, placeholder_paths()))
    step_index = 1

    if planned_archive:
        run_command("Archive case audio and transcript", archive_command(args), step_index, total, args.quiet)
        step_index += 1

    paths = resolve_case_after_archive(args)
    print("\nCase")
    print(f"  metadata: {paths.metadata}")
    print(f"  audio:    {paths.audio}")
    print(f"  pdf:      {paths.transcript_pdf}")

    for title, command in build_steps(args, paths):
        run_command(title, command, step_index, total, args.quiet)
        step_index += 1

    print("\nPipeline complete")
    print(f"  timeline: {paths.timeline}")
    if not args.skip_alignment:
        print(f"  chunks:   {paths.chunk_report}")
        print(f"  merged:   {paths.merged_alignment}")
        print(f"  words:    {paths.official_word_alignment}")
    print(f"  profiles: {paths.profiles}")
    print(f"  viewer:   {paths.viewer.resolve()}")
    return 0


def placeholder_paths() -> CasePaths:
    path = Path("__placeholder__")
    return CasePaths(
        case_dir=path,
        metadata=path / "metadata.json",
        audio=path / "case.mp3",
        transcript_pdf=path / "case.pdf",
        speaker_turns=path / "case.speaker-turns.json",
        timeline=path / "case.case-timeline.json",
        chunk_report=path / "chunk-report.json",
        merged_alignment=path / "merged.json",
        official_word_alignment=path / "official-asr-word-alignment.json",
        profiles=path / "profiles.json",
        viewer=path / "viewer.html",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the full SCOTUS case pipeline.")
    parser.add_argument("--docket", help="case docket to archive/process, for example 24-109")
    parser.add_argument("--term", type=int, action="append", help="term year to search; repeat as needed")
    parser.add_argument("--case-dir", type=Path, help="use an existing archived case directory")
    parser.add_argument("--chunk-seconds", type=float, default=300.0)
    parser.add_argument("--max-chunks", type=int, help="process only the first N chunks")
    parser.add_argument("--workers", type=int, default=2, help="parallel chunk alignment workers")
    parser.add_argument("--model-size", default="tiny.en")
    parser.add_argument("--max-bad-ratio", type=float, default=0.5)
    parser.add_argument("--stop-on-warning", action="store_true")
    parser.add_argument("--skip-archive", action="store_true")
    parser.add_argument("--skip-alignment", action="store_true")
    parser.add_argument("--overwrite-downloads", action="store_true")
    parser.add_argument("--overwrite-alignment", action="store_true")
    parser.add_argument("--overwrite-word-alignment", action="store_true")
    parser.add_argument("--apply-low-quality-word-alignment", action="store_true")
    parser.add_argument("--quiet", action="store_true", help="hide subprocess output")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.case_dir and not args.docket:
        raise SystemExit("error: provide --docket or --case-dir")
    if args.case_dir and not args.skip_archive:
        args.skip_archive = True
    try:
        return run_pipeline(args)
    except Exception as exc:
        print(f"\nPipeline failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
