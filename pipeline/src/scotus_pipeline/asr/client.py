from __future__ import annotations

import queue
import subprocess
import sys
import threading
from contextlib import suppress
from pathlib import Path

from scotus_pipeline.asr.types import WorkerRequest, WorkerResponse


class AsrWorkerClient:
    def __init__(
        self,
        *,
        cwd: Path | None = None,
        stderr_path: Path | None = None,
        request_timeout_seconds: float = 1800.0,
    ) -> None:
        if request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        self.request_timeout_seconds = request_timeout_seconds
        self.stderr_path = stderr_path
        self._stderr_handle = None
        self._closed = False
        self._responses: queue.Queue[str | None] = queue.Queue()
        stderr_target = subprocess.DEVNULL
        if stderr_path is not None:
            stderr_path.parent.mkdir(parents=True, exist_ok=True)
            self._stderr_handle = stderr_path.open("a", encoding="utf-8")
            stderr_target = self._stderr_handle
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-m", "scotus_pipeline.asr.worker"],
                cwd=cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr_target,
                text=True,
            )
        except BaseException:
            if self._stderr_handle:
                self._stderr_handle.close()
            raise
        self._reader = threading.Thread(target=self._read_responses, daemon=True)
        self._reader.start()

    def _read_responses(self) -> None:
        try:
            assert self.process.stdout is not None
            for line in self.process.stdout:
                self._responses.put(line)
        finally:
            self._responses.put(None)

    def request(self, request: WorkerRequest, *, timeout_seconds: float | None = None) -> WorkerResponse:
        if self._closed or self.process.stdin is None:
            raise RuntimeError("ASR worker is closed")
        try:
            self.process.stdin.write(request.model_dump_json() + "\n")
            self.process.stdin.flush()
            raw = self._responses.get(timeout=timeout_seconds or self.request_timeout_seconds)
            if raw is None:
                raise RuntimeError("ASR worker exited without a response")
            response = WorkerResponse.model_validate_json(raw)
            if response.request_id != request.request_id:
                raise RuntimeError("ASR worker response ID does not match request")
            if not response.ok:
                raise RuntimeError(response.error or "ASR worker request failed")
            return response
        except queue.Empty as exc:
            self.close(force=True)
            detail = f"; see {self.stderr_path}" if self.stderr_path else ""
            raise TimeoutError(f"ASR worker request timed out{detail}") from exc
        except (BrokenPipeError, OSError):
            self.close(force=True)
            raise

    def close(self, *, force: bool = False) -> None:
        if self._closed:
            return
        try:
            if self.process.poll() is None and not force:
                with suppress(Exception):
                    self.request(WorkerRequest(request_id="shutdown", op="shutdown"), timeout_seconds=5)
            if self.process.poll() is None:
                if force:
                    self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
        finally:
            self._closed = True
            self._reader.join(timeout=5)
            for stream in (self.process.stdin, self.process.stdout):
                if stream:
                    stream.close()
            if self._stderr_handle:
                self._stderr_handle.close()

    def __enter__(self) -> AsrWorkerClient:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close(force=exc_type is not None)
