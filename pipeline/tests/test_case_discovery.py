from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import test_resume_and_qa

from scotus_pipeline.cases import build_artifacts_from_case_dir, case_output_key, find_case_by_docket


class CaseDiscoveryTests(unittest.TestCase):
    def test_output_key_is_a_safe_single_directory(self) -> None:
        for value in (".", "..", "...", "../", "/", " 24-624 ", "24-ABC"):
            with self.subTest(value=value):
                key = case_output_key({"docket": value}, None, Path("source"))
                self.assertNotIn(key, ("", ".", ".."))
                self.assertEqual(Path(key).name, key)
                self.assertEqual(key, key.lower())

    def test_explicit_case_rejects_wrong_docket_and_non_object_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = test_resume_and_qa.ResumeAndQaTests().context(root)
            metadata = root / "source/metadata.json"
            for payload, message in (({"docket": "24-999"}, "docket mismatch"), ([], "must be an object")):
                metadata.write_text(json.dumps(payload))
                with self.assertRaisesRegex(ValueError, message):
                    build_artifacts_from_case_dir(context, metadata.parent)

    def test_relocated_archive_prefers_local_sources_and_resolves_relative_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = test_resume_and_qa.ResumeAndQaTests().context(root)
            case = root / "source"
            stale = root / "old.mp3"
            stale.write_bytes(b"wrong recording")
            (case / "nested").mkdir()
            (case / "nested/transcript.pdf").write_bytes(b"correct pdf")
            (case / "metadata.json").write_text(
                json.dumps(
                    {
                        "docket": "24-624",
                        "audio_file": {"path": str(stale)},
                        "transcript_file": {"path": "nested/transcript.pdf"},
                    }
                )
            )
            artifacts = build_artifacts_from_case_dir(context, case)
            self.assertEqual(artifacts.audio, case / "audio.mp3")
            self.assertEqual(artifacts.transcript_pdf, case / "nested/transcript.pdf")

    def test_discovery_skips_malformed_metadata(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            context = test_resume_and_qa.ResumeAndQaTests().context(root)
            sources = context.resolve(context.config.paths.sources_dir)
            sources.mkdir(parents=True, exist_ok=True)
            (root / "source").rename(sources / "source")
            for name, payload in (("list", []), ("bad-term", {"docket": "24-624", "term": "oops"})):
                directory = sources / name
                directory.mkdir()
                (directory / "metadata.json").write_text(json.dumps(payload))
            self.assertEqual(find_case_by_docket(context).case_dir, sources / "source")
