from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scotus_pipeline.processors._native import alignment_chunks


class NativeIsolationTests(unittest.TestCase):
    def test_runtime_sources_do_not_reference_root_scripts(self) -> None:
        source_root = Path(__file__).resolve().parents[1] / "src" / "scotus_pipeline"
        banned = ("scripts/", "processor_command", "PythonProcessExecutor")
        offenders: list[str] = []
        for path in sorted(source_root.rglob("*.py")):
            lines = path.read_text(encoding="utf-8").splitlines()
            for token in banned:
                for line_number, line in enumerate(lines, start=1):
                    if token in line and "argument_transcripts" not in line:
                        offenders.append(f"{path.relative_to(source_root)}:{line_number} contains {token!r}")
        self.assertEqual([], offenders)

    def test_chunk_cache_requires_matching_speaker_turns_source(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            chunk_path = root / "chunks" / "case.chunk-000000-000010.json"
            expected_turns = root / "transcripts" / "new.speaker-turns.json"
            stale_turns = root / "transcripts" / "old.speaker-turns.json"
            expected_turns.parent.mkdir(parents=True)
            expected_turns.write_text("[]\n", encoding="utf-8")
            stale_turns.write_text("[]\n", encoding="utf-8")

            alignment_chunks.write_json(
                root / "cache" / "case.000000-000010.whisperx.json",
                {"segments": [{"words": [{"word": "test", "start": 0.0, "end": 0.2}]}]},
            )
            alignment_chunks.write_json(
                chunk_path,
                {
                    "window": {"start": 0.0, "end": 10.0},
                    "turn_window": {"turn_start": 0, "turn_count": 2},
                    "alignment": {"status_counts": {"matched": 1}},
                    "aligned_turns": [{"turn_index": 0, "status": "matched"}],
                    "sources": {"speaker_turns": str(stale_turns)},
                },
            )

            self.assertFalse(
                alignment_chunks.validate_existing_chunk(chunk_path, 0.0, 10.0, 0, 2, expected_turns)
            )

            payload = json.loads(chunk_path.read_text(encoding="utf-8"))
            payload["sources"]["speaker_turns"] = str(expected_turns)
            alignment_chunks.write_json(chunk_path, payload)

            self.assertTrue(
                alignment_chunks.validate_existing_chunk(chunk_path, 0.0, 10.0, 0, 2, expected_turns)
            )


if __name__ == "__main__":
    unittest.main()
