from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scotus_pipeline.asr.client import AsrWorkerClient
from scotus_pipeline.asr.models import alignment_model, transcription_model
from scotus_pipeline.asr.types import WorkerRequest
from scotus_pipeline.processors.align_transcript import clip_audio, run_whisperx


class AsrLifecycleTests(unittest.TestCase):
    def tearDown(self) -> None:
        alignment_model.cache_clear()
        transcription_model.cache_clear()

    def spawn_fake(self, script: str):
        popen = subprocess.Popen
        return patch(
            "scotus_pipeline.asr.client.subprocess.Popen",
            side_effect=lambda *_args, **kwargs: popen([sys.executable, "-c", script], **kwargs),
        )

    def test_stalled_worker_times_out_and_is_reaped(self) -> None:
        with self.spawn_fake("import time; time.sleep(60)"):
            client = AsrWorkerClient(request_timeout_seconds=0.1)
            with self.assertRaises(TimeoutError):
                client.request(WorkerRequest(request_id="test", op="shutdown"))
            self.assertIsNotNone(client.process.poll())
            self.assertTrue(client.process.stdin.closed)
            self.assertTrue(client.process.stdout.closed)
            client.close()

    def test_mismatched_response_and_worker_exit_fail_clearly(self) -> None:
        scripts = [
            (
                'import sys; sys.stdin.readline(); print(\'{"request_id":"wrong","ok":true}\',flush=True)',
                "does not match",
            ),
            ("import sys; sys.stdin.readline()", "without a response"),
        ]
        for script, message in scripts:
            with self.subTest(message=message), self.spawn_fake(script):
                client = AsrWorkerClient(request_timeout_seconds=2)
                try:
                    with self.assertRaisesRegex(RuntimeError, message):
                        client.request(WorkerRequest(request_id="test", op="shutdown"))
                finally:
                    client.close(force=True)
                self.assertIsNotNone(client.process.poll())

    def test_audio_clip_cache_rejects_changed_source(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "audio.mp3"
            audio.write_bytes(b"original")

            def extract(command, **_kwargs):
                Path(command[-1]).write_bytes(b"clip")

            with (
                patch("scotus_pipeline.processors.align_transcript.shutil.which", return_value="ffmpeg"),
                patch(
                    "scotus_pipeline.processors.align_transcript.subprocess.run", side_effect=extract
                ) as extraction,
            ):
                clip_audio(audio, root / "cache", 0, 15, False)
                clip_audio(audio, root / "cache", 0, 15, False)
                self.assertEqual(1, extraction.call_count)
                audio.write_bytes(b"changed")
                clip_audio(audio, root / "cache", 0, 15, False)
                self.assertEqual(2, extraction.call_count)

    def test_models_are_reused_and_raw_cache_checks_model_and_audio(self) -> None:
        transcription_model.cache_clear()
        alignment_model.cache_clear()
        transcriber = SimpleNamespace(transcribe=Mock(return_value={"segments": [{"text": "hello"}]}))
        whisperx = SimpleNamespace(
            load_model=Mock(return_value=transcriber),
            load_align_model=Mock(return_value=("model", {})),
            load_audio=Mock(return_value="audio"),
            align=Mock(return_value={"segments": [{"words": [{"word": "hello", "start": 0, "end": 1}]}]}),
        )
        torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
        with (
            TemporaryDirectory() as tmp,
            patch.dict("sys.modules", {"torch": torch, "whisperx": whisperx}),
            patch("scotus_pipeline.processors.align_transcript.require_asr_dependencies"),
        ):
            root = Path(tmp)
            clip = root / "clip.wav"
            clip.write_bytes(b"audio")
            for index in range(2):
                run_whisperx(clip, root / f"{index}.json", "tiny.en", False, True)
            self.assertEqual(1, whisperx.load_model.call_count)
            self.assertEqual(1, whisperx.load_align_model.call_count)
            self.assertEqual(2, transcriber.transcribe.call_count)
            run_whisperx(clip, root / "1.json", "tiny.en", False, True)
            self.assertEqual(2, transcriber.transcribe.call_count)
            run_whisperx(clip, root / "1.json", "base.en", False, True)
            self.assertEqual(2, whisperx.load_model.call_count)
            self.assertEqual(3, transcriber.transcribe.call_count)
            clip.write_bytes(b"changed")
            run_whisperx(clip, root / "1.json", "base.en", False, True)
            self.assertEqual(4, transcriber.transcribe.call_count)
