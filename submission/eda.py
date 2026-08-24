#!/usr/bin/env python3
"""Explore the raw 13F XML produced by Chapter 1.

Run from any directory with:

    python submission/eda.py

FINDINGS from the current 40-filing slice
------------------------------------------
1. A filing is represented by two different SEC XML documents.  The archive
   index lists a cover document and, for holdings reports, a separate
   ``informationTable`` document; therefore a missing ``infoTable`` element in a
   cover document is not evidence that the filing has no holdings.
2. One cover document is a real notice (``13F NOTICE``), accession
   ``0001172661-26-003777``.  It has no information table and must remain a row in
   ``filings.parquet`` with null table totals.
3. Namespace prefixes vary: cover documents use the
   ``thirteenffiler``/``common`` namespaces, while information tables use the
   ``informationtable`` namespace.  Parsers must match namespace URI and local
   name, not literal prefixes.
4. The observed information tables contain repeated CUSIPs in 4 documents,
   including ``88579Y101`` in accession ``0001193125-26-226359``.  These are
   separate position rows, not duplicates to drop.  ``putCall`` is absent from
   ordinary rows, while the XML element named ``None`` is present in voting
   authority data.

The numbers above are printed from the files as evidence and will update if the
input slice changes.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from lxml import etree


ROOT = Path(__file__).resolve().parents[1]
FILINGS_DIR = ROOT / "output" / "filings"
MANIFEST = ROOT / "output" / "source_manifest.json"


def elements(root: etree._Element) -> Iterable[etree._Element]:
    """Yield real XML elements, excluding comments and processing instructions."""
    return (element for element in root.iter() if isinstance(element.tag, str))


def local_name(element: etree._Element) -> str:
    return etree.QName(element).localname


def text_of(element: etree._Element | None) -> str | None:
    if element is None:
        return None
    value = " ".join("".join(element.itertext()).split())
    return value or None


def first_text(root: etree._Element, name: str) -> str | None:
    return next((text_of(element) for element in elements(root) if local_name(element) == name), None)


def accession_map() -> dict[str, str]:
    """Map both dashed and archive-style accessions to the dashed form."""
    if not MANIFEST.exists():
        return {}
    records = json.loads(MANIFEST.read_text()).get("filings", [])
    result: dict[str, str] = {}
    for record in records:
        accession = record["accession_number"]
        result[accession] = accession
        result[accession.replace("-", "")] = accession
    return result


@dataclass
class Artifact:
    path: Path
    accession: str
    root_name: str
    report_type: str | None
    info_tables: list[etree._Element]
    table_entry_total: int | None
    namespaces: dict[str | None, str]


def load_artifacts() -> list[Artifact]:
    accessions = accession_map()
    artifacts: list[Artifact] = []
    for path in sorted(FILINGS_DIR.glob("*/*.xml")):
        root = etree.parse(str(path)).getroot()
        xml_elements = list(elements(root))
        info_tables = [element for element in xml_elements if local_name(element) == "infoTable"]
        declared = first_text(root, "tableEntryTotal")
        archive_stem = path.stem.removesuffix(".info")
        artifacts.append(Artifact(
            path=path,
            accession=accessions.get(archive_stem, archive_stem),
            root_name=local_name(root),
            report_type=first_text(root, "reportType"),
            info_tables=info_tables,
            table_entry_total=int(declared) if declared and declared.isdigit() else None,
            namespaces=dict(root.nsmap),
        ))
    return artifacts


def field_present(info_table: etree._Element, field: str) -> bool:
    return any(local_name(element) == field for element in elements(info_table))


def duplicate_summary(artifact: Artifact) -> tuple[int, str | None, int]:
    cusips: list[str] = []
    for table in artifact.info_tables:
        value = next((text_of(element) for element in elements(table) if local_name(element) == "cusip"), None)
        if value is not None:
            cusips.append(value)
    counts = Counter(cusips)
    repeated = [(cusip, count) for cusip, count in counts.items() if count > 1]
    if not repeated:
        return 0, None, 0
    cusip, count = max(repeated, key=lambda item: (item[1], item[0]))
    return len(repeated), cusip, count


def main() -> int:
    artifacts = load_artifacts()
    if not artifacts:
        raise SystemExit(f"No XML files found under {FILINGS_DIR}")

    cover = [artifact for artifact in artifacts if artifact.root_name == "edgarSubmission"]
    info_documents = [artifact for artifact in artifacts if artifact.root_name == "informationTable"]
    notices = [artifact for artifact in cover if artifact.report_type == "13F NOTICE"]
    cover_without_table = [artifact for artifact in cover if not artifact.info_tables]
    report_types = Counter(artifact.report_type or "<information-table document>" for artifact in artifacts)
    namespace_pairs = Counter(
        (prefix or "<default>", uri)
        for artifact in artifacts
        for prefix, uri in artifact.namespaces.items()
    )

    print(f"Files examined: {len(artifacts)}")
    print(f"Root documents: {Counter(artifact.root_name for artifact in artifacts)}")
    print("Report types:")
    for report_type, count in sorted(report_types.items()):
        print(f"  {report_type}: {count}")

    print("\nFinding 1 — cover/table split")
    print(f"  {len(cover)} cover documents and {len(info_documents)} information-table documents")
    print(f"  {len(cover_without_table)} cover documents have no infoTable element")
    if cover_without_table:
        examples = ", ".join(artifact.accession for artifact in cover_without_table[:3])
        print(f"  Examples (not automatically notices): {examples}")
    paired_accessions = set(artifact.accession for artifact in info_documents)
    print(f"  {sum(artifact.accession in paired_accessions for artifact in cover)}/{len(cover)} covers have a separate holdings document")
    print("  Interpretation: this is the expected two-document layout; use the archive index/accession to associate the pair.")

    print("\nFinding 2 — notices")
    print(f"  {len(notices)} notice document(s): {', '.join(artifact.accession for artifact in notices) or 'none'}")
    print("  Interpretation: a notice is retained in filings.parquet even though it contributes no holdings rows.")

    print("\nFinding 3 — namespaces")
    for (prefix, uri), count in sorted(namespace_pairs.items()):
        print(f"  {prefix} -> {uri}: {count} root declaration(s)")
    print("  Interpretation: match namespace URI and local name; never depend on a literal prefix.")

    print("\nFinding 4 — optional fields and duplicate positions")
    docs_with_tables = [artifact for artifact in artifacts if artifact.info_tables]
    for field in ("figi", "putCall", "otherManager", "None"):
        count = sum(any(field_present(table, field) for table in artifact.info_tables) for artifact in docs_with_tables)
        print(f"  {field}: present in {count}/{len(docs_with_tables)} information-table documents")
    duplicate_docs = []
    for artifact in docs_with_tables:
        repeated_count, cusip, occurrences = duplicate_summary(artifact)
        if repeated_count:
            duplicate_docs.append((artifact, repeated_count, cusip, occurrences))
    print(f"  Repeated-CUSIP documents: {len(duplicate_docs)}/{len(docs_with_tables)}")
    if duplicate_docs:
        artifact, repeated_count, cusip, occurrences = duplicate_docs[0]
        print(f"  Example: {artifact.accession} repeats {cusip} {occurrences} times ({repeated_count} repeated CUSIP value(s) total)")
    print("  Interpretation: absent putCall means null; the XML element named None is voting_none; preserve every position row.")

    print("\nFinding 5 — declared cover totals")
    declared = [artifact for artifact in cover if artifact.table_entry_total is not None]
    by_accession: dict[str, list[Artifact]] = {}
    for artifact in artifacts:
        by_accession.setdefault(artifact.accession, []).append(artifact)
    comparisons: list[tuple[str, int, int]] = []
    for cover_artifact in declared:
        holdings_rows = sum(
            len(artifact.info_tables)
            for artifact in by_accession.get(cover_artifact.accession, [])
            if artifact.root_name == "informationTable"
        )
        if holdings_rows:
            comparisons.append((cover_artifact.accession, cover_artifact.table_entry_total, holdings_rows))
    mismatches = [comparison for comparison in comparisons if comparison[1] != comparison[2]]
    print(f"  {len(declared)} cover documents declare tableEntryTotal; {len(comparisons)} have a paired holdings document")
    print(f"  Paired count comparison: {len(mismatches)} mismatch(es) out of {len(comparisons)}")
    if declared:
        sample = declared[0]
        print(f"  Example declared total: {sample.accession} says {sample.table_entry_total} entries")
    if mismatches:
        print(f"  First mismatch: {mismatches[0]}")
    print("  Interpretation: compare declared totals only after joining the cover document to its separate information-table document; do not treat a cover-only parse as zero holdings.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
