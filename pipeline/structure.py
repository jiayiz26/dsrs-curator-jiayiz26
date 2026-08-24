"""Turn the paired SEC 13F XML documents into the Chapter 3 Parquet tables."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import pyarrow as pa
import pyarrow.parquet as pq
from lxml import etree


FILINGS_SCHEMA = pa.schema([
    ("accession_number", pa.string()),
    ("cik", pa.string()),
    ("fund_name", pa.string()),
    ("filing_manager", pa.string()),
    ("form_type", pa.string()),
    ("report_period", pa.date32()),
    ("report_quarter", pa.string()),
    ("filing_date", pa.date32()),
    ("is_amendment", pa.bool_()),
    ("amendment_no", pa.int32()),
    ("amendment_type", pa.string()),
    ("report_type", pa.string()),
    ("form_13f_file_number", pa.string()),
    ("crd_number", pa.string()),
    ("sec_file_number", pa.string()),
    ("other_included_managers_count", pa.int32()),
    ("table_entry_total", pa.int64()),
    ("table_value_total", pa.int64()),
])

HOLDINGS_SCHEMA = pa.schema([
    ("accession_number", pa.string()),
    ("cik", pa.string()),
    ("report_quarter", pa.string()),
    ("name_of_issuer", pa.string()),
    ("title_of_class", pa.string()),
    ("cusip", pa.string()),
    ("figi", pa.string()),
    ("value", pa.int64()),
    ("ssh_prnamt", pa.int64()),
    ("ssh_prnamt_type", pa.string()),
    ("put_call", pa.string()),
    ("investment_discretion", pa.string()),
    ("other_manager", pa.string()),
    ("voting_sole", pa.int64()),
    ("voting_shared", pa.int64()),
    ("voting_none", pa.int64()),
])


def _elements(root: etree._Element) -> Iterable[etree._Element]:
    return (element for element in root.iter() if isinstance(element.tag, str))


def _local(element: etree._Element) -> str:
    return etree.QName(element).localname


def _text(element: etree._Element | None) -> str | None:
    if element is None:
        return None
    value = " ".join("".join(element.itertext()).split())
    return value or None


def _first(root: etree._Element, name: str) -> str | None:
    return next((_text(element) for element in _elements(root) if _local(element) == name), None)


def _first_under(root: etree._Element, parent_name: str, child_name: str) -> str | None:
    for parent in _elements(root):
        if _local(parent) == parent_name:
            for child in _elements(parent):
                if _local(child) == child_name:
                    return _text(child)
    return None


def _integer(value: str | None, *, field: str, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        return int(value.replace(",", "").strip())
    except ValueError as exc:
        raise ValueError(f"invalid integer for {field}: {value!r}") from exc


def _date(value: str | None, *, field: str, formats: tuple[str, ...]) -> date:
    if not value:
        raise ValueError(f"missing required date field {field}")
    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"invalid date for {field}: {value!r}")


def _quarter(period: date) -> str:
    return f"{period.year}Q{(period.month - 1) // 3 + 1}"


def _read_filers(path: Path) -> dict[str, str]:
    with path.open(newline="") as handle:
        return {row["cik"].zfill(10): row["fund_name"] for row in csv.DictReader(handle)}


def _read_manifest(path: Path) -> list[dict[str, Any]]:
    records = json.loads(path.read_text()).get("filings", [])
    return sorted(records, key=lambda record: record["accession_number"])


def _parse_filing(record: dict[str, Any], fund_name: str, output: Path) -> dict[str, Any]:
    cover_path = output / record["cover_path"]
    root = etree.parse(str(cover_path)).getroot()
    report_period = _date(_first(root, "periodOfReport"), field="periodOfReport", formats=("%m-%d-%Y", "%Y-%m-%d"))
    form_type = record["form_type"]
    report_type = _first(root, "reportType")
    if not report_type:
        raise ValueError(f"{record['accession_number']}: missing reportType")
    amendment = form_type.endswith("/A")
    amendment_type = _first(root, "amendmentType")
    filing_date = _date(record["filing_date"], field="filingDate", formats=("%Y-%m-%d",))
    return {
        "accession_number": record["accession_number"],
        "cik": record["cik"].zfill(10),
        "fund_name": fund_name,
        "filing_manager": _first_under(root, "filingManager", "name") or "",
        "form_type": form_type,
        "report_period": report_period,
        "report_quarter": _quarter(report_period),
        "filing_date": filing_date,
        "is_amendment": amendment,
        "amendment_no": _integer(_first(root, "amendmentNo"), field="amendmentNo"),
        "amendment_type": amendment_type,
        "report_type": report_type,
        "form_13f_file_number": _first(root, "form13FFileNumber"),
        "crd_number": _first(root, "crdNumber"),
        "sec_file_number": _first(root, "secFileNumber"),
        "other_included_managers_count": _integer(_first(root, "otherIncludedManagersCount"), field="otherIncludedManagersCount"),
        "table_entry_total": _integer(_first(root, "tableEntryTotal"), field="tableEntryTotal"),
        "table_value_total": _integer(_first(root, "tableValueTotal"), field="tableValueTotal"),
    }


def _parse_holdings(record: dict[str, Any], filing: dict[str, Any], output: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for relative_path in record.get("information_table_paths", []):
        root = etree.parse(str(output / relative_path)).getroot()
        for info in (element for element in _elements(root) if _local(element) == "infoTable"):
            def value(name: str) -> str | None:
                return next((_text(element) for element in _elements(info) if _local(element) == name), None)

            issuer = value("nameOfIssuer")
            title = value("titleOfClass")
            cusip = value("cusip")
            discretion = value("investmentDiscretion")
            amount_type = value("sshPrnamtType")
            if not issuer or not title or not cusip or not discretion or not amount_type:
                raise ValueError(f"{record['accession_number']}: required holding field is missing")
            rows.append({
                "accession_number": filing["accession_number"],
                "cik": filing["cik"],
                "report_quarter": filing["report_quarter"],
                "name_of_issuer": issuer,
                "title_of_class": title,
                "cusip": cusip,
                "figi": value("figi"),
                "value": _integer(value("value"), field="value"),
                "ssh_prnamt": _integer(value("sshPrnamt"), field="sshPrnamt"),
                "ssh_prnamt_type": amount_type,
                "put_call": value("putCall"),
                "investment_discretion": discretion,
                "other_manager": value("otherManager"),
                "voting_sole": _integer(value("Sole"), field="Sole", default=0),
                "voting_shared": _integer(value("Shared"), field="Shared", default=0),
                "voting_none": _integer(value("None"), field="None", default=0),
            })
    return rows


def build_parquet(output: Path) -> tuple[int, int]:
    """Parse Chapter 1 artifacts and write deterministic Snappy Parquet tables."""
    manifest_path = output / "source_manifest.json"
    filers_path = output / "filers.csv"
    if not manifest_path.exists() or not filers_path.exists():
        raise FileNotFoundError("Chapter 1 artifacts are missing; run the source stage first")
    fund_names = _read_filers(filers_path)
    filings: list[dict[str, Any]] = []
    holdings: list[dict[str, Any]] = []
    for record in _read_manifest(manifest_path):
        cik = record["cik"].zfill(10)
        if cik not in fund_names:
            raise ValueError(f"{record['accession_number']}: CIK {cik} absent from output/filers.csv")
        filing = _parse_filing(record, fund_names[cik], output)
        filings.append(filing)
        holdings.extend(_parse_holdings(record, filing, output))

    pq.write_table(pa.Table.from_pylist(filings, schema=FILINGS_SCHEMA), output / "filings.parquet", compression="snappy")
    pq.write_table(pa.Table.from_pylist(holdings, schema=HOLDINGS_SCHEMA), output / "holdings.parquet", compression="snappy")
    return len(filings), len(holdings)
