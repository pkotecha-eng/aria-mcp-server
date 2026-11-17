# Handling gaps, truncation and failures

Use this when a result is empty, cut, missing a field, capped, or failed. The goal: report exactly what ARIA returned, and no more.

## Decision table

| Observation | What it means | Say / do |
|---|---|---|
| `No papers found.` / `No clinical trials found matching those criteria.` / `No ISRCTN trials found matching those criteria.` | This query matched nothing in this source | Reformulate (see below). Do not conclude "none exist" from one empty query |
| Tool error, timeout, message starting `<Source> search failed` | The call did not run to completion | Retry once. If it fails again, report the source as unavailable. Never treat as empty |
| Returned as many results as `max_results` requested (default 5, maximum 10) | The limit may have cut the list; there is no pagination or total count | "Showing N; more may exist." Raise `max_results` (up to 10) or refine the query to see others |
| Fewer results than requested (ISRCTN) | Post-filtering removed some, and the list may still have been cut before filtering | Not evidence of absence, and not evidence of completeness |
| Field ends in `...` | The field was cut at its cap | Quote what is shown, say it is cut, give the record `URL` |
| Field is `N/A` | Missing upstream or not extracted | Say "not available in ARIA's output"; never "none" or "no restrictions" |
| `Exclusion Criteria: N/A` on ClinicalTrials.gov | Possibly an unparsed exclusion list | Say exclusions could not be read; give the `URL` |
| Result off-topic for the question | Matched on a generic word | Say it is off-topic; do not use it as support |
| Paper names a trial ID not found in any result | ARIA could not confirm the trial | "Paper cites <ID>; ARIA could not confirm it." No link claimed |

## Reformulating an empty result

Try at least two changes before giving up on a source:

- **PubMed:** MeSH to `[tiab]`; add synonyms with OR; drop the study-type tag; generic name instead of brand name.
- **ClinicalTrials.gov:** `status=ALL`; broaden the condition; drop `intervention`; generic drug name.
- **ISRCTN:** single most distinctive keyword; synonym; broader condition term.

## When to say "insufficient data"

Say it when all hold:

1. You tried at least two formulations in each relevant source.
2. What came back is empty, off-topic, or missing the field the answer depends on.
3. No searched source supports the claim.

Then state: what was searched (tools, queries, result counts), what ARIA cannot cover (full texts, other registries, results data), and what would answer the question.

Example wording: "Insufficient data. I searched PubMed (2 queries, 0 relevant results), ClinicalTrials.gov (status=ALL, 2 queries, 0 results) and ISRCTN (1 query, 0 results) for <topic>. ARIA does not search full texts or other registries. A full-text literature review or a regulator database would be needed to answer this."

## What not to do

- Do not fill a gap with a plausible-sounding paper, trial, drug name, number or identifier.
- Do not present background knowledge as a search finding. If you offer it, label it unverified and give no identifier.
- Do not convert "ARIA returned nothing" into "nothing exists".
- Do not repair a cut field by guessing its ending.
- Do not report a failed call as an empty result.

## Keeping the record honest

What you tell the user and the methods note must agree. If the answer says a field was cut, unavailable, or a source failed, the methods note says the same. Do not soften the answer while the methods note still reports a clean, complete search.
