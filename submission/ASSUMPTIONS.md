# Assumptions

Where the specification was ambiguous, or where you asked a question and kept working
rather than waiting on an answer, record the call you made and why.

This is not a penalty. A documented assumption is a normal part of data work — the
alternative is a stalled pipeline or a silent guess nobody can audit later. We read
this alongside your output, and a well-reasoned assumption that differs from ours costs
you nothing.

| # | What was unclear | What you assumed | Why |
|---|---|---|---|
| 1 | How aggressively to trust a fuzzy CIK match when the roster's CIK doesn't match SEC's lookup file at all | In `pipeline/source.py::resolve_cik`, only accept a fuzzy match when it scores ≥0.93 *and* beats the runner-up by ≥0.04 *and* resolves to exactly one CIK; otherwise raise rather than guess. Two roster entries needed correction this way: `Tudor Investment Corp` (roster CIK `0000854157` → correct `0000923093`) and `Situational Awareness LP` (roster CIK `0001697748` → correct `0002045724`). | The brief says a wrong CIK "resolves to a real company, and every step after it runs cleanly on the wrong manager's data" — a silent low-confidence guess is worse than no guess. Every corrected row is recorded with `cik_source=corrected` in `output/filers.csv` so the fix is auditable, not hidden. |
| 2 | SEC's name lookup sometimes maps one normalized name to *several* CIKs (multiple legal entities under variants of the same name), with no single fuzzy match to fall back on | Break the tie using evidence instead of dictionary order: fetch each candidate CIK's submissions and count how many in-scope 13F filings (right form, right report periods, right cutoff) it actually has. Pick the candidate with a clear lead; refuse if there's a tie or none qualify. | A name collision by itself carries no information about which entity is "our" manager, but "which of these CIKs actually filed the 13Fs we're looking for" does. This keeps the resolution grounded in the same scope filter the rest of the pipeline uses, rather than an arbitrary pick. |
| 3 | A filing whose EDGAR index unexpectedly lists more than one non-schema information-table XML (the spec assumes one cover + one information table) | Treat it as a hard error (`SourceError`) and stop, rather than silently keeping one and dropping the other. | Guessing which information table is "the real one" risks silently discarding real holdings — worse than a loud failure I can go inspect by hand. None of the 40 in-scope filings actually hit this case, so it never fired, but the pipeline documents the decision rather than staying silent about it. |
| 4 | `Sole`/`Shared`/`None` voting-authority elements are schema-required (`voting_sole`, `voting_shared`, `voting_none`) but the XSD marks the whole `votingAuthority` block optional per SEC's 13F spec | When the element is entirely absent from an `infoTable`, default it to `0` rather than null, since the schema forbids a null there. | A missing voting-authority element means "no shares in that bucket," not "unknown" — 13F filers who don't split voting authority still hold and vote the shares somehow, so `0` is the accurate reading, not a placeholder. |
| 5 | `agents/llm.py` in this repo (generated 2026-08-19) differs from the current upstream `GiesDSRS/dsrs-curator` template in how guided JSON decoding is requested (`extra_body={"guided_json": ...}` vs. `response_format={"type": "json_schema", ...}`) | Left the frozen file exactly as generated and did not "fix" it to match the newer upstream version. | It's a frozen file — the brief says modifications get discarded and code then runs against the original anyway. The difference reflects the template being updated after this repo was generated from it, not a local edit. |

## Questions you sent us

If you emailed dsrs@business.illinois.edu and proceeded before hearing back, note it
here so we can see what you were working around.

| Question | Date sent | What you did in the meantime |
|---|---|---|
| | | |
