from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scotus_pipeline.io import atomic_write_json
from scotus_pipeline.processors.contracts import OfficialWordsProcessorInput
from scotus_pipeline.processors.official_words import run
from scotus_pipeline.processors.official_words.service import build_word_alignment


class WordAlignmentTests(unittest.TestCase):
    def case(self, root: Path, *, method: str = "asr") -> OfficialWordsProcessorInput:
        audio = root / "audio.mp3"
        audio.write_bytes(b"synthetic-audio")
        timeline = root / "timeline.json"
        atomic_write_json(
            timeline,
            {
                "case": {"docket": "24-624", "audio_file": str(audio)},
                "duration": 3.0,
                "segments": [
                    {
                        "turn_index": 0,
                        "speaker": "JUSTICE",
                        "start": 0.0,
                        "end": 3.0,
                        "text": "The Court requires evidence.",
                    }
                ],
            },
        )
        cache = root / "cache"
        atomic_write_json(
            cache / "000000-000300.whisperx.json",
            {
                "segments": [
                    {"start": 0.0, "end": 3.0, "text": "The Court requires evidence.", "words": self.words()}
                ],
            },
        )
        return OfficialWordsProcessorInput(
            timeline=timeline,
            audio=audio,
            output_alignment=root / "alignment.json",
            output_timeline=root / "refined.json",
            cache_dir=cache,
            method=method,
        )

    def words(self) -> list[dict]:
        return [
            {"word": word, "start": 0.2 + index * 0.5, "end": 0.6 + index * 0.5, "score": 0.95}
            for index, word in enumerate(["The", "Court", "requires", "evidence."])
        ]

    def assert_aligned(self, args: OfficialWordsProcessorInput) -> None:
        alignment = json.loads(args.output_alignment.read_text())
        timeline = json.loads(args.output_timeline.read_text())
        self.assertEqual("aligned", alignment["turns"][0]["status"])
        self.assertEqual(
            ["The", "Court", "requires", "evidence."],
            [word["word"] for word in timeline["segments"][0]["words"]],
        )
        self.assertAlmostEqual(0.2, timeline["segments"][0]["start"])
        self.assertAlmostEqual(2.1, timeline["segments"][0]["end"])
        self.assertEqual("1", alignment["schema_version"])
        self.assertEqual("1", timeline["schema_version"])

    def test_asr_alignment_runs_and_reuses_valid_cache(self) -> None:
        with TemporaryDirectory() as tmp:
            args = self.case(Path(tmp))
            with patch(
                "scotus_pipeline.processors.official_words.service.build_word_alignment",
                wraps=build_word_alignment,
            ) as build:
                self.assertEqual((args.output_alignment, args.output_timeline), run(args))
                self.assert_aligned(args)
                run(args)
                self.assertEqual(1, build.call_count)
                timeline = json.loads(args.timeline.read_text())
                timeline["segments"][0]["text"] = "The Court requires more evidence."
                atomic_write_json(args.timeline, timeline)
                run(args)
                self.assertEqual(2, build.call_count)

    def test_block_alignment_runs_with_injected_model(self) -> None:
        with TemporaryDirectory() as tmp:
            args = self.case(Path(tmp), method="block")
            torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False))
            whisperx = SimpleNamespace(
                load_align_model=Mock(return_value=("model", {})),
                load_audio=Mock(return_value="audio"),
                align=Mock(return_value={"word_segments": self.words()}),
            )
            with (
                patch.dict("sys.modules", {"torch": torch, "whisperx": whisperx}),
                patch("scotus_pipeline.processors.official_words.blocks.require_asr_dependencies"),
            ):
                run(args)
            self.assert_aligned(args)
            whisperx.load_align_model.assert_called_once_with(
                language_code="en", device="cpu", model_name=None
            )
            whisperx.align.assert_called_once()

    def test_invalid_cache_is_rebuilt_without_changing_source_timeline(self) -> None:
        with TemporaryDirectory() as tmp:
            args = self.case(Path(tmp))
            original = copy.deepcopy(json.loads(args.timeline.read_text()))
            atomic_write_json(args.output_alignment, {"source_fingerprint": {"docket": "another-case"}})
            run(args)
            self.assert_aligned(args)
            self.assertEqual(original, json.loads(args.timeline.read_text()))
