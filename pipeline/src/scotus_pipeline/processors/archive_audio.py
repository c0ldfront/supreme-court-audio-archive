#!/usr/bin/env python3
"""Archive U.S. Supreme Court oral argument audio from supremecourt.gov.

The Court's public audio index currently advertises terms from 2010 onward.
This processor discovers those term pages, follows each audio detail page, and
downloads the MP3 files into a stable directory layout with metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen

from scotus_pipeline.io import atomic_write_json
from scotus_pipeline.processors.contracts import ArchiveProcessorInput

BASE_URL = "https://www.supremecourt.gov"
ARGUMENT_AUDIO_URL = f"{BASE_URL}/oral_arguments/argument_audio"
USER_AGENT = "scotus-pipeline/0.1 (+https://www.supremecourt.gov/)"


@dataclass(frozen=True)
class IndexRecord:
    term: int
    detail_id: str
    docket: str
    case_name: str
    date_argued: str
    detail_url: str
    session: str | None = None


@dataclass(frozen=True)
class DetailRecord:
    term: int
    detail_id: str
    docket: str
    case_name: str
    date_argued: str
    date_argued_iso: str | None
    detail_url: str
    audio_url: str | None
    transcript_url: str | None
    session: str | None = None


class TermIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.terms: set[int] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href") or ""
        parsed = urlparse(href)
        if parsed.fragment:
            return
        path = parsed.path.rstrip("/")
        if re.fullmatch(r"20\d{2}", path):
            self.terms.add(int(path))
            return
        match = re.fullmatch(r".*/oral_arguments/argument_audio/(20\d{2})", path)
        if match:
            self.terms.add(int(match.group(1)))


class ArgumentAudioIndexParser(HTMLParser):
    def __init__(self, term: int, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.term = term
        self.page_url = page_url
        self.records: list[IndexRecord] = []
        self.session: str | None = None
        self._in_tr = False
        self._in_td = False
        self._in_a = False
        self._cell_text: list[str] = []
        self._cell_links: list[dict[str, str]] = []
        self._row_cells: list[dict[str, object]] = []
        self._current_href: str | None = None
        self._current_anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "tr":
            self._in_tr = True
            self._row_cells = []
        elif self._in_tr and tag == "td":
            self._in_td = True
            self._cell_text = []
            self._cell_links = []
        elif self._in_td and tag == "a":
            self._in_a = True
            self._current_href = attrs_dict.get("href")
            self._current_anchor_text = []

    def handle_data(self, data: str) -> None:
        cleaned = normalize_space(data)
        if not cleaned:
            return
        if self._in_td:
            self._cell_text.append(cleaned)
            if self._in_a:
                self._current_anchor_text.append(cleaned)
        elif "Argument Session:" in cleaned:
            self.session = cleaned

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_a:
            if self._current_href:
                self._cell_links.append(
                    {
                        "href": self._current_href,
                        "text": normalize_space(" ".join(self._current_anchor_text)),
                    }
                )
            self._in_a = False
            self._current_href = None
            self._current_anchor_text = []
        elif tag == "td" and self._in_td:
            self._row_cells.append(
                {
                    "text": normalize_space(" ".join(self._cell_text)),
                    "links": self._cell_links[:],
                }
            )
            self._in_td = False
            self._cell_text = []
            self._cell_links = []
        elif tag == "tr" and self._in_tr:
            self._record_row()
            self._in_tr = False
            self._row_cells = []

    def _record_row(self) -> None:
        if len(self._row_cells) < 2:
            return

        first_cell = self._row_cells[0]
        links = first_cell.get("links") or []
        audio_link = None
        for link in links:
            href = str(link.get("href", ""))
            if "/audio/" in href or "../audio/" in href:
                audio_link = link
                break
        if not audio_link:
            return

        detail_url = urljoin(self.page_url, str(audio_link["href"]))
        parsed = urlparse(detail_url)
        detail_id = unquote(parsed.path.rstrip("/").split("/")[-1])
        docket = normalize_space(str(audio_link.get("text") or detail_id))
        text = normalize_space(str(first_cell.get("text") or ""))
        case_name = text
        if docket and text.startswith(docket):
            case_name = normalize_space(text[len(docket) :])

        date_argued = ""
        for cell in self._row_cells[1:]:
            match = re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", str(cell.get("text", "")))
            if match:
                date_argued = match.group(0)
                break

        self.records.append(
            IndexRecord(
                term=self.term,
                detail_id=detail_id,
                docket=docket,
                case_name=case_name,
                date_argued=date_argued,
                detail_url=detail_url,
                session=self.session,
            )
        )


class AudioDetailParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.text: list[str] = []
        self.audio_urls: list[str] = []
        self.transcript_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        for attr in ("href", "src"):
            raw_url = attrs_dict.get(attr)
            if not raw_url:
                continue
            absolute_url = urljoin(self.page_url, raw_url)
            lowered = absolute_url.lower()
            if lowered.endswith(".mp3"):
                self.audio_urls.append(absolute_url)
            elif lowered.endswith(".pdf") and "/oral_arguments/argument_transcripts/" in lowered:
                self.transcript_urls.append(absolute_url)

    def handle_data(self, data: str) -> None:
        cleaned = normalize_space(data)
        if cleaned:
            self.text.append(cleaned)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def safe_slug(value: str, max_length: int = 96) -> str:
    value = unquote(value).lower()
    value = value.replace("&", " and ")
    value = re.sub(r"[^a-z0-9._-]+", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-._")
    if not value:
        value = "record"
    return value[:max_length].strip("-._") or "record"


def date_to_iso(value: str) -> str | None:
    for fmt in ("%m/%d/%y", "%m/%d/%Y"):
        try:
            parsed = datetime.strptime(value, fmt)
        except ValueError:
            continue
        return parsed.date().isoformat()
    return None


def request_bytes(url: str, timeout: int, retries: int, delay: float) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(delay * attempt)
    raise RuntimeError(f"failed to fetch {url}: {last_error}") from last_error


def fetch_text(url: str, timeout: int, retries: int, delay: float) -> str:
    raw = request_bytes(url, timeout=timeout, retries=retries, delay=delay)
    return raw.decode("utf-8", errors="replace")


def discover_terms(timeout: int, retries: int, delay: float) -> list[int]:
    html_text = fetch_text(ARGUMENT_AUDIO_URL, timeout=timeout, retries=retries, delay=delay)
    parser = TermIndexParser()
    parser.feed(html_text)
    return sorted(parser.terms, reverse=True)


def fetch_index_records(term: int, timeout: int, retries: int, delay: float) -> list[IndexRecord]:
    page_url = f"{ARGUMENT_AUDIO_URL}/{term}"
    html_text = fetch_text(page_url, timeout=timeout, retries=retries, delay=delay)
    parser = ArgumentAudioIndexParser(term=term, page_url=page_url)
    parser.feed(html_text)
    deduped: dict[str, IndexRecord] = {}
    for record in parser.records:
        deduped[record.detail_url] = record
    return list(deduped.values())


def fetch_detail(record: IndexRecord, timeout: int, retries: int, delay: float) -> DetailRecord:
    html_text = fetch_text(record.detail_url, timeout=timeout, retries=retries, delay=delay)
    parser = AudioDetailParser(page_url=record.detail_url)
    parser.feed(html_text)

    docket = record.docket
    case_name = record.case_name
    date_argued = record.date_argued
    text = parser.text

    for index, chunk in enumerate(text):
        if chunk.startswith("Docket Number:"):
            parsed_docket = normalize_space(chunk.split(":", 1)[1])
            if parsed_docket:
                docket = parsed_docket
            if index > 0 and text[index - 1] != "Oral Argument - Audio":
                case_name = text[index - 1]
        elif chunk.startswith("Date Argued:"):
            parsed_date = normalize_space(chunk.split(":", 1)[1])
            if parsed_date:
                date_argued = parsed_date

    return DetailRecord(
        term=record.term,
        detail_id=record.detail_id,
        docket=docket,
        case_name=case_name,
        date_argued=date_argued,
        date_argued_iso=date_to_iso(date_argued),
        detail_url=record.detail_url,
        audio_url=parser.audio_urls[0] if parser.audio_urls else None,
        transcript_url=parser.transcript_urls[0] if parser.transcript_urls else None,
        session=record.session,
    )


def record_directory(output: Path, record: DetailRecord) -> Path:
    return output / "sources" / safe_slug(record.docket, max_length=64)


def record_stem(record: DetailRecord) -> str:
    date_prefix = record.date_argued_iso or "unknown-date"
    docket = safe_slug(record.docket, max_length=32)
    case_name = safe_slug(record.case_name, max_length=72)
    return f"{date_prefix}_{docket}_{case_name}"


def download_file(
    url: str,
    destination: Path,
    timeout: int,
    retries: int,
    delay: float,
    overwrite: bool,
) -> dict[str, object]:
    if destination.exists() and destination.stat().st_size > 0 and not overwrite:
        sha256, size = checksum_file(destination)
        return {"path": str(destination), "bytes": size, "sha256": sha256, "downloaded": False}

    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = destination.with_suffix(destination.suffix + ".part")
    if tmp_path.exists():
        tmp_path.unlink()

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = Request(url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=timeout) as response, tmp_path.open("wb") as out:
                shutil.copyfileobj(response, out, length=1024 * 1024)
            tmp_path.replace(destination)
            sha256, size = checksum_file(destination)
            return {"path": str(destination), "bytes": size, "sha256": sha256, "downloaded": True}
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if tmp_path.exists():
                tmp_path.unlink()
            if attempt == retries:
                break
            time.sleep(delay * attempt)

    raise RuntimeError(f"failed to download {url}: {last_error}") from last_error


def checksum_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            total += len(chunk)
    return digest.hexdigest(), total


def write_json(path: Path, payload: object) -> None:
    atomic_write_json(path, payload)


def select_terms(args: argparse.Namespace) -> list[int]:
    if args.term:
        terms = sorted(set(args.term), reverse=True)
    else:
        terms = discover_terms(timeout=args.timeout, retries=args.retries, delay=args.retry_delay)

    if args.from_term is not None:
        terms = [term for term in terms if term >= args.from_term]
    if args.to_term is not None:
        terms = [term for term in terms if term <= args.to_term]
    return terms


def filter_index_records(records: list[IndexRecord], args: argparse.Namespace) -> list[IndexRecord]:
    filtered = records
    if args.docket:
        wanted = {normalize_lookup(docket) for docket in args.docket}
        filtered = [record for record in filtered if normalize_lookup(record.docket) in wanted]
    if args.query:
        queries = [normalize_lookup(query) for query in args.query]
        filtered = [
            record
            for record in filtered
            if all(
                query
                in normalize_lookup(
                    " ".join(
                        [
                            record.docket,
                            record.detail_id,
                            record.case_name,
                            record.date_argued,
                            record.session or "",
                        ]
                    )
                )
                for query in queries
            )
        ]
    return filtered


def normalize_lookup(value: str) -> str:
    return re.sub(r"\s+", " ", value.casefold()).strip()


def archive_records(args: ArchiveProcessorInput) -> int:
    output = args.output.resolve()
    terms = select_terms(args)
    if not terms:
        print("No term years selected.", file=sys.stderr)
        return 1

    if args.list_terms:
        for term in terms:
            print(term)
        return 0

    print(f"Selected terms: {', '.join(str(term) for term in terms)}")
    print(f"Output: {output}")
    manifest_records: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    seen = 0

    for term in terms:
        print(f"\nFetching term index {term}...")
        try:
            index_records = fetch_index_records(
                term, timeout=args.timeout, retries=args.retries, delay=args.retry_delay
            )
        except RuntimeError as exc:
            failures.append({"term": str(term), "url": f"{ARGUMENT_AUDIO_URL}/{term}", "error": str(exc)})
            print(f"  ERROR: {exc}", file=sys.stderr)
            continue

        unfiltered_count = len(index_records)
        index_records = filter_index_records(index_records, args)
        if len(index_records) == unfiltered_count:
            print(f"  Found {len(index_records)} records")
        else:
            print(f"  Found {unfiltered_count} records; {len(index_records)} matched filters")
        for index_record in index_records:
            if args.limit is not None and seen >= args.limit:
                break
            seen += 1
            time.sleep(args.rate_limit)
            try:
                detail = fetch_detail(
                    index_record,
                    timeout=args.timeout,
                    retries=args.retries,
                    delay=args.retry_delay,
                )
            except RuntimeError as exc:
                failures.append({"term": str(term), "url": index_record.detail_url, "error": str(exc)})
                print(f"  ERROR: {index_record.detail_url}: {exc}", file=sys.stderr)
                continue

            print(f"  {detail.date_argued or 'unknown date'} {detail.docket} {detail.case_name}")
            record_dir = record_directory(output, detail)
            record_payload: dict[str, object] = {
                **asdict(detail),
                "archived_at": datetime.now(UTC).isoformat(),
                "source": "supremecourt.gov",
            }

            if detail.audio_url:
                audio_path = record_dir / "audio.mp3"
                if args.dry_run:
                    record_payload["audio_file"] = {"path": str(audio_path), "downloaded": False}
                else:
                    record_payload["audio_file"] = download_file(
                        detail.audio_url,
                        audio_path,
                        timeout=args.timeout,
                        retries=args.retries,
                        delay=args.retry_delay,
                        overwrite=args.overwrite,
                    )
            else:
                record_payload["audio_file"] = None
                failures.append({"term": str(term), "url": detail.detail_url, "error": "missing MP3 URL"})

            if args.include_transcripts and detail.transcript_url:
                transcript_path = record_dir / "transcript.pdf"
                if args.dry_run:
                    record_payload["transcript_file"] = {
                        "path": str(transcript_path),
                        "downloaded": False,
                    }
                else:
                    record_payload["transcript_file"] = download_file(
                        detail.transcript_url,
                        transcript_path,
                        timeout=args.timeout,
                        retries=args.retries,
                        delay=args.retry_delay,
                        overwrite=args.overwrite,
                    )

            if not args.dry_run:
                write_json(record_dir / "metadata.json", record_payload)
            manifest_records.append(record_payload)

        if args.limit is not None and seen >= args.limit:
            break

    summary = {
        "created_at": datetime.now(UTC).isoformat(),
        "source_index": ARGUMENT_AUDIO_URL,
        "terms": terms,
        "record_count": len(manifest_records),
        "failure_count": len(failures),
        "failures": failures,
        "records": manifest_records,
    }

    if args.dry_run:
        print("\nDry run complete; no files were written.")
    else:
        output.mkdir(parents=True, exist_ok=True)
        index_dir = output / "sources" / "_index"
        index_dir.mkdir(parents=True, exist_ok=True)
        manifest_json = index_dir / "manifest.latest.json"
        manifest_jsonl = index_dir / "manifest.jsonl"
        write_json(manifest_json, summary)
        with manifest_jsonl.open("w", encoding="utf-8") as handle:
            for record in manifest_records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        print(f"\nWrote {manifest_json}")
        print(f"Wrote {manifest_jsonl}")

    if failures:
        print(f"Completed with {len(failures)} failures.", file=sys.stderr)
        return 2
    print(f"Archived metadata for {len(manifest_records)} records.")
    return 0


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be non-negative")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed
