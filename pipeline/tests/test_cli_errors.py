from __future__ import annotations

import contextlib
import io
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scotus_pipeline.asr.smoke import run_smoke
from scotus_pipeline.cli import main


class CliErrorTests(unittest.TestCase):
    def test_invalid_yaml_and_ffmpeg_failure_return_clean_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "broken.yaml"
            config.write_text("case: [unterminated")
            with contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(main(["validate", str(config)]), 2)
            self.assertIn("Error:", output.getvalue())
            self.assertNotIn("Traceback", output.getvalue())
        with patch(
            "scotus_pipeline.asr.smoke.run_smoke", side_effect=subprocess.CalledProcessError(1, "ffmpeg")
        ):
            with contextlib.redirect_stderr(io.StringIO()) as output:
                self.assertEqual(main(["smoke-asr", "audio.mp3"]), 2)
            self.assertNotIn("Traceback", output.getvalue())

    def test_smoke_rejects_nonfinite_duration_and_timeout_before_processing(self) -> None:
        for value in (float("nan"), float("inf"), -1.0, 0.0):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    run_smoke(Path("missing.mp3"), seconds=value)
                with self.assertRaises(ValueError):
                    run_smoke(Path("missing.mp3"), timeout_seconds=value)
