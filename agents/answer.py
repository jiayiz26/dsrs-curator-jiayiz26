"""Read-only question answering over the Chapter 3 Parquet tables.

The data access and common question patterns are deterministic and do not execute
model-generated code.  This makes answers repeatable and keeps issuer text (which is
filed by third parties) out of an instruction channel.  Unsupported or out-of-scope
questions return a truthful null rather than guessing.
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from agents.planner import plan
from agents.llm import LLMError

OUTPUT = Path(__file__).resolve().parents[1] / "output"
FILINGS = OUTPUT / "filings.parquet"
HOLDINGS = OUTPUT / "holdings.parquet"
VALID_UNITS = {"USD", "SHARES", "COUNT", "PERCENT", "NAME", "DATE", "NONE"}


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _quarter(question: str) -> str | None:
    q = question.lower()
    match = re.search(r"(20\d{2})\s*q\s*([1-4])", q)
    if match:
        return f"{match.group(1)}Q{match.group(2)}"
    match = re.search(r"q\s*([1-4])\s*20(\d{2})", q)
    if match:
        return f"20{match.group(2)}Q{match.group(1)}"
    match = re.search(r"(first|second|third|fourth)\s+quarter\s+(20\d{2})", q)
    if match:
        n = {"first": 1, "second": 2, "third": 3, "fourth": 4}[match.group(1)]
        return f"{match.group(2)}Q{n}"
    return None


def _issuer(question: str) -> str | None:
    q = _norm(question)
    aliases = {
        "nvidia": "nvidia",
        "apple": "apple",
        "microsoft": "microsoft",
        "tesla": "tesla",
        "amazon": "amazon",
        "alphabet": "alphabet",
    }
    return next((needle for needle in aliases if needle in q), None)


def _is_out_of_scope(question: str, quarters: set[str]) -> bool:
    explicit = _quarter(question)
    return explicit is not None and explicit not in quarters


def _null(reason: str, *, log: bool = True) -> dict[str, Any]:
    if log:
        print(f"answer unavailable: {reason}", file=sys.stderr)
    return {"answer": None, "unit": "NONE", "sources": []}


def _sources(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({row["accession_number"] for row in rows})


@lru_cache(maxsize=1)
def _data() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load the read-only tables once per process."""
    if not FILINGS.exists() or not HOLDINGS.exists():
        raise FileNotFoundError("Chapter 3 Parquet files are missing")
    filings = pq.read_table(FILINGS).to_pylist()
    holdings = pq.read_table(HOLDINGS).to_pylist()
    by_accession = {row["accession_number"]: row for row in filings}
    for row in holdings:
        filing = by_accession.get(row["accession_number"])
        if filing is not None:
            row["fund_name"] = filing["fund_name"]
            row["filing_date"] = filing["filing_date"]
            row["report_type"] = filing["report_type"]
    return filings, holdings


def _fund(question: str, filings: list[dict[str, Any]]) -> str | None:
    """Resolve a roster fund only when one name is clearly present in the question."""
    q = _norm(question)
    candidates = []
    for name in sorted({row["fund_name"] for row in filings}):
        tokens = [token for token in _norm(name).split() if len(token) > 2]
        score = sum(token in q for token in tokens) / max(1, len(tokens))
        if score >= 0.60:
            candidates.append((score, name))
    candidates.sort(reverse=True)
    if candidates and (len(candidates) == 1 or candidates[0][0] > candidates[1][0]):
        return candidates[0][1]
    return None


def _issuer_rows(holdings: list[dict[str, Any]], issuer: str, quarter: str) -> list[dict[str, Any]]:
    return [
        row for row in holdings
        if row.get("report_quarter") == quarter and issuer in _norm(row.get("name_of_issuer") or "")
    ]


def _planned_issuer_rows(holdings: list[dict[str, Any]], issuer_query: str | None, quarter: str) -> list[dict[str, Any]]:
    needle = _norm(issuer_query or "")
    if not needle:
        return []
    return [row for row in holdings if row.get("report_quarter") == quarter and needle in _norm(row.get("name_of_issuer") or "")]


