"""Offline checks for Chapter 1 selection and SEC-document discovery."""

from pipeline.source import choose_xml_documents, choose_xml_name, in_scope_filings, normalize_name, resolve_cik
from agents.answer import main as answer_question


def test_name_normalization_handles_legal_suffixes_and_parentheticals():
    assert normalize_name("D. E. Shaw & Co. Inc.") == "D E SHAW AND"
    assert normalize_name("DME Capital Management LP (Greenlight)") == "DME CAPITAL MANAGEMENT"


def test_cik_resolution_marks_a_changed_cik():
    lookup = {"THIRD POINT": {"1040273"}}
    assert resolve_cik("Third Point LLC", "0000000001", lookup) == ("1040273", "corrected")


def test_discovery_filters_report_date_form_and_cutoff():
    payload = {"filings": {"recent": {
        "accessionNumber": ["a", "b", "c", "d"],
        "form": ["13F-HR", "13F-HR", "10-K", "13F-NT"],
        "reportDate": ["2026-03-31", "2025-12-31", "2026-06-30", "2026-06-30"],
        "filingDate": ["2026-05-01", "2026-02-01", "2026-08-01", "2026-08-19"],
        "primaryDocument": ["one.xml", "two.xml", "three.xml", "four.xml"],
    }}}
    found = in_scope_filings("0000000001", "Example Fund", payload)
    assert [filing.accession_number for filing in found] == ["a"]


def test_xml_choice_prefers_primary_document_then_non_info_table_xml():
    index = {"directory": {"item": [
        {"name": "primary_doc.xml"}, {"name": "infotable.xml"}, {"name": "schema.xml"},
    ]}}
    assert choose_xml_name(index, "primary_doc.xml") == "primary_doc.xml"
    assert choose_xml_name(index, "cover.htm") == "primary_doc.xml"


def test_xml_choice_returns_cover_and_separate_information_table():
    index = {"directory": {"item": [
        {"name": "primary_doc.xml"}, {"name": "infotable.xml"}, {"name": "schema.xsd"},
    ]}}
    assert choose_xml_documents(index, "primary_doc.xml") == ("primary_doc.xml", ["infotable.xml"])


def test_agent_returns_null_for_out_of_scope_quarter():
    result = answer_question("What was the average portfolio value in 2026 Q3?")
    assert result == {"answer": None, "unit": "NONE", "sources": []}


def test_agent_returns_accession_for_notice_query():
    result = answer_question("Which managers filed a 13F-NT in 2026 Q2?")
    assert result["unit"] == "NAME"
    assert result["sources"] == ["0001172661-26-003777"]
