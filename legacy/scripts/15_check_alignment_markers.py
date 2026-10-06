#!/usr/bin/env python3
"""Check known phrase/time markers against a generated case timeline."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


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
        raise FileNotFoundError("timeline JSON not found")
    return matches[0]


def parse_time(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) == 1:
        return float(parts[0])
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    raise ValueError(f"invalid marker time: {value}")


def format_time(value: float | None) -> str:
    if value is None:
        return "--"
    minutes = int(value // 60)
    seconds = value - minutes * 60
    return f"{minutes}:{seconds:05.2f}"


def normalize_tokens(value: str) -> list[str]:
    value = value.casefold()
    value = value.replace("milligan", "miligan")
    value = value.replace("boerne", "boerne")
    return re.findall(r"[a-z0-9]+", value)


def find_subsequence(tokens: list[str], phrase_tokens: list[str]) -> int | None:
    if not phrase_tokens:
        return None
    for index in range(0, len(tokens) - len(phrase_tokens) + 1):
        if tokens[index : index + len(phrase_tokens)] == phrase_tokens:
            return index
    return None


def parse_marker(value: str) -> dict[str, Any]:
    if "|" in value:
        time_value, phrase = value.split("|", 1)
    elif "=" in value:
        time_value, phrase = value.split("=", 1)
    else:
        raise ValueError("markers must be formatted as TIME|PHRASE")
    return {
        "expected": parse_time(time_value),
        "phrase": phrase.strip().strip('"'),
    }


def load_markers(args: argparse.Namespace) -> list[dict[str, Any]]:
    markers = [parse_marker(value) for value in args.marker or []]
    if args.markers:
        data = load_json(args.markers)
        if isinstance(data, dict) and isinstance(data.get("markers"), list):
            data = data["markers"]
        if not isinstance(data, list):
            raise RuntimeError("--markers JSON must be a list or marker export object")
        for item in data:
            if not isinstance(item, dict):
                continue
            if "expected_time_seconds" in item:
                expected = float(item["expected_time_seconds"])
            else:
                expected = parse_time(str(item.get("time") or item.get("expected")))
            turn = item.get("turn", {})
            selection = item.get("selection", {})
            markers.append(
                {
                    "expected": expected,
                    "phrase": str(item["phrase"]),
                    "turn_index": turn.get("turn_index") if isinstance(turn, dict) else None,
                    "selection": selection if isinstance(selection, dict) else {},
                }
            )
    if not markers:
        raise RuntimeError("provide at least one --marker or --markers file")
    return markers


def phrase_timing_from_words(segment: dict[str, Any], token_start: int, token_count: int) -> tuple[float | None, float | None, str]:
    words = segment.get("words") or []
    if not isinstance(words, list) or not words:
        return None, None, "turn_estimate"
    selected = []
    for word in words[token_start : token_start + token_count]:
        if not isinstance(word, dict) or word.get("start") is None or word.get("end") is None:
            continue
        try:
            selected.append((float(word["start"]), float(word["end"])))
        except (TypeError, ValueError):
            pass
    if not selected:
        return None, None, "turn_estimate"
    return selected[0][0], selected[-1][1], "word"


def estimate_phrase_timing(segment: dict[str, Any], token_start: int, token_count: int) -> tuple[float, float]:
    tokens = normalize_tokens(str(segment.get("text", "")))
    start = float(segment.get("start", 0.0))
    end = float(segment.get("end", start))
    duration = max(0.0, end - start)
    total = max(1, len(tokens))
    phrase_start = start + duration * (token_start / total)
    phrase_end = start + duration * ((token_start + token_count) / total)
    return phrase_start, phrase_end


def check_marker(marker: dict[str, Any], segments: list[dict[str, Any]]) -> dict[str, Any]:
    phrase_tokens = normalize_tokens(marker["phrase"])
    hits: list[dict[str, Any]] = []
    marker_turn_index = marker.get("turn_index")
    selection = marker.get("selection") if isinstance(marker.get("selection"), dict) else {}

    if marker_turn_index is not None and selection:
        try:
            word_start = int(selection["word_start_index"])
            word_end = int(selection["word_end_index"])
        except (KeyError, TypeError, ValueError):
            word_start = word_end = -1
        if word_start >= 0 and word_end >= word_start:
            for segment in segments:
                if not isinstance(segment, dict) or str(segment.get("turn_index")) != str(marker_turn_index):
                    continue
                token_count = word_end - word_start + 1
                start, end, method = phrase_timing_from_words(segment, word_start, token_count)
                if start is None or end is None:
                    start, end = estimate_phrase_timing(segment, word_start, token_count)
                delta = start - float(marker["expected"])
                return {
                    "phrase": marker["phrase"],
                    "expected": round(float(marker["expected"]), 3),
                    "status": "ok",
                    "best": {
                        "turn_index": segment.get("turn_index"),
                        "speaker": segment.get("speaker"),
                        "expected": round(float(marker["expected"]), 3),
                        "observed_start": round(start, 3),
                        "observed_end": round(end, 3),
                        "delta_seconds": round(delta, 3),
                        "abs_delta_seconds": round(abs(delta), 3),
                        "method": f"{method}_selection",
                        "timing": (segment.get("source") or {}).get("timing"),
                        "word_alignment_score": (segment.get("source") or {}).get("word_alignment_mean_score"),
                        "text": str(segment.get("text", "")),
                    },
                    "hits": [],
                }

    for segment in segments:
        if not isinstance(segment, dict):
            continue
        tokens = normalize_tokens(str(segment.get("text", "")))
        token_start = find_subsequence(tokens, phrase_tokens)
        if token_start is None:
            continue

        start, end, method = phrase_timing_from_words(segment, token_start, len(phrase_tokens))
        if start is None or end is None:
            start, end = estimate_phrase_timing(segment, token_start, len(phrase_tokens))

        delta = start - float(marker["expected"])
        hits.append(
            {
                "turn_index": segment.get("turn_index"),
                "speaker": segment.get("speaker"),
                "expected": round(float(marker["expected"]), 3),
                "observed_start": round(start, 3),
                "observed_end": round(end, 3),
                "delta_seconds": round(delta, 3),
                "abs_delta_seconds": round(abs(delta), 3),
                "method": method,
                "timing": (segment.get("source") or {}).get("timing"),
                "word_alignment_score": (segment.get("source") or {}).get("word_alignment_mean_score"),
                "text": str(segment.get("text", "")),
            }
        )

    if not hits:
        return {
            "phrase": marker["phrase"],
            "expected": round(float(marker["expected"]), 3),
            "status": "not_found",
            "hits": [],
        }

    if marker_turn_index is not None:
        turn_hits = [hit for hit in hits if str(hit.get("turn_index")) == str(marker_turn_index)]
        if turn_hits:
            hits = turn_hits

    hits.sort(key=lambda hit: hit["abs_delta_seconds"])
    best = hits[0]
    return {
        "phrase": marker["phrase"],
        "expected": round(float(marker["expected"]), 3),
        "status": "ok",
        "best": best,
        "hits": hits[:5],
    }


def check_markers(args: argparse.Namespace) -> dict[str, Any]:
    timeline_path = resolve_timeline(args.timeline)
    timeline = load_json(timeline_path)
    segments = timeline.get("segments", []) if isinstance(timeline, dict) else []
    if not isinstance(segments, list) or not segments:
        raise RuntimeError(f"timeline has no segments: {timeline_path}")

    markers = load_markers(args)
    results = [check_marker(marker, segments) for marker in markers]
    deltas = [
        float(result["best"]["delta_seconds"])
        for result in results
        if result.get("status") == "ok" and result.get("best")
    ]
    max_abs_delta = max((abs(delta) for delta in deltas), default=None)
    mean_abs_delta = sum(abs(delta) for delta in deltas) / len(deltas) if deltas else None
    return {
        "timeline": str(timeline_path),
        "marker_count": len(markers),
        "matched_count": len(deltas),
        "summary": {
            "max_abs_delta_seconds": round(max_abs_delta, 3) if max_abs_delta is not None else None,
            "mean_abs_delta_seconds": round(mean_abs_delta, 3) if mean_abs_delta is not None else None,
        },
        "results": results,
    }


def print_table(report: dict[str, Any]) -> None:
    print(f"timeline: {report['timeline']}")
    print(f"markers: {report['matched_count']}/{report['marker_count']}")
    print(f"mean_abs_delta: {report['summary']['mean_abs_delta_seconds']}s")
    print(f"max_abs_delta: {report['summary']['max_abs_delta_seconds']}s")
    for result in report["results"]:
        if result["status"] != "ok":
            print(f"- {format_time(result['expected'])} not found: {result['phrase']}")
            continue
        best = result["best"]
        print(
            f"- {format_time(best['expected'])} -> {format_time(best['observed_start'])} "
            f"delta={best['delta_seconds']:+.2f}s "
            f"turn={best['turn_index']} {best['speaker']} "
            f"method={best['method']} phrase={result['phrase']}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check known phrase/time markers against a timeline.")
    parser.add_argument("--timeline", type=Path, help="case timeline JSON")
    parser.add_argument("--marker", action="append", help='marker formatted as "MM:SS|phrase"; repeat as needed')
    parser.add_argument("--markers", type=Path, help="JSON list of marker objects with time/phrase")
    parser.add_argument("--output", type=Path, help="optional JSON report output")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = check_markers(args)
    if args.output:
        write_json(args.output, report)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_table(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
