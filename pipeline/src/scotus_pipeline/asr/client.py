from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scotus_pipeline.asr.types import WorkerRequest, WorkerResponse


class AsrWorkerClient:
    def __init__(self, *, cwd: Path | None = None, stderr_path: Path | None = None) -> None:
        self.stderr_path = stderr_path
        self._stderr_handle = None
        stderr_target = subprocess.DEVNULL
        if stderr_path is not None:
            stderr_path.parent.mkdir(parents=True, exist_ok=True)
            self._stderr_handle = stderr_path.open("a", encoding="utf-8")
            stderr_target = self._stderr_handle
        self.process = subprocess.Popen(
            [sys.executable, "-m", "scotus_pipeline.asr.worker"],
            cwd=str(cwd) if cwd else None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_target,
            text=True,
        )

    def request(self, request: WorkerRequest) -> WorkerResponse:
        if self.process.stdin is None or self.process.stdout is None:
            raise RuntimeError("ASR worker pipes are not available")
        self.process.stdin.write(request.model_dump_json() + "\n")
        self.process.stdin.flush()
        raw = self.process.stdout.readline()
        if not raw:
            detail = f"; see {self.stderr_path}" if self.stderr_path else ""
            raise RuntimeError(f"ASR worker exited without a response{detail}")
        response = WorkerResponse.model_validate_json(raw)
        if not response.ok:
            raise RuntimeError(response.error or "ASR worker request failed")
        return response

    def close(self) -> None:
        if self.process.poll() is None:
            self.request(WorkerRequest(request_id="shutdown", op="shutdown"))
            self.process.wait(timeout=30)
        for stream in (self.process.stdin, self.process.stdout):
            if stream is not None and not stream.closed:
                stream.close()
        if self._stderr_handle is not None and not self._stderr_handle.closed:
            self._stderr_handle.close()

    def __enter__(self) -> AsrWorkerClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
