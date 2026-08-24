# Dependencies

Every library you added to `requirements-extra.txt`, with a one-line reason.

We are not counting libraries — a well-chosen dependency is better engineering than a
hand-rolled version of the same thing. What we are reading is whether you added each
one deliberately.

| Library | Version | Why |
|---|---|---|
| *(none)* | | `requirements-extra.txt` is empty. Everything the pipeline and agent need — `httpx` for EDGAR requests, `lxml` for namespace-aware XML parsing, `pyarrow` for the Parquet tables, `openai` for the LLM client, `python-dotenv` for config — was already available in the frozen `requirements.txt`, so nothing extra was added. |

## Anything you considered and rejected

- **A 13F-parsing library (e.g. `sec-edgar-api`/`edgartools`-style wrappers).** Rejected —
  the challenge is graded on the parsing itself, and a wrapper would hide exactly the
  namespace and duplicate-CUSIP edge cases (`submission/eda.py`) the schema depends on
  getting right. Hand-rolling the `lxml` traversal in `pipeline/structure.py` keeps every
  field's provenance explainable.
- **`rapidfuzz` for CIK name matching.** Considered for `pipeline/source.py::resolve_cik`,
  but the standard-library `difflib.SequenceMatcher` was accurate enough at the roster's
  scale (20 names) and avoided one more dependency to pin and justify.
- **`pandas` for building the Parquet tables.** Rejected in favor of building rows as
  plain dicts and handing them straight to `pyarrow.Table.from_pylist` with an explicit
  schema — one less layer between the parsed values and the exact Arrow types the schema
  requires.

## Note

Libraries that wrap 13F retrieval and parsing end to end will not, on their own,
satisfy the schema or the quality report, and we will ask you to explain the edge cases
in your output regardless of how you produced it. If you can explain it, you own it.
