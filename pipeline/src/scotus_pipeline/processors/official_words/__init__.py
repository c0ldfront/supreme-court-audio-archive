"""Official transcript word alignment: cached ASR matching and block fallback."""

from scotus_pipeline.processors.official_words.cache import (
    alignment_matches_current_inputs,
    official_alignment_fingerprint,
)
from scotus_pipeline.processors.official_words.service import run

__all__ = ["run", "alignment_matches_current_inputs", "official_alignment_fingerprint"]