def _largest_manager_position(question: str, holdings: list[dict[str, Any]], filings: list[dict[str, Any]], quarter: str, issuer: str) -> dict[str, Any]:
    rows = _issuer_rows(holdings, issuer, quarter)
    if not rows:
        return _null(f"no {issuer} holdings found in {quarter}")
    totals: dict[str, int] = defaultdict(int)
    supporting: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        totals[row["fund_name"]] += row["value"] or 0
        supporting[row["fund_name"]].append(row)
    winner = max(totals, key=lambda name: (totals[name], name))
    return {"answer": winner, "unit": "NAME", "sources": _sources(supporting[winner])}


def _largest_position(question: str, holdings: list[dict[str, Any]], quarter: str, fund: str, issuer: str | None = None) -> dict[str, Any]:
    rows = [row for row in holdings if row.get("report_quarter") == quarter and row.get("fund_name") == fund]
    if issuer:
        rows = [row for row in rows if issuer in _norm(row.get("name_of_issuer") or "")]
    if not rows:
        return _null(f"no holdings found for {fund} in {quarter}")
    winner = max(rows, key=lambda row: (row["value"] or 0, row["name_of_issuer"]))
    return {
        "answer": f"{winner['name_of_issuer']} ({winner['value']} USD)",
        "unit": "USD",
        "sources": _sources([winner]),
    }


def _execute_plan(intent: dict[str, Any], filings: list[dict[str, Any]], holdings: list[dict[str, Any]]) -> dict[str, Any]:
    """Execute only allow-listed planner intents against local data."""
    op = intent["operation"]
    quarter = intent["quarter"]
    issuer_query = intent["issuer_query"]
    fund = intent["fund_name"]
    if op == "unsupported":
        return _null("the planner marked the question unsupported")
    if op == "notice_managers":
        rows = [row for row in filings if row["report_quarter"] == (quarter or "2026Q2") and row["form_type"].startswith("13F-NT")]
        return {"answer": sorted({row["fund_name"] for row in rows}), "unit": "NAME", "sources": _sources(rows)} if rows else _null("no notice filing found")
    if not quarter:
        return _null("the planner did not identify a quarter")
    rows = _planned_issuer_rows(holdings, issuer_query, quarter)
    if op == "largest_manager_value":
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows: groups[row["fund_name"]].append(row)
        if not groups: return _null("no matching issuer holdings found")
        winner = max(groups, key=lambda name: (sum(row["value"] or 0 for row in groups[name]), name))
        return {"answer": winner, "unit": "NAME", "sources": _sources(groups[winner])}
    if op == "distinct_issuers" and fund:
        selected = [row for row in holdings if row["fund_name"] == fund and row["report_quarter"] == quarter]
        return {"answer": len({row["name_of_issuer"] for row in selected}), "unit": "COUNT", "sources": _sources(selected)} if selected else _null("no holdings found")
    if op == "reported_total" and fund:
        selected = [row for row in filings if row["fund_name"] == fund and row["report_quarter"] == quarter]
        return {"answer": selected[0]["table_value_total"], "unit": "USD", "sources": _sources(selected)} if selected and selected[0]["table_value_total"] is not None else _null("no reported total found")
    if op == "direct_holding" and fund:
        selected_filings = [row for row in filings if row["fund_name"] == fund and row["report_quarter"] == quarter]
        selected_holdings = [row for row in rows if row["fund_name"] == fund]
        if selected_holdings: return {"answer": "Yes", "unit": "NONE", "sources": _sources(selected_holdings)}
        return {"answer": "No", "unit": "NONE", "sources": _sources(selected_filings)} if selected_filings else _null("no filing found")
    if op == "most_call_options":
        selected = [row for row in holdings if row["report_quarter"] == quarter and (row.get("put_call") or "").lower() == "call"]
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in selected: groups[row["fund_name"]].append(row)
        if not groups: return _null("no call options found")
        winner = max(groups, key=lambda name: (len(groups[name]), name))
        return {"answer": winner, "unit": "NAME", "sources": _sources(groups[winner])}
    if op == "largest_position" and fund:
        selected = [row for row in holdings if row["fund_name"] == fund and row["report_quarter"] == quarter]
        if issuer_query: selected = [row for row in selected if _norm(issuer_query) in _norm(row["name_of_issuer"])]
        if not selected: return _null("no matching position found")
        winner = max(selected, key=lambda row: (row["value"] or 0, row["name_of_issuer"]))
        return {"answer": f"{winner['name_of_issuer']} ({winner['value']} USD)", "unit": "USD", "sources": _sources([winner])}
    if op in {"change_shares", "both_quarters"} and issuer_query:
        q1 = _planned_issuer_rows(holdings, issuer_query, "2026Q1")
        q2 = _planned_issuer_rows(holdings, issuer_query, "2026Q2")
        groups: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"2026Q1": [], "2026Q2": []})
        for row in q1: groups[row["fund_name"]]["2026Q1"].append(row)
        for row in q2: groups[row["fund_name"]]["2026Q2"].append(row)
        candidates = [(name, p) for name, p in groups.items() if p["2026Q1"] and p["2026Q2"]]
        if not candidates: return _null("no manager reported the issuer in both quarters")
        if op == "change_shares":
            changes = [(sum(r["ssh_prnamt"] or 0 for r in p["2026Q2"]) - sum(r["ssh_prnamt"] or 0 for r in p["2026Q1"]), name, p) for name, p in candidates]
            change, name, periods = max(changes, key=lambda x: (x[0], x[1]))
            return {"answer": name, "unit": "NAME", "sources": _sources(periods["2026Q1"] + periods["2026Q2"])}
        name, periods = max(candidates, key=lambda x: x[0])
        old = sum(r["ssh_prnamt"] or 0 for r in periods["2026Q1"]); new = sum(r["ssh_prnamt"] or 0 for r in periods["2026Q2"])
        direction = "grew" if new > old else "shrank" if new < old else "was unchanged"
        return {"answer": [name, direction], "unit": "NONE", "sources": _sources(periods["2026Q1"] + periods["2026Q2"])}
    return _null("the planner intent could not be executed")


