"""Reuse speech models within the worker process, keeping GPU state isolated."""

from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def transcription_model(model_size: str, device: str, compute_type: str):
    import whisperx

    return whisperx.load_model(model_size, device=device, compute_type=compute_type, language="en")


@lru_cache(maxsize=1)
def alignment_model(device: str):
    import whisperx

    return whisperx.load_align_model(language_code="en", device=device)
