# AI Usage

Declare what you used and how. We are not scoring the amount — we are checking that
you can account for your own work.

## Tools used

OpenAI Codex CLI (primary, throughout development) and Claude Code (Anthropic; used to
finish the submission — Git/GitHub setup and push, and drafting this documentation for
my review).

## Where you used them

Roughly, by chapter. A sentence each is enough.

| Chapter | How you used AI |
|---|---|
| 1 · Source | Used Codex to help work through the CIK-verification and EDGAR-discovery logic in `pipeline/source.py` — reasoning through the fuzzy-match thresholds, the archive vs. submissions API path/CIK-format differences, and the caching structure. |
| 2 · Interrogate | Used Codex to help explore the raw XML from Chapter 1 and organize the findings into `submission/eda.py` (namespace variance, the real 13F-NT filing, duplicate CUSIPs, the missing `putCall`/`None` elements). |
| 3 · Structure | Used Codex to help write the namespace-aware `lxml` parsing in `pipeline/structure.py` and align the two PyArrow schemas field-by-field with `docs/SCHEMA.md`. |
| 4 · Serve | Used Codex to help design and implement the two-tier `agents/answer.py`/`agents/planner.py` split — the deterministic pattern-matching layer, and the validated planner that constrains the LLM to a fixed, allow-listed intent schema rather than letting it generate code or SQL. |

Codex was used throughout as a coding assistant to help organize the logic and write the
code; I directed the approach and reviewed what it produced chapter by chapter. Claude
Code was brought in afterward, separately, to help get the finished repository committed
and pushed to GitHub and to draft `ASSUMPTIONS.md`/`DEPENDENCIES.md`/this file from the
actual code and outputs, for me to review.

## What you would change

With more time I would extend the deterministic layer in `agents/answer.py` to cover more
question phrasings before falling back to the LLM planner, and add unit tests for the
`agents/planner.py` validation paths (invalid operation, out-of-scope quarter, unknown
fund) rather than only exercising `agents/answer.py` end-to-end in `tests/test_pipeline.py`.
