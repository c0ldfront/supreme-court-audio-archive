from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".part")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(path)


def atomic_write_json(path: Path, payload: Any) -> None:
    if isinstance(payload, dict) and "schema_version" not in payload:
        payload = {"schema_version": "1", **payload}
    atomic_write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_existing(paths: list[Path], *, require_all: bool = False) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in paths:
        if path.exists() and path.is_file():
            hashes[str(path)] = sha256_file(path)
        elif path.is_dir():
            digest = hashlib.sha256()
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    digest.update(child.relative_to(path).as_posix().encode())
                    digest.update(sha256_file(child).encode())
            hashes[str(path)] = digest.hexdigest()
        elif require_all:
            raise FileNotFoundError(f"declared pipeline input does not exist: {path}")
    return hashes


def copy_snapshot(source: Path, snapshot: Path) -> None:
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = snapshot.with_suffix(snapshot.suffix + ".part")
    shutil.copy2(source, tmp_path)
    os.replace(tmp_path, snapshot)


def path_identity(path: Path) -> str:
    return str(path.resolve()) if path.exists() else str(path)


def assert_same_path(actual: str | Path | None, expected: Path, *, label: str) -> None:
    if actual is None:
        raise ValueError(f"{label} is missing")
    actual_path = Path(str(actual))
    expected_resolved = expected.resolve() if expected.exists() else expected
    actual_resolved = actual_path.resolve() if actual_path.exists() else actual_path
    if actual_resolved != expected_resolved:
        raise ValueError(f"{label} mismatch: expected {expected_resolved}, got {actual_resolved}")


def assert_case_artifact(
    payload: dict[str, Any], *, audio: Path | None = None, docket: str | None = None
) -> None:
    case = payload.get("case")
    if not isinstance(case, dict):
        raise ValueError("artifact is missing case metadata")
    if docket is not None and str(case.get("docket")) != docket:
        raise ValueError(f"artifact docket mismatch: expected {docket}, got {case.get('docket')}")
    if audio is not None:
        audio_file = case.get("audio_file")
        assert_same_path(audio_file, audio, label="artifact case.audio_file")
