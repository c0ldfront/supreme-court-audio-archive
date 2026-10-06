from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scotus_pipeline.artifacts import (
    validate_official_word_alignment_file,
    validate_timeline_file,
)
from scotus_pipeline.asr.client import AsrWorkerClient
from scotus_pipeline.asr.types import WorkerRequest
from scotus_pipeline.io import atomic_write_json, sha256_file
from scotus_pipeline.models import StepResult, StepStatus
from scotus_pipeline.processors import official_words
from scotus_pipeline.processors.contracts import OfficialWordsProcessorInput, ViewerProcessorInput
from scotus_pipeline.processors.viewer_bundle import build_viewer_bundle, scrub_local_paths
from scotus_pipeline.state import PipelineState


class PipelineContractTests(unittest.TestCase):
    def test_bundle_rejects_profiles_from_another_docket(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "audio.mp3"
            audio.write_bytes(b"audio")
            timeline = root / "timeline.json"
            profiles = root / "profiles.json"
            atomic_write_json(
                timeline,
                {
                    "case": {"docket": "24-624", "audio_file": str(audio)},
                    "duration": 1,
                },
            )
            atomic_write_json(
                profiles,
                {
                    "case": {"docket": "24-999", "audio_file": str(audio)},
                    "profiles": [],
                },
            )
            output = root / "bundle.json"
            output.write_text("previous bundle")
            with self.assertRaisesRegex(ValueError, "profile docket mismatch"):
                build_viewer_bundle(
                    ViewerProcessorInput(
                        timeline=timeline,
                        profiles=profiles,
                        output=output,
                    )
                )
            self.assertEqual(output.read_text(), "previous bundle")

    def test_public_bundle_scrubs_path_lists_and_retains_urls(self) -> None:
        payload = {
            "sources": {
                "transcript_file": ["/home/private.pdf", "https://court/file.pdf"],
                "nested": {"cache_dir": "/home/cache"},
            }
        }
        scrub_local_paths(payload)
        self.assertEqual(payload["sources"]["transcript_file"], [None, "https://court/file.pdf"])
        self.assertIsNone(payload["sources"]["nested"]["cache_dir"])

    def test_atomic_json_writes_schema_version(self) -> None:
        with TemporaryDirectory() as tmp:
            output = Path(tmp) / "artifact.json"

            atomic_write_json(output, {"case": {"docket": "24-624"}, "items": []})

            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual("1", payload["schema_version"])
            self.assertEqual("24-624", payload["case"]["docket"])
            self.assertFalse(output.with_suffix(".json.part").exists())

    def test_artifact_validator_rejects_cross_case_audio(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected_audio = root / "expected.mp3"
            stale_audio = root / "stale.mp3"
            timeline = root / "timeline.json"
            expected_audio.write_bytes(b"expected")
            stale_audio.write_bytes(b"stale")
            atomic_write_json(
                timeline,
                {
                    "case": {
                        "docket": "24-624",
                        "name": "Case v. Montana",
                        "audio_file": str(stale_audio),
                    },
                    "duration": 1.0,
                    "segments": [],
                },
            )

            with self.assertRaises(ValueError):
                validate_timeline_file(timeline, audio=expected_audio)

    def test_official_word_alignment_is_schema_checked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "case.mp3"
            alignment = root / "official.json"
            audio.write_bytes(b"audio")
            atomic_write_json(
                alignment,
                {
                    "case": {"docket": "24-624", "audio_file": str(audio)},
                    "sources": {},
                    "summary": {"matched_turns": 0},
                    "turns": [],
                },
            )

            parsed = validate_official_word_alignment_file(alignment, audio=audio)

            self.assertEqual("1", parsed.schema_version)
            self.assertEqual("24-624", parsed.case.docket)

    def test_official_word_alignment_reuse_requires_source_fingerprint(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "case.mp3"
            timeline_path = root / "timeline.json"
            audio.write_bytes(b"audio")
            timeline = {
                "case": {"docket": "24-624", "audio_file": str(audio)},
                "duration": 1.0,
                "segments": [
                    {
                        "turn_index": 0,
                        "speaker": "CHIEF JUSTICE ROBERTS",
                        "text": "We will hear argument next in Case 24-624.",
                    }
                ],
            }
            args = OfficialWordsProcessorInput(
                timeline=timeline_path,
                audio=audio,
                output_alignment=root / "official.json",
                output_timeline=timeline_path,
                cache_dir=root / "cache",
            )
            fingerprint = official_words.official_alignment_fingerprint(timeline, audio, args)

            self.assertFalse(official_words.alignment_matches_current_inputs({}, timeline, audio, args))
            self.assertTrue(
                official_words.alignment_matches_current_inputs(
                    {"source_fingerprint": fingerprint},
                    timeline,
                    audio,
                    args,
                )
            )

    def test_resume_state_depends_on_current_hashes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "case.toml"
            input_path = root / "input.txt"
            output_path = root / "output.txt"
            config_path.write_text("[case]\ndocket = '24-624'\n", encoding="utf-8")
            input_path.write_text("first", encoding="utf-8")
            output_path.write_text("done", encoding="utf-8")
            input_hashes = {str(input_path): sha256_file(input_path)}
            output_hashes = {str(output_path): sha256_file(output_path)}
            state = PipelineState(run_id="test", config_path=config_path)
            state.record(
                StepResult(
                    step_id="sample",
                    status=StepStatus.succeeded,
                    started_at="2026-05-03T00:00:00+00:00",
                    finished_at="2026-05-03T00:00:01+00:00",
                    duration_seconds=1.0,
                    outputs=[output_path],
                    input_hashes=input_hashes,
                    output_hashes=output_hashes,
                )
            )

            self.assertTrue(state.completed("sample", input_hashes=input_hashes, output_hashes=output_hashes))

            input_path.write_text("changed", encoding="utf-8")
            changed_hashes = {str(input_path): sha256_file(input_path)}
            self.assertFalse(
                state.completed("sample", input_hashes=changed_hashes, output_hashes=output_hashes)
            )

    def test_asr_worker_shutdown_protocol(self) -> None:
        client = AsrWorkerClient(cwd=Path(__file__).resolve().parents[1])
        try:
            response = client.request(WorkerRequest(request_id="shutdown-test", op="shutdown"))
            self.assertTrue(response.ok)
            self.assertEqual("shutdown-test", response.request_id)
            client.process.wait(timeout=10)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
