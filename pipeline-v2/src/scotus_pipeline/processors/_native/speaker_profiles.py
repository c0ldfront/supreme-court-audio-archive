#!/usr/bin/env python3
"""Build speaker profile metadata for a SCOTUS case timeline."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


DEFAULT_TIMELINE_OUTPUT = Path("timeline-output")
DEFAULT_OUTPUT = Path("profiles-output")
SCOTUS_BIOGRAPHIES_URL = "https://www.supremecourt.gov/about/biographies.aspx"
JUSTICE_PHOTO_BASE = "https://www.supremecourt.gov/about/justice_pictures/"


JUSTICE_PROFILES = {
    "CHIEF JUSTICE ROBERTS": {
        "display_name": "John G. Roberts, Jr.",
        "short_name": "Roberts",
        "title": "Chief Justice of the United States",
        "photo_file": "Roberts_8807-16_Crop.jpg",
    },
    "JUSTICE THOMAS": {
        "display_name": "Clarence Thomas",
        "short_name": "Thomas",
        "title": "Associate Justice",
        "photo_file": "Thomas_9366-024_Crop.jpg",
    },
    "JUSTICE ALITO": {
        "display_name": "Samuel A. Alito, Jr.",
        "short_name": "Alito",
        "title": "Associate Justice",
        "photo_file": "Alito_9264-001-Crop.jpg",
    },
    "JUSTICE SOTOMAYOR": {
        "display_name": "Sonia Sotomayor",
        "short_name": "Sotomayor",
        "title": "Associate Justice",
        "photo_file": "Sotomayor_Official_2025.jpg",
    },
    "JUSTICE KAGAN": {
        "display_name": "Elena Kagan",
        "short_name": "Kagan",
        "title": "Associate Justice",
        "photo_file": "Kagan_10713-017-Crop.jpg",
    },
    "JUSTICE GORSUCH": {
        "display_name": "Neil M. Gorsuch",
        "short_name": "Gorsuch",
        "title": "Associate Justice",
        "photo_file": "Gorsuch2.jpg",
    },
    "JUSTICE KAVANAUGH": {
        "display_name": "Brett M. Kavanaugh",
        "short_name": "Kavanaugh",
        "title": "Associate Justice",
        "photo_file": "Kavanaugh 12221_005_crop.jpg",
    },
    "JUSTICE BARRETT": {
        "display_name": "Amy Coney Barrett",
        "short_name": "Barrett",
        "title": "Associate Justice",
        "photo_file": "Barrett_102535_w151.jpg",
    },
    "JUSTICE JACKSON": {
        "display_name": "Ketanji Brown Jackson",
        "short_name": "Jackson",
        "title": "Associate Justice",
        "photo_file": "KBJackson3.jpg",
    },
}


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
    raise ValueError("timeline is required")


def photo_url(photo_file: str) -> str:
    return JUSTICE_PHOTO_BASE + quote(photo_file)


def display_advocate_name(speaker: str) -> str:
    if speaker.startswith(("MR. ", "MS. ", "MRS. ")):
        prefix, name = speaker.split(" ", 1)
        return f"{prefix} {name.title()}"
    return speaker.title()


def initials(display_name: str) -> str:
    words = re.findall(r"[A-Za-z]+", display_name)
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[-1][0]).upper()


def role_for_speaker(speaker: str) -> str:
    if speaker.startswith("CHIEF JUSTICE") or speaker.startswith("JUSTICE"):
        return "justice"
    return "advocate"


def segment_stats(segments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    counts = Counter(str(segment.get("speaker")) for segment in segments)
    seconds = Counter()
    aligned = Counter()

    for segment in segments:
        speaker = str(segment.get("speaker"))
        seconds[speaker] += max(0.0, float(segment.get("duration", 0.0)))
        source = segment.get("source", {})
        if isinstance(source, dict) and source.get("timing") == "whisperx_alignment":
            aligned[speaker] += 1

    for speaker in counts:
        stats[speaker] = {
            "turn_count": counts[speaker],
            "speaking_seconds": round(seconds[speaker], 3),
            "aligned_turn_count": aligned[speaker],
        }
    return stats


def speaker_color(timeline_speakers: list[dict[str, Any]], speaker: str, fallback_index: int) -> str:
    for profile in timeline_speakers:
        if profile.get("id") == speaker and profile.get("color"):
            return str(profile["color"])
    palette = ["#2563eb", "#dc2626", "#16a34a", "#7c3aed", "#ea580c", "#0891b2"]
    return palette[fallback_index % len(palette)]


def build_profile(speaker: str, timeline_speakers: list[dict[str, Any]], stats: dict[str, Any], index: int) -> dict[str, Any]:
    role = role_for_speaker(speaker)
    color = speaker_color(timeline_speakers, speaker, index)

    if role == "justice" and speaker in JUSTICE_PROFILES:
        justice = JUSTICE_PROFILES[speaker]
        display_name = justice["display_name"]
        return {
            "id": speaker,
            "display_name": display_name,
            "short_name": justice["short_name"],
            "role": role,
            "title": justice["title"],
            "initials": initials(display_name),
            "color": color,
            "photo_url": photo_url(justice["photo_file"]),
            "photo_source_url": SCOTUS_BIOGRAPHIES_URL,
            "photo_credit": "Supreme Court of the United States",
            "stats": stats,
            "voice": {
                "cluster_id": None,
                "identification_method": "official_transcript_speaker_label",
                "status": "not_voice_matched_yet",
            },
        }

    display_name = display_advocate_name(speaker)
    return {
        "id": speaker,
        "display_name": display_name,
        "short_name": display_name,
        "role": role,
        "title": "Advocate",
        "initials": initials(display_name),
        "color": color,
        "photo_url": None,
        "photo_source_url": None,
        "photo_credit": None,
        "stats": stats,
        "voice": {
            "cluster_id": None,
            "identification_method": "official_transcript_speaker_label",
            "status": "not_voice_matched_yet",
        },
    }


def build_profiles(args: argparse.Namespace) -> Path:
    timeline_path = resolve_timeline(args.timeline)
    timeline = load_json(timeline_path)
    segments = timeline.get("segments", []) if isinstance(timeline, dict) else []
    speakers = timeline.get("speakers", []) if isinstance(timeline, dict) else []
    if not isinstance(segments, list) or not segments:
        raise RuntimeError(f"timeline has no segments: {timeline_path}")
    if not isinstance(speakers, list):
        speakers = []

    ids = sorted({str(segment.get("speaker")) for segment in segments if segment.get("speaker")})
    stats = segment_stats(segments)
    profiles = [build_profile(speaker, speakers, stats.get(speaker, {}), index) for index, speaker in enumerate(ids)]

    case = timeline.get("case", {}) if isinstance(timeline, dict) else {}
    stem = Path(str(case.get("audio_file") or timeline_path.stem)).stem
    output = args.output or DEFAULT_OUTPUT / f"{stem}.speaker-profiles.json"
    payload = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case": case,
        "sources": {
            "timeline": str(timeline_path),
            "justice_biographies": SCOTUS_BIOGRAPHIES_URL,
        },
        "profiles": profiles,
        "notes": [
            "Justice portrait URLs point to the Supreme Court's official current-members biography page.",
            "Advocate photos are intentionally left blank until a verified source is added.",
            "Voice fields are placeholders for later diarization-to-speaker matching.",
        ],
    }
    write_json(output, payload)
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build speaker profile metadata for the case viewer.")
    parser.add_argument("--timeline", type=Path, help="case timeline JSON from script 05 or 08")
    parser.add_argument("--output", type=Path, help="speaker profiles JSON output path")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output = build_profiles(args)
    data = load_json(output)
    print(f"speaker_profiles: {output}")
    print(f"case: {data['case'].get('docket')} {data['case'].get('name')}")
    print(f"profiles: {len(data['profiles'])}")
    print(f"with_photos: {sum(1 for profile in data['profiles'] if profile.get('photo_url'))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
