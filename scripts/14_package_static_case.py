#!/usr/bin/env python3
"""Package an already-processed SCOTUS case for static hosting."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DEFAULT_ARCHIVE = Path("archive/scotus-audio")
DEFAULT_TRANSCRIPT_OUTPUT = Path("transcript-output")
DEFAULT_ALIGNMENT_OUTPUT = Path("alignment-output")
DEFAULT_TIMELINE_OUTPUT = Path("timeline-output")
DEFAULT_PROFILES_OUTPUT = Path("profiles-output")
DEFAULT_OUTPUT = Path("dist")
USER_AGENT = "supreme-court-ingest-packager/1.0"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".part")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def slugify(value: str) -> str:
    value = value.casefold()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-") or "scotus-case"


def normalize_docket(value: str) -> str:
    return value.strip().casefold().replace(" ", "")


def resolve_case_dir(args: argparse.Namespace) -> Path:
    if args.case_dir:
        metadata = args.case_dir / "metadata.json"
        if not metadata.exists():
            raise FileNotFoundError(metadata)
        return args.case_dir

    if not args.docket:
        raise ValueError("provide --case-dir or --docket")

    matches: list[tuple[str, Path]] = []
    for metadata_path in sorted(DEFAULT_ARCHIVE.glob("*/**/metadata.json")):
        try:
            metadata = load_json(metadata_path)
        except (OSError, json.JSONDecodeError):
            continue
        if normalize_docket(str(metadata.get("docket", ""))) != normalize_docket(args.docket):
            continue
        if args.term is not None and int(metadata.get("term", 0) or 0) != args.term:
            continue
        date = str(metadata.get("date_argued_iso") or metadata.get("date_argued") or "")
        matches.append((date, metadata_path.parent))

    if not matches:
        raise FileNotFoundError(f"no archived case found for docket {args.docket}")
    return sorted(matches, reverse=True)[0][1]


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


def resolve_optional(paths: list[Path]) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def copy_file(src: Path, dst: Path) -> dict[str, Any]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {
        "path": str(dst),
        "bytes": dst.stat().st_size,
        "sha256": sha256(dst),
    }


def load_viewer_builder():
    module_path = Path("scripts/12_build_case_viewer.py")
    spec = importlib.util.spec_from_file_location("case_viewer_builder", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def download(url: str, output: Path, timeout: int) -> bool:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            output.parent.mkdir(parents=True, exist_ok=True)
            tmp = output.with_suffix(output.suffix + ".part")
            with tmp.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            tmp.replace(output)
        return True
    except (OSError, URLError):
        return False


def portrait_filename(profile: dict[str, Any]) -> str:
    parsed = urlparse(str(profile.get("photo_url", "")))
    suffix = Path(parsed.path).suffix or ".jpg"
    return f"{slugify(str(profile.get('short_name') or profile.get('display_name') or profile.get('id')))}{suffix}"


def localize_portraits(profiles: dict[str, Any], package_dir: Path, timeout: int, strict: bool) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for profile in profiles.get("profiles", []):
        if not isinstance(profile, dict) or not profile.get("photo_url"):
            continue
        url = str(profile["photo_url"])
        relative = Path("assets") / "portraits" / portrait_filename(profile)
        output = package_dir / relative
        ok = output.exists() or download(url, output, timeout)
        if not ok and strict:
            raise RuntimeError(f"failed to download portrait: {url}")
        if ok:
            profile["photo_url"] = relative.as_posix()
        results.append({"source": url, "path": relative.as_posix() if ok else None, "downloaded": ok})
    return results


def package_case(args: argparse.Namespace) -> Path:
    case_dir = resolve_case_dir(args)
    metadata_path = case_dir / "metadata.json"
    metadata = load_json(metadata_path)
    audio_path = resolve_audio(metadata, case_dir)
    stem = audio_path.stem
    docket = str(metadata.get("docket") or stem)
    case_name = str(metadata.get("case_name") or metadata.get("name") or stem)
    package_name = args.name or f"{slugify(case_name)}_{slugify(docket)}"
    package_dir = args.output / package_name

    if package_dir.exists() and args.overwrite:
        shutil.rmtree(package_dir)
    package_dir.mkdir(parents=True, exist_ok=True)

    timeline_path = args.timeline or DEFAULT_TIMELINE_OUTPUT / f"{stem}.case-timeline.json"
    profiles_path = args.profiles or DEFAULT_PROFILES_OUTPUT / f"{stem}.speaker-profiles.json"
    if not timeline_path.exists():
        raise FileNotFoundError(timeline_path)
    if not profiles_path.exists():
        raise FileNotFoundError(profiles_path)

    timeline = load_json(timeline_path)
    profiles = load_json(profiles_path)
    copied: dict[str, Any] = {}

    copied["audio"] = copy_file(audio_path, package_dir / "assets" / "audio.mp3")
    copied["metadata"] = copy_file(metadata_path, package_dir / "data" / "metadata.json")
    copied["timeline"] = copy_file(timeline_path, package_dir / "data" / "timeline.json")
    copied["profiles"] = copy_file(profiles_path, package_dir / "data" / "speaker-profiles.json")

    optional_sources = {
        "transcript_pdf": resolve_optional([case_dir / f"{stem}.pdf", *sorted(case_dir.glob("*.pdf"))]),
        "transcript_text": resolve_optional([DEFAULT_TRANSCRIPT_OUTPUT / f"{stem}.txt"]),
        "speaker_turns": resolve_optional([DEFAULT_TRANSCRIPT_OUTPUT / f"{stem}.speaker-turns.json"]),
        "chunk_report": resolve_optional(
            [
                DEFAULT_ALIGNMENT_OUTPUT / "chunks" / f"{stem}.chunk-report.json",
                DEFAULT_ALIGNMENT_OUTPUT / "chunks" / "chunk-report.json",
            ]
        ),
        "merged_alignment": resolve_optional([DEFAULT_ALIGNMENT_OUTPUT / f"{stem}.merged-chunks.json"]),
    }
    optional_destinations = {
        "transcript_pdf": package_dir / "data" / "transcript.pdf",
        "transcript_text": package_dir / "data" / "transcript.txt",
        "speaker_turns": package_dir / "data" / "speaker-turns.json",
        "chunk_report": package_dir / "data" / "chunk-report.json",
        "merged_alignment": package_dir / "data" / "merged-alignment.json",
    }
    for key, source in optional_sources.items():
        if source:
            copied[key] = copy_file(source, optional_destinations[key])

    portraits = localize_portraits(profiles, package_dir, args.timeout, args.strict_portraits)
    write_json(package_dir / "data" / "speaker-profiles.json", profiles)
    copied["profiles"] = {
        "path": str(package_dir / "data" / "speaker-profiles.json"),
        "bytes": (package_dir / "data" / "speaker-profiles.json").stat().st_size,
        "sha256": sha256(package_dir / "data" / "speaker-profiles.json"),
    }

    timeline.setdefault("case", {})["audio_file"] = "assets/audio.mp3"
    viewer_builder = load_viewer_builder()
    html = viewer_builder.build_html(
        timeline,
        profiles,
        "assets/audio.mp3",
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    index_path = package_dir / "index.html"
    index_path.write_text(html, encoding="utf-8")
    copied["viewer"] = {
        "path": str(index_path),
        "bytes": index_path.stat().st_size,
        "sha256": sha256(index_path),
    }

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "package": package_name,
        "case": {
            "docket": metadata.get("docket"),
            "name": metadata.get("case_name") or metadata.get("name"),
            "date_argued_iso": metadata.get("date_argued_iso"),
            "term": metadata.get("term"),
        },
        "entrypoint": "index.html",
        "files": copied,
        "portraits": portraits,
        "notes": [
            "This package is static and does not require the source repo to serve.",
            "Open index.html from a static web server for best browser behavior.",
        ],
    }
    write_json(package_dir / "manifest.json", manifest)

    if args.zip:
        zip_path = package_dir.with_suffix(".zip")
        if zip_path.exists():
            zip_path.unlink()
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(package_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(package_dir.parent))
        print(f"zip: {zip_path}")

    return package_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Package an already-processed SCOTUS case for static hosting.")
    parser.add_argument("--case-dir", type=Path, help="archived case directory containing metadata.json")
    parser.add_argument("--docket", help="case docket to package")
    parser.add_argument("--term", type=int, help="term year when resolving --docket")
    parser.add_argument("--timeline", type=Path, help="timeline JSON to package")
    parser.add_argument("--profiles", type=Path, help="speaker profiles JSON to package")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="package output root")
    parser.add_argument("--name", help="package directory name")
    parser.add_argument("--zip", action="store_true", help="also create a zip archive")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing package directory")
    parser.add_argument("--strict-portraits", action="store_true", help="fail if a portrait cannot be cached")
    parser.add_argument("--timeout", type=int, default=30, help="portrait download timeout")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        package_dir = package_case(args)
    except Exception as exc:
        print(f"package failed: {exc}", file=sys.stderr)
        return 1
    print(f"package: {package_dir}")
    print(f"open: {package_dir.resolve() / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