def _answer(question: str) -> dict[str, Any]:
    filings, holdings = _data()
    quarters = {row["report_quarter"] for row in filings}
    if _is_out_of_scope(question, quarters):
        return _null("the requested reporting quarter is outside the dataset")
    q = question.lower()
    quarter = _quarter(question)
    issuer = _issuer(question)

    if "13f-nt" in q or "13f nt" in q or "notice" in q:
        target = quarter or "2026Q2"
        rows = [row for row in filings if row["report_quarter"] == target and row["form_type"].startswith("13F-NT")]
        if not rows:
            return _null(f"no 13F notice found in {target}")
        return {"answer": sorted({row["fund_name"] for row in rows}), "unit": "NAME", "sources": _sources(rows)}

    if "distinct issuer" in q or "unique issuer" in q:
        fund = _fund(question, filings)
        if not fund or not quarter:
            return _null("could not identify one manager and one quarter")
        rows = [row for row in holdings if row["fund_name"] == fund and row["report_quarter"] == quarter]
        if not rows:
            return _null(f"no holdings found for {fund} in {quarter}")
        return {"answer": len({row["name_of_issuer"] for row in rows}), "unit": "COUNT", "sources": _sources(rows)}

    if "total reported value" in q or "portfolio value" in q:
        fund = _fund(question, filings)
        if not fund or not quarter:
            return _null("could not identify one manager and one quarter")
        rows = [row for row in filings if row["fund_name"] == fund and row["report_quarter"] == quarter]
        if not rows or rows[0]["table_value_total"] is None:
            return _null("the requested reported total is not available")
        return {"answer": rows[0]["table_value_total"], "unit": "USD", "sources": _sources(rows)}

    if "directly" in q and issuer:
        fund = _fund(question, filings)
        if not fund or not quarter:
            return _null("could not identify one manager, issuer and quarter")
        filing_rows = [row for row in filings if row["fund_name"] == fund and row["report_quarter"] == quarter]
        position_rows = [row for row in _issuer_rows(holdings, issuer, quarter) if row["fund_name"] == fund]
        if position_rows:
            return {"answer": "Yes", "unit": "NONE", "sources": _sources(position_rows)}
        if filing_rows:
            return {"answer": "No", "unit": "NONE", "sources": _sources(filing_rows)}
        return _null(f"no filing found for {fund} in {quarter}")

    if "average" in q and "portfolio" in q:
        if not quarter:
            return _null("could not identify the requested quarter")
        rows = [row for row in filings if row["report_quarter"] == quarter and row["table_value_total"] is not None]
        if not rows:
            return _null("no reported portfolio totals are available")
        return {"answer": sum(row["table_value_total"] for row in rows) / len(rows), "unit": "USD", "sources": _sources(rows)}

    if ("added" in q or "between" in q or "grow" in q or "shrink" in q) and "both" not in q:
        if issuer is None:
            return _null("the question does not identify an issuer")
        q1, q2 = "2026Q1", "2026Q2"
        rows1, rows2 = _issuer_rows(holdings, issuer, q1), _issuer_rows(holdings, issuer, q2)
        if not rows1 or not rows2:
            return _null(f"insufficient {issuer} holdings in both quarters")
        by_fund: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {q1: [], q2: []})
        for row in rows1: by_fund[row["fund_name"]][q1].append(row)
        for row in rows2: by_fund[row["fund_name"]][q2].append(row)
        changes = []
        for fund, periods in by_fund.items():
            if periods[q1] and periods[q2]:
                old = sum(row["ssh_prnamt"] or 0 for row in periods[q1])
                new = sum(row["ssh_prnamt"] or 0 for row in periods[q2])
                changes.append((new - old, fund, periods[q1] + periods[q2]))
        if not changes:
            return _null(f"no manager reported {issuer} in both quarters")
        change, fund, rows = max(changes, key=lambda item: (item[0], item[1]))
        if "which manager" in q or "who" in q:
            return {"answer": fund, "unit": "NAME", "sources": _sources(rows)}
        return {"answer": change, "unit": "SHARES", "sources": _sources(rows)}

    if "both" in q and issuer:
        by_fund: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"2026Q1": [], "2026Q2": []})
        for row in _issuer_rows(holdings, issuer, "2026Q1"):
            by_fund[row["fund_name"]]["2026Q1"].append(row)
        for row in _issuer_rows(holdings, issuer, "2026Q2"):
            by_fund[row["fund_name"]]["2026Q2"].append(row)
        candidates = [(fund, periods) for fund, periods in by_fund.items() if periods["2026Q1"] and periods["2026Q2"]]
        if not candidates:
            return _null(f"no manager reported {issuer} in both quarters")
        fund, periods = max(candidates, key=lambda item: item[0])
        old = sum(row["ssh_prnamt"] or 0 for row in periods["2026Q1"])
        new = sum(row["ssh_prnamt"] or 0 for row in periods["2026Q2"])
        direction = "grew" if new > old else "shrank" if new < old else "was unchanged"
        return {"answer": [fund, direction], "unit": "NONE", "sources": _sources(periods["2026Q1"] + periods["2026Q2"])}

    if "call option" in q or "call options" in q:
        target = quarter or "2026Q2"
        rows = [row for row in holdings if row["report_quarter"] == target and (row.get("put_call") or "").lower() == "call"]
        if not rows:
            return _null(f"no call options found in {target}")
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows: groups[row["fund_name"]].append(row)
        winner = max(groups, key=lambda name: (len(groups[name]), name))
        return {"answer": winner, "unit": "NAME", "sources": _sources(groups[winner])}

    fund = _fund(question, filings)
    if "largest" in q and "position" in q and fund and quarter:
        return _largest_position(question, holdings, quarter, fund, issuer)

    if "held" in q and issuer and quarter:
        return _largest_manager_position(question, holdings, filings, quarter, issuer)

    return _null("question pattern is not supported by the deterministic query layer", log=False)


def main(question: str) -> dict[str, Any]:
    """Answer a question against the read-only Parquet dataset."""
    try:
        result = _answer(question)
        if result.get("answer") is None and not _is_out_of_scope(question, {row["report_quarter"] for row in _data()[0]}):
            print("deterministic miss; using the validated DeepSeek planner", file=sys.stderr)
            filings, holdings = _data()
            managers = sorted({row["fund_name"] for row in filings})
            quarters = sorted({row["report_quarter"] for row in filings})
            result = _execute_plan(plan(question, managers, quarters), filings, holdings)
    except LLMError as exc:
        print(f"planner unavailable: {exc}", file=sys.stderr)
        return {"answer": None, "unit": "NONE", "sources": []}
    except Exception as exc:
        print(f"agent failure: {exc}", file=sys.stderr)
        return {"answer": None, "unit": "NONE", "sources": []}
    if result.get("unit") not in VALID_UNITS or not isinstance(result.get("sources"), list):
        return _null("internal result validation failed")
    return result


def _cli() -> int:
    if len(sys.argv) < 2:
        print('usage: python -m agents.answer "your question"', file=sys.stderr)
        return 2
    result = main(sys.argv[1])
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
