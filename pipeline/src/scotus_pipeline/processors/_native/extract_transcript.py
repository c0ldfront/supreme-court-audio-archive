#!/usr/bin/env python3
"""Extract SCOTUS transcript PDF text and speaker turns."""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

SPEAKER_NAME_CHARS = r"A-ZÀ-ÖØ-Þ"
SPEAKER_RE = re.compile(
    r"^(?P<speaker>(?:CHIEF JUSTICE|JUSTICE|MR\.|MS\.|MRS\.|GENERAL|PROFESSOR|DR\.)"
    rf"(?: [{SPEAKER_NAME_CHARS}][{SPEAKER_NAME_CHARS}.\-']+)*):\s*(?P<text>.*)$"
)
LINE_NUMBER_RE = re.compile(r"^\s*(?:\d{1,2}\s+)?(?P<text>.*)$")


@dataclass(frozen=True)
class SpeakerTurn:
    index: int
    speaker: str
    text: str
    start_line: int
    end_line: int


def find_pdf(path: Path | None) -> Path:
    if path:
        if not path.exists():
            raise FileNotFoundError(path)
        return path
    raise ValueError("pdf path is required")


def extract_pdf_text(pdf_path: Path, layout: bool) -> str:
    pdftotext = shutil.which("pdftotext")
    if not pdftotext:
        raise RuntimeError("pdftotext is required but was not found on PATH")

    command = [pdftotext]
    if layout:
        command.append("-layout")
    command.extend([str(pdf_path), "-"])
    return subprocess.check_output(command, text=True, stderr=subprocess.PIPE)


def clean_transcript_line(raw_line: str) -> str:
    line = raw_line.replace("\f", "").rstrip()
    if not line.strip():
        return ""

    match = LINE_NUMBER_RE.match(line)
    text = match.group("text") if match else line
    text = re.sub(r"\s+", " ", text).strip()
    submitted_marker = "case was submitted.)"
    if submitted_marker in text:
        text = text[: text.index(submitted_marker) + len(submitted_marker)]

    if "Official - Subject to Final Review" in line:
        return ""
    if "Heritage Reporting Corporation" in line:
        return ""
    if re.fullmatch(r"ORAL ARGUMENT OF:?\s*(PAGE:?)?", text):
        return ""
    if re.fullmatch(r"ORAL ARGUMENT OF .+", text):
        return ""
    if re.fullmatch(r"(REBUTTAL )?ARGUMENT OF .+", text):
        return ""
    if re.fullmatch(r"CLOSING ARGUMENT OF .+", text):
        return ""
    if re.fullmatch(r"ON BEHALF OF .+", text):
        return ""
    if re.fullmatch(r"\s*\d{1,3}\s*", line):
        return ""
    return text


def parse_speaker_turns(text: str) -> list[SpeakerTurn]:
    turns: list[SpeakerTurn] = []
    current_speaker: str | None = None
    current_text: list[str] = []
    start_line = 0
    last_line = 0

    def flush() -> None:
        nonlocal current_speaker, current_text, start_line, last_line
        if current_speaker and current_text:
            turns.append(
                SpeakerTurn(
                    index=len(turns),
                    speaker=current_speaker,
                    text=re.sub(r"\s+", " ", " ".join(current_text)).strip(),
                    start_line=start_line,
                    end_line=last_line,
                )
            )
        current_speaker = None
        current_text = []
        start_line = 0
        last_line = 0

    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = clean_transcript_line(raw_line)
        if not line:
            continue

        match = SPEAKER_RE.match(line)
        if match:
            flush()
            current_speaker = match.group("speaker")
            current_text = [match.group("text")]
            start_line = line_no
            last_line = line_no
            continue

        if current_speaker:
            current_text.append(line)
            last_line = line_no
            if "was submitted.)" in line:
                break

    flush()
    return turns
