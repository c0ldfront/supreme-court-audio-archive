from __future__ import annotations

import importlib.util

ASR_EXTRA = "scotus-pipeline[asr]"


def require_asr_dependencies() -> None:
    missing = [name for name in ("torch", "whisperx") if importlib.util.find_spec(name) is None]
    if missing:
        formatted = ", ".join(missing)
        raise RuntimeError(
            f"ASR alignment requires optional dependencies missing from this environment: "
            f"{formatted}. Install the ASR extra with `uv sync --extra asr` or "
            f"`uv pip install -e '.[asr]'`."
        )
