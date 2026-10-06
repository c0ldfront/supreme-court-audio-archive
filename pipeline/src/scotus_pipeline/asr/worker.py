from __future__ import annotations

import sys
import traceback
from contextlib import redirect_stdout, suppress

from scotus_pipeline.asr.types import WorkerRequest, WorkerResponse
from scotus_pipeline.io import sha256_file
from scotus_pipeline.processors import align_transcript
from scotus_pipeline.processors.contracts import AlignTranscriptProcessorInput


def handle(request: WorkerRequest) -> WorkerResponse:
    if request.op == "shutdown":
        return WorkerResponse(request_id=request.request_id, ok=True)
    required = {
        "case_dir": request.case_dir,
        "audio": request.audio,
        "audio_sha256": request.audio_sha256,
        "speaker_turns": request.speaker_turns,
        "output_dir": request.output_dir,
        "cache_dir": request.cache_dir,
        "output": request.output,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError(f"missing ASR alignment fields: {', '.join(missing)}")
    assert request.case_dir is not None
    assert request.audio is not None
    assert request.audio_sha256 is not None
    assert request.speaker_turns is not None
    assert request.output_dir is not None
    assert request.cache_dir is not None
    assert request.output is not None
    actual_audio_sha256 = sha256_file(request.audio)
    if actual_audio_sha256 != request.audio_sha256:
        raise ValueError(f"audio sha256 mismatch: expected {request.audio_sha256}, got {actual_audio_sha256}")
    with redirect_stdout(sys.stderr):
        output = align_transcript.build_alignment(
            AlignTranscriptProcessorInput(
                case_dir=request.case_dir,
                metadata=request.metadata,
                audio=request.audio,
                speaker_turns=request.speaker_turns,
                output_dir=request.output_dir,
                cache_dir=request.cache_dir,
                output=request.output,
                start=request.start,
                duration=request.duration,
                full=False,
                turn_start=request.turn_start,
                turn_count=request.turn_count,
                model_size=request.model_size,
                max_segments_per_turn=request.max_segments_per_turn,
                min_score=request.min_score,
                overwrite=request.overwrite,
                no_word_alignment=request.no_word_alignment,
            )
        )
    return WorkerResponse(request_id=request.request_id, ok=True, output=output)


def main() -> int:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = WorkerRequest.model_validate_json(line)
            response = handle(request)
        except Exception:
            request_id = "unknown"
            with suppress(Exception):
                request_id = WorkerRequest.model_validate_json(line).request_id
            response = WorkerResponse(request_id=request_id, ok=False, error=traceback.format_exc())
        print(response.model_dump_json(), flush=True)
        if line and response.ok:
            with suppress(Exception):
                if WorkerRequest.model_validate_json(line).op == "shutdown":
                    return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
