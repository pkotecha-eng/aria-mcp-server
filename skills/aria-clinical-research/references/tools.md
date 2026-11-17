# ARIA tool reference (agent view)

What affects how an agent reads each tool's results. All three tools are read-only, need no API key, take `max_results` 1-10 (default 5; out-of-range values are rejected, not clamped), and return one text block per record separated by blank lines. Missing fields print as `N/A`.

## search_pubmed(query, max_results=5)

- `query` goes to NCBI esearch unchanged, so PubMed field tags and boolean operators work. See [query-syntax.md](query-syntax.md).
- No sort, date or filter parameters; use query tags (`[dp]`, `[pt]`) instead.
- Record fields: `Title`, `Authors`, `Journal/Year`, `Abstract`, `URL`.
- **There is no PMID line.** The PMID is the number in `URL: https://pubmed.ncbi.nlm.nih.gov/<PMID>/`.
- Abstract is cut at 500 characters and ends in `...` when cut.
- Empty result: `No papers found.`

## search_clinical_trials(condition, status="RECRUITING", intervention="", max_results=5)

- `condition` maps to the registry's condition field, `intervention` to its intervention field. Keep them separate.
- `status` is one of `RECRUITING` (default), `COMPLETED`, `ALL`. Other states (terminated, withdrawn, not yet recruiting, and so on) appear only under `ALL`.
- To check an NCT ID: pass the NCT ID as `condition` with `status=ALL`; accept the link only if a returned `NCT ID:` matches exactly; otherwise say ARIA couldn't confirm it.
- Record fields: `NCT ID`, `Title`, `Status`, `Phase`, `Condition(s)`, `Intervention(s)`, `Sponsor`, `Age Range`, `Sex`, `Locations` (first 3 only), `Summary`, `Primary Outcome`, `Secondary Outcomes` (first 3 only), `Inclusion Criteria`, `Exclusion Criteria`, `URL`.
- Empty result: `No clinical trials found matching those criteria.`

## search_isrctn(query, max_results=5)

- `query` is free-text keywords. **Searching by ISRCTN ID returns nothing**, even for an ID a topic search just returned.
- No status filter; `Status` is displayed only.
- Results are filtered after the fetch: a trial is kept only if some query word longer than 3 characters appears in its title or condition. Fewer than `max_results` results is therefore normal and not evidence that nothing exists.
- Record fields: `ISRCTN ID`, `Title`, `Status`, `Phase`, `Condition`, `Primary Outcome`, `Secondary Outcomes`, `Sponsor`, `Countries`, `Age Range`, `Gender`, `Inclusion Criteria`, `Exclusion Criteria`, `URL`.
- Empty result: `No ISRCTN trials found matching those criteria.`

## Truncation caps

A field is cut to its cap, with `...` replacing the last 3 characters, only when the source text is longer than the cap. No trailing `...` means the field is complete as returned.

| Field | PubMed | ClinicalTrials.gov | ISRCTN |
|---|---|---|---|
| Abstract | 500 | n/a | n/a |
| Summary | n/a | 400 | n/a |
| Primary outcome | n/a | 300 | 300 |
| Secondary outcomes | n/a | 300 | 300 |
| Inclusion criteria | n/a | 600 | 600 |
| Exclusion criteria | n/a | 800 | 800 |

## What `N/A` can mean

`N/A` is printed when a field is missing upstream **or** could not be extracted. It is never a statement that the thing does not exist.

- **ClinicalTrials.gov:** inclusion and exclusion criteria come from one upstream text field that is split on the exact phrase `Exclusion Criteria:`. If a trial words it differently, the exclusion list can show `N/A` even though exclusions exist. Open the `URL` before telling a user a trial has no exclusions.
- **ISRCTN:** inclusion and exclusion criteria are separate upstream fields; `N/A` means the registrant left it empty.

## Errors

- Invalid input (empty query, `max_results` outside 1-10, bad `status`) is rejected before the search runs.
- Upstream failures or timeouts raise an error whose message starts with the source name (for example `PubMed search failed: ...`). Treat as unavailable, not as empty.
- PubMed allows roughly 3 requests per second; avoid bursts of parallel PubMed calls.
