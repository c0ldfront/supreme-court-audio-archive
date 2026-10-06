"""Fingerprint source inputs to reject stale word-alignment caches."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from scotus_pipeline.io import path_identity, sha256_file
from scotus_pipeline.processors.contracts import OfficialWordsProcessorInput
from scotus_pipeline.processors.official_words.tokens import iter_timeline_segments


def official_alignment_fingerprint(
    timeline: dict[str, Any],
    audio_path: Path,
    args: OfficialWordsProcessorInput,
) -> dict[str, Any]:
    segments = [
        {
            "turn_index": segment.get("turn_index"),
            "speaker": segment.get("speaker"),
            "text": segment.get("text"),
        }
        for segment in iter_timeline_segments(timeline)
    ]
    transcript_digest = hashlib.sha256(
        json.dumps(segments, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "docket": str((timeline.get("case") or {}).get("docket") or ""),
        "audio_file": path_identity(audio_path),
        "audio_sha256": sha256_file(audio_path),
        "method": args.method,
        "settings": args.model_dump(
            mode="json",
            exclude={
                "progress",
                "timeline",
                "audio",
                "output_alignment",
                "output_timeline",
                "cache_dir",
                "overwrite",
                "progress_every",
            },
        ),
        "asr_cache": {p.name: sha256_file(p) for p in sorted(args.cache_dir.glob("*.whisperx.json"))}
        if args.method == "asr"
        else {},
        "segment_timing": [
            {"start": s.get("start"), "end": s.get("end")} for s in iter_timeline_segments(timeline)
        ]
        if args.method == "block"
        else [],
        "transcript_sha256": transcript_digest,
        "turn_count": len(segments),
    }


def alignment_matches_current_inputs(
    alignment: Any,
    timeline: dict[str, Any],
    audio_path: Path,
    args: OfficialWordsProcessorInput,
) -> bool:
    if not isinstance(alignment, dict):
        return False
    return alignment.get("source_fingerprint") == official_alignment_fingerprint(
        timeline,
        audio_path,
        args,
    )
