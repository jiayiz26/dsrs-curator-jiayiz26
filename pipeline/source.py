"""SEC discovery and download stage for the curator pipeline.

All SEC access goes through :class:`SecClient`: it adds the required contact
header, spaces requests below the SEC limit, and persists responses in ``.cache``.
The functions here deliberately stop at raw XML; parsing belongs in a later stage.
"""

from __future__ import annotations

import csv
import json
import re
import time
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import PurePosixPath
from pathlib import Path
from typing import Any, Iterable

import httpx


LOOKUP_URL = "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/{name}"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/index.json"
FORM_TYPES = {"13F-HR", "13F-HR/A", "13F-NT", "13F-NT/A"}
REPORT_PERIODS = {"2026-03-31", "2026-06-30"}
FILING_DATE_CUTOFF = "2026-08-18"


class SourceError(RuntimeError):
    """A source response cannot safely be used to build the dataset."""


def normalize_name(name: str) -> str:
    """Make conservative comparisons of SEC entity names.

    This intentionally does not remove meaningful words such as ``management`` or
    ``capital``. Legal suffixes and punctuation are common formatting differences;
    removing broader terms makes a wrong CIK look plausible.
    """
    cleaned = name.upper().replace("&", " AND ")
    cleaned = re.sub(r"\([^)]*\)", " ", cleaned)
    tokens = re.findall(r"[A-Z0-9]+", cleaned)
    suffixes = {"LLC", "LLP", "LP", "LTD", "LIMITED", "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "PLC", "ADV", "MA"}
    while tokens and tokens[-1] in suffixes:
        tokens.pop()
    if len(tokens) >= 2 and tokens[-2:] == ["ET", "AL"]:
        tokens = tokens[:-2]
        while tokens and tokens[-1] in suffixes:
            tokens.pop()
    if tokens and tokens[0] == "THE":
        tokens.pop(0)
    return " ".join(tokens)


def parse_cik_lookup(text: str) -> dict[str, set[str]]:
    """Parse SEC's ``name:CIK:`` lookup file into normalized-name candidates."""
    result: dict[str, set[str]] = {}
    for line in text.splitlines():
        try:
            name, cik, _ = line.rsplit(":", 2)
        except ValueError:
            continue
        if not cik.isdigit():
            continue
        result.setdefault(normalize_name(name), set()).add(str(int(cik)))
    return result


def resolve_cik(fund_name: str, supplied_cik: str, lookup: dict[str, set[str]]) -> tuple[str, str]:
    """Resolve one roster entry, refusing ambiguous or weak fuzzy matches."""
    target = normalize_name(fund_name)
    exact = lookup.get(target, set())
    supplied = str(int(supplied_cik))
    if supplied in exact:
        # SEC can list multiple legal entities under the same normalized name.
        # When the roster CIK is one of those exact candidates, it disambiguates
        # the name without a fuzzy guess.
        return supplied, "given"
    if len(exact) == 1:
        return next(iter(exact)), "corrected"
    if len(exact) > 1:
        raise SourceError(f"ambiguous SEC lookup for {fund_name!r}: {sorted(exact)}")

    scored = sorted(
        ((SequenceMatcher(None, target, candidate).ratio(), candidate, ciks)
         for candidate, ciks in lookup.items()),
        reverse=True,
    )
    if not scored:
        raise SourceError("SEC CIK lookup was empty")
    score, candidate, ciks = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if score < 0.93 or score - runner_up < 0.04 or len(ciks) != 1:
        examples = ", ".join(f"{name} ({value:.2f})" for value, name, _ in scored[:3])
        raise SourceError(
            f"cannot safely resolve {fund_name!r}; best SEC name is {candidate!r} "
            f"({score:.2f}). Review manually. Candidates: {examples}"
        )
    cik = next(iter(ciks))
    return cik, "given" if int(supplied_cik) == int(cik) else "corrected"


@dataclass(frozen=True)
class Filing:
    cik: str
    fund_name: str
    accession_number: str
    form_type: str
    report_date: str
    filing_date: str
    primary_document: str


def in_scope_filings(cik: str, fund_name: str, payload: dict[str, Any]) -> list[Filing]:
    """Select the only 13F filings the challenge asks for, deterministically."""
    recent = payload.get("filings", {}).get("recent", {})
    keys = ("accessionNumber", "form", "reportDate", "filingDate", "primaryDocument")
    if any(key not in recent for key in keys):
        raise SourceError(f"submissions response for CIK {cik} lacks required fields")
    rows: list[Filing] = []
    for accession, form, report_date, filing_date, primary_document in zip(
        recent["accessionNumber"], recent["form"], recent["reportDate"],
        recent["filingDate"], recent["primaryDocument"], strict=True,
    ):
        if form in FORM_TYPES and report_date in REPORT_PERIODS and filing_date <= FILING_DATE_CUTOFF:
            rows.append(Filing(cik, fund_name, accession, form, report_date, filing_date, primary_document))
    return sorted(rows, key=lambda row: (row.cik, row.report_date, row.filing_date, row.accession_number))


class SecClient:
    """A small cached SEC client with a process-wide request interval."""

    def __init__(self, user_agent: str, cache_dir: Path, min_interval: float = 0.12) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = min_interval
        self._last_request = 0.0
        self.cache_hits = 0
        self.network_requests = 0
        self._client = httpx.Client(
            headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
            follow_redirects=True,
            timeout=30.0,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "SecClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get_bytes(self, url: str, cache_path: Path) -> bytes:
        if cache_path.exists():
            self.cache_hits += 1
            return cache_path.read_bytes()
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        response = self._client.get(url)
        self._last_request = time.monotonic()
        self.network_requests += 1
        response.raise_for_status()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(response.content)
        return response.content

    def get_json(self, url: str, cache_path: Path) -> dict[str, Any]:
        return json.loads(self.get_bytes(url, cache_path))


def choose_xml_name(index: dict[str, Any], primary_document: str) -> str:
    """Choose the cover XML document without guessing filenames from accession."""
    items = index.get("directory", {}).get("item", [])
    names = [item.get("name", "") for item in items if isinstance(item, dict)]
    primary_basename = PurePosixPath(primary_document).name
    if primary_document.lower().endswith(".xml") and primary_basename in names:
        return primary_basename
    xml_names = [name for name in names if name.lower().endswith(".xml")]
    preferred = [name for name in xml_names if "infotable" not in name.lower() and "schema" not in name.lower()]
    if preferred:
        return sorted(preferred)[0]
    if xml_names:
        return sorted(xml_names)[0]
    raise SourceError(f"no XML document in filing index (primary document: {primary_document!r})")


def choose_xml_documents(index: dict[str, Any], primary_document: str) -> tuple[str, list[str]]:
    """Return ``(cover_document, information_table_documents)`` from an index.

    A 13F filing commonly has a cover XML and a separate information-table XML.
    Keep both roles explicit; choosing one arbitrary XML silently loses either
    cover metadata or holdings.
    """
    items = index.get("directory", {}).get("item", [])
    names = [item.get("name", "") for item in items if isinstance(item, dict)]
    xml_names = [name for name in names if name.lower().endswith(".xml")]
    cover = choose_xml_name(index, primary_document)
    info = [
        name for name in xml_names
        if name != cover and "schema" not in name.lower()
    ]
    return cover, sorted(info)


def write_filers(path: Path, rows: Iterable[tuple[str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["fund_name", "cik", "cik_source"])
        writer.writeheader()
        for fund_name, cik, source in sorted(rows, key=lambda row: int(row[1])):
            writer.writerow({"fund_name": fund_name, "cik": cik, "cik_source": source})


def build_source(roster: list[dict[str, str]], output_dir: Path, cache_dir: Path, user_agent: str) -> list[Filing]:
    """Verify the roster, download in-scope XML, and write Chapter 1 artifacts."""
    with SecClient(user_agent, cache_dir) as sec:
        lookup_text = sec.get_bytes(LOOKUP_URL, cache_dir / "cik-lookup-data.txt").decode("latin-1")
        lookup = parse_cik_lookup(lookup_text)
        resolved: list[tuple[str, str, str]] = []
        for row in roster:
            try:
                cik, source = resolve_cik(row["fund_name"], row["cik"], lookup)
            except SourceError as exc:
                # A normalized name can legitimately map to several SEC entities.
                # Use the in-scope filing count as an evidence-based tie breaker;
                # never silently choose based on dictionary order.
                candidates = lookup.get(normalize_name(row["fund_name"]), set())
                if "ambiguous SEC lookup" not in str(exc) or not candidates:
                    raise
                scored: list[tuple[int, str]] = []
                for candidate in sorted(candidates):
                    candidate_payload = sec.get_json(
                        SUBMISSIONS_URL.format(cik=int(candidate)),
                        cache_dir / "submissions" / f"{int(candidate):010d}.json",
                    )
                    scored.append((len(in_scope_filings(candidate.zfill(10), row["fund_name"], candidate_payload)), candidate))
                scored.sort(reverse=True)
                if not scored or scored[0][0] == 0 or (len(scored) > 1 and scored[0][0] == scored[1][0]):
                    raise SourceError(f"could not disambiguate {row['fund_name']!r}: {scored}") from exc
                cik, source = scored[0][1], "corrected"
            resolved.append((row["fund_name"], cik, source))
        write_filers(output_dir / "filers.csv", resolved)

        filings: list[Filing] = []
        for fund_name, cik, _ in resolved:
            payload = sec.get_json(SUBMISSIONS_URL.format(cik=int(cik)), cache_dir / "submissions" / f"{int(cik):010d}.json")
            filings.extend(in_scope_filings(cik.zfill(10), fund_name, payload))
        if len(filings) != 40:
            raise SourceError(f"expected 40 in-scope filings, found {len(filings)}; stop before parsing")

        manifest: list[dict[str, Any]] = []
        for filing in filings:
            bare_accession = filing.accession_number.replace("-", "")
            numeric_cik = str(int(filing.cik))
            index = sec.get_json(
                INDEX_URL.format(cik=numeric_cik, accession=bare_accession),
                cache_dir / "indexes" / numeric_cik / f"{bare_accession}.json",
            )
            cover_name, info_names = choose_xml_documents(index, filing.primary_document)
            target_dir = output_dir / "filings" / numeric_cik
            target_dir.mkdir(parents=True, exist_ok=True)

            cover = sec.get_bytes(
                ARCHIVE_URL.format(cik=numeric_cik, accession=bare_accession, name=cover_name),
                cache_dir / "xml" / numeric_cik / f"{bare_accession}-{cover_name}",
            )
            cover_target = target_dir / f"{bare_accession}.xml"
            cover_target.write_bytes(cover)

            info_paths: list[str] = []
            for info_name in info_names:
                info = sec.get_bytes(
                    ARCHIVE_URL.format(cik=numeric_cik, accession=bare_accession, name=info_name),
                    cache_dir / "xml" / numeric_cik / f"{bare_accession}-{info_name}",
                )
                info_target = target_dir / f"{bare_accession}.info.xml"
                info_target.write_bytes(info)
                info_paths.append(str(info_target.relative_to(output_dir)))
                # A filing normally has one information table. If an unusual
                # index lists several, the last one would overwrite this path;
                # surface that rather than silently claiming all were retained.
                if len(info_names) > 1:
                    raise SourceError(f"filing {filing.accession_number} has multiple information tables: {info_names}")

            manifest.append({
                **asdict(filing),
                "cover_document": cover_name,
                "information_table_documents": info_names,
                "cover_path": str(cover_target.relative_to(output_dir)),
                "information_table_paths": info_paths,
            })

        (output_dir / "source_manifest.json").write_text(json.dumps({
            "filings": manifest,
            "cache_hits": sec.cache_hits,
            "network_requests": sec.network_requests,
        }, indent=2, sort_keys=True) + "\n")
        return filings
