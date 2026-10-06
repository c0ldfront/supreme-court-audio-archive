"""Run real uncached inference on a short recording through the ASR worker."""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
from pathlib import Path
from uuid import uuid4

from scotus_pipeline.asr.client import AsrWorkerClient
from scotus_pipeline.asr.types import WorkerRequest
from scotus_pipeline.io import atomic_write_json, sha256_file


def run_smoke(
    audio: Path, *, seconds: float = 15.0, model_size: str = "tiny.en", timeout_seconds: float = 300.0
) -> dict:
    if not math.isfinite(seconds) or seconds <= 0 or seconds > 60:
        raise ValueError("Smoke duration must be greater than zero and at most 60 seconds")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("Smoke timeout must be finite and greater than zero")
    audio = audio.resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="scotus-asr-smoke-") as directory:
        root = Path(directory)
        clip = root / "audio.mp3"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(audio), "-t", str(seconds), "-ac", "1", str(clip)],
            check=True,
        )
        metadata = root / "metadata.json"
        turns = root / "turns.json"
        atomic_write_json(metadata, {"docket": "smoke", "case_name": "ASR smoke test"})
        atomic_write_json(turns, [{"speaker": "SPEAKER", "text": "We will hear argument next.", "index": 0}])
        output = root / "alignment.json"
        cache = root / "cache"
        with AsrWorkerClient(
            stderr_path=root / "worker.log", request_timeout_seconds=timeout_seconds
        ) as client:
            try:
                client.request(
                    WorkerRequest(
                        request_id=uuid4().hex,
                        case_dir=root,
                        metadata=metadata,
                        audio=clip,
                        audio_sha256=sha256_file(clip),
                        speaker_turns=turns,
                        output_dir=root,
                        cache_dir=cache,
                        output=output,
                        duration=seconds,
                        turn_count=1,
                        model_size=model_size,
                        overwrite=True,
                    )
                )
            except Exception as exc:
                log = (root / "worker.log").read_text(errors="replace")[-6000:]
                raise RuntimeError(f"Fresh ASR smoke test failed: {exc}\n{log}") from exc
        paths = list(cache.glob("*.whisperx.json"))
        if not paths or not output.exists():
            raise RuntimeError("ASR worker did not produce inference artifacts")
        data = json.loads(paths[0].read_text())
        segments = data.get("segments", [])
        words = [
            word
            for segment in segments
            for word in segment.get("words", [])
            if isinstance(word, dict) and word.get("start") is not None and word.get("end") is not None
        ]
        if not segments or not words:
            raise RuntimeError("Fresh inference produced no timed speech words")
        return {
            "fresh_inference": True,
            "model": model_size,
            "seconds": seconds,
            "segments": len(segments),
            "timed_words": len(words),
        }
