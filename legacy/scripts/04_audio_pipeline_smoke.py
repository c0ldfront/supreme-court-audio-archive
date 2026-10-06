#!/usr/bin/env python3
"""Smoke-test local audio ML tooling against an archived SCOTUS MP3."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from huggingface_hub.errors import GatedRepoError

from env_utils import load_dotenv


DEFAULT_ARCHIVE = Path("archive/scotus-audio")
DEFAULT_OUTPUT = Path("audio-test-output")
DEFAULT_DIARIZATION_MODEL = "pyannote-community/speaker-diarization-community-1"


def find_audio(path: Path | None) -> Path:
    if path:
        if not path.exists():
            raise FileNotFoundError(path)
        return path

    matches = sorted(DEFAULT_ARCHIVE.glob("**/*.mp3"))
    if not matches:
        raise FileNotFoundError(
            f"no MP3 files found under {DEFAULT_ARCHIVE}; archive one record first"
        )
    return matches[0]


def run_ffmpeg_clip(audio_path: Path, output_dir: Path, seconds: int, overwrite: bool) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required but was not found on PATH")

    output_dir.mkdir(parents=True, exist_ok=True)
    clip_path = output_dir / f"{audio_path.stem}_{seconds}s.wav"
    if clip_path.exists() and not overwrite:
        return clip_path

    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y" if overwrite else "-n",
        "-i",
        str(audio_path),
        "-t",
        str(seconds),
        "-ac",
        "1",
        "-ar",
        "16000",
        str(clip_path),
    ]
    subprocess.run(command, check=True)
    return clip_path


def check_torch() -> dict[str, Any]:
    import torch

    payload: dict[str, Any] = {
        "torch_version": torch.__version__,
        "torch_cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
    }
    if torch.cuda.is_available():
        payload["device"] = torch.cuda.get_device_name(0)
        payload["capability"] = torch.cuda.get_device_capability(0)
    return payload


def run_whisperx(clip_path: Path, output_dir: Path, model_size: str) -> Path:
    import torch
    import whisperx

    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    model = whisperx.load_model(model_size, device=device, compute_type=compute_type, language="en")
    result = model.transcribe(str(clip_path), batch_size=8, language="en")

    output_path = output_dir / f"{clip_path.stem}.whisperx.json"
    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def run_pyannote(
    clip_path: Path,
    output_dir: Path,
    token: str | None,
    num_speakers: int | None,
    model_id: str,
) -> Path:
    import torch
    from pyannote.audio import Pipeline

    try:
        pipeline = Pipeline.from_pretrained(model_id, token=token)
    except GatedRepoError as exc:
        raise RuntimeError(
            f"pyannote model access is still gated for {model_id}. "
            f"Open https://huggingface.co/{model_id} while logged in, "
            "accept/request access if required, then rerun "
            "scripts/03_check_hf_pyannote_access.py."
        ) from exc
    if torch.cuda.is_available():
        pipeline.to(torch.device("cuda"))

    kwargs: dict[str, Any] = {}
    if num_speakers:
        kwargs["num_speakers"] = num_speakers

    output = pipeline(str(clip_path), **kwargs)
    diarization = getattr(output, "speaker_diarization", output)
    segments = [
        {"start": turn.start, "end": turn.end, "speaker": speaker}
        for turn, _, speaker in diarization.itertracks(yield_label=True)
    ]

    safe_model = model_id.replace("/", "_")
    output_path = output_dir / f"{clip_path.stem}.{safe_model}.diarization.json"
    output_path.write_text(json.dumps(segments, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test local SCOTUS audio processing.")
    parser.add_argument("--audio", type=Path, help="MP3 file to test; defaults to first archived MP3")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="test output directory")
    parser.add_argument("--clip-seconds", type=int, default=120, help="seconds to extract for the test clip")
    parser.add_argument("--overwrite", action="store_true", help="overwrite existing test clip/results")
    parser.add_argument("--check-gpu", action="store_true", help="print PyTorch/CUDA status")
    parser.add_argument("--transcribe", action="store_true", help="run WhisperX on the test clip")
    parser.add_argument("--model-size", default="tiny.en", help="WhisperX model size for smoke test")
    parser.add_argument("--diarize", action="store_true", help="run pyannote diarization on the test clip")
    parser.add_argument(
        "--diarization-model",
        default=DEFAULT_DIARIZATION_MODEL,
        help=f"pyannote pipeline model id (default: {DEFAULT_DIARIZATION_MODEL})",
    )
    parser.add_argument("--num-speakers", type=int, help="known speaker count for diarization")
    return parser


def main() -> int:
    load_dotenv()
    args = build_parser().parse_args()
    audio_path = find_audio(args.audio)
    output_dir = args.output.resolve()
    clip_path = run_ffmpeg_clip(audio_path, output_dir, args.clip_seconds, args.overwrite)
    print(f"audio: {audio_path}")
    print(f"clip:  {clip_path}")

    if args.check_gpu:
        print(json.dumps(check_torch(), indent=2, sort_keys=True))

    if args.transcribe:
        result_path = run_whisperx(clip_path, output_dir, args.model_size)
        print(f"whisperx: {result_path}")

    if args.diarize:
        token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
        result_path = run_pyannote(
            clip_path,
            output_dir,
            token,
            args.num_speakers,
            args.diarization_model,
        )
        print(f"diarization: {result_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
