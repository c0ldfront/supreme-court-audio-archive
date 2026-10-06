"""Tokenize official transcript turns and normalize matching text."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

TOKEN_RE = re.compile(r"\S+")
NORMALIZE_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class OfficialToken:
    global_index: int
    turn_index: int
    word_index: int
    word: str
    normalized: str


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
