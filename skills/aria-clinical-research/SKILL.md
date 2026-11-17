---
name: aria-clinical-research
description: Use ARIA's search_pubmed, search_clinical_trials and search_isrctn tools for clinical research questions - literature searches, evidence reviews for a drug or intervention in a condition, finding or checking registered trials (ClinicalTrials.gov NCT IDs, UK/European ISRCTN IDs), trial status and eligibility criteria, safety or adverse-event evidence, and "is there a trial for X?" questions. Covers which tool to use, query construction, multi-step workflows, handling empty, truncated or partial results, and citing PMIDs, NCT IDs and ISRCTN IDs without inventing any. Not for individual diagnosis or treatment advice.
---

# ARIA clinical research

ARIA returns short structured snippets from three public sources. It is an evidence-retrieval aid, not a source of medical advice. Tools are referred to here by bare name (`search_pubmed`, `search_clinical_trials`, `search_isrctn`); your client may prefix them.

## 1. Pick the tool

| Question is about | Tool | Notes |
|---|---|---|
| Published evidence: efficacy, safety, mechanism, outcomes, reviews | `search_pubmed` | Peer-reviewed literature only; no trial status |
| Registered trials, US or global; status, phase, sponsor, eligibility | `search_clinical_trials` | Default `status` is `RECRUITING` and hides everything else |
| UK or European registered trials | `search_isrctn` | No status filter; some UK trials are also on ClinicalTrials.gov |
| "Everything on X" / evidence review | All three, in the order in section 2 | |

Do not call a tool whose source cannot answer the question (for example, PubMed cannot tell you whether a trial is recruiting).

## 2. Workflow: evidence review for intervention X in condition Y

1. **PubMed first.** Run 2-3 query variants (MeSH, free-text `[tiab]`, and a `[pt]` filter for trials or reviews). See `references/query-syntax.md`.
2. **Trial registries.** `search_clinical_trials` with `condition=Y`, `intervention=X`, and `status=ALL` so completed, terminated and not-yet-recruiting trials are visible. Then `RECRUITING` if the user asks about ongoing work. Add `search_isrctn` when UK/EU relevance is plausible.
3. **Cross-reference.** Scan abstracts and trial summaries for NCT or ISRCTN identifiers. No tool has an ID-lookup parameter:
   - **NCT ID:** pass the NCT ID as `condition` with `status=ALL` (the default `RECRUITING` hides completed trials). Accept the link only if a returned `NCT ID:` matches the ID exactly; otherwise say ARIA couldn't confirm it.
   - **ISRCTN ID:** do not search by ID; it returns nothing, even for an ID that a topic search just returned. Match an ISRCTN ID only against trials already returned by `search_isrctn` topic queries. If it is not among them, say the paper names that trial but ARIA couldn't confirm it, and do not claim a link.
   - A registered trial with no matching paper is "no published results found in ARIA", not "unpublished".
4. **Synthesize** with the output contract in section 6.
5. **Budget.** Stop at roughly 8-10 tool calls. Say what you did not search.

More worked examples: `references/workflows.md`.

## 3. Reading results correctly

- **Each call returns at most `max_results` records (default 5, maximum 10), with no pagination and no total count.** If a call returns as many records as you requested, the list may be cut off; do not treat it as complete. Never claim exhaustive coverage; raise `max_results` (up to 10) or vary the query instead.
- **A trailing `...` means the field was cut.** No `...` means the field is complete as returned. For decisions that depend on a cut field, point the user to the record URL. Per-field caps are in `references/tools.md`.
- **`N/A` means "not extracted or not provided", never "none".** In particular, `Exclusion Criteria: N/A` does NOT mean a trial has no exclusions. Say the exclusion criteria were not available and give the URL.
- **PubMed output has no PMID line.** The PMID is the number in the `URL:` line (`pubmed.ncbi.nlm.nih.gov/<PMID>/`). Cite that number.
- **ISRCTN** can return fewer results than requested (relevance filtering happens after the limit). Fewer results is not evidence of absence.

Field details and caps: `references/tools.md`.

## 4. Empty, partial, truncated and failed results

| Situation | Do |
|---|---|
| Empty result | Reformulate at least twice (synonyms, drop the intervention, broader condition, `status=ALL`) before concluding anything |
| Tool error or timeout | Retry once. If it still fails, report that source as unavailable. A failed call is not an empty result |
| Returned as many results as `max_results` requested | Say results may be incomplete; refine or raise `max_results` (up to 10) rather than extrapolate |
| Truncated or `N/A` field you need | Say so, quote what you have, give the URL |
| Results off-topic | Say they were off-topic; do not stretch them to fit |

**Say "insufficient data" when all of these hold:** you tried at least two formulations in each relevant source; what came back is empty, off-topic, or missing the decisive field; and no source you searched supports the claim. Then state what was searched, and what would answer the question (for example a full-text review or a different registry).

Never fill a gap from memory presented as a finding. Background knowledge may be offered only if clearly labelled unverified and carrying no identifier. Details: `references/gaps-and-truncation.md`.

## 5. Query construction (short form)

- **PubMed:** the query goes to NCBI unchanged, so field tags work: `"epilepsy"[MeSH Terms] AND "ketogenic diet"[tiab] AND randomized controlled trial[pt]`.
- **ClinicalTrials.gov:** `condition` is the disease, `intervention` is the drug or procedure; keep them separate rather than one long string. Use the generic drug name; try a brand name as a second query.
- **ISRCTN:** one plain keyword query; use distinctive words longer than 3 characters.

Full syntax and patterns: `references/query-syntax.md`.

## 6. Output contract

- Every claim drawn from search results carries an identifier: **PMID** (the number in the record's `URL:` line), **NCT ID** (the `NCT ID:` field), or **ISRCTN ID** (the `ISRCTN ID:` field).
- Copy identifiers exactly as they appear in tool output. Never construct, guess, repair or "complete" an identifier, and never cite one you did not see in a result during this task.
- Keep three things distinct: found in ARIA results (cited), background knowledge (labelled unverified, no identifier), and not found.
- End with a short methods note: which tools, which queries, how many results each returned, and known limits (any call that returned as many results as requested, truncated fields, status filter used, sources not searched).
- Do not give individual medical advice; present evidence and direct clinical decisions to a clinician.
