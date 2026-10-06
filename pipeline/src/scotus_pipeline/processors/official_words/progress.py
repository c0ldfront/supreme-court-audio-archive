"""Report word-alignment progress through processor callbacks."""

from __future__ import annotations

from typing import Any

from scotus_pipeline.processors.contracts import OfficialWordsProcessorInput


def emit_progress(
    args: OfficialWordsProcessorInput, current: int, total: int, message: str, **metrics: Any
) -> None:
    progress = getattr(args, "progress", None)
    if progress:
        progress(current=current, total=total, message=message, metrics=metrics)
        return
    print(message, flush=True)
