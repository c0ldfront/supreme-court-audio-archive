from __future__ import annotations

from pathlib import Path
from typing import Any

from scotus_pipeline.io import atomic_write_json as atomic_write_json
from scotus_pipeline.io import atomic_write_text as atomic_write_text
from scotus_pipeline.io import copy_snapshot as copy_snapshot
from scotus_pipeline.io import path_identity as path_identity
from scotus_pipeline.io import read_json as read_json
from scotus_pipeline.io import sha256_file as sha256_file


def assert_same_path(actual: str | Path | None, expected: Path, *, label: str) -> None:
    if actual is None:
        raise ValueError(f"{label} is missing")
    actual_path = Path(str(actual))
    expected_resolved = expected.resolve() if expected.exists() else expected
    actual_resolved = actual_path.resolve() if actual_path.exists() else actual_path
    if actual_resolved != expected_resolved:
        raise ValueError(f"{label} mismatch: expected {expected_resolved}, got {actual_resolved}")


def assert_case_artifact(payload: dict[str, Any], *, audio: Path | None = None, docket: str | None = None) -> None:
    case = payload.get("case")
    if not isinstance(case, dict):
        raise ValueError("artifact is missing case metadata")
    if docket is not None and str(case.get("docket")) != docket:
        raise ValueError(f"artifact docket mismatch: expected {docket}, got {case.get('docket')}")
    if audio is not None:
        audio_file = case.get("audio_file")
        assert_same_path(audio_file, audio, label="artifact case.audio_file")
