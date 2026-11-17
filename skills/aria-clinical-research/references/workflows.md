# Worked workflows

Identifiers below are placeholders in angle brackets. In real answers, copy identifiers only from tool output.

## A. Evidence review: intervention X in condition Y

1. `search_pubmed`: `"Y"[MeSH Terms] AND "X"[tiab]` (max_results 10).
2. `search_pubmed`: same terms plus `(randomized controlled trial[pt] OR meta-analysis[pt] OR systematic review[pt])`.
3. `search_clinical_trials`: `condition="Y"`, `intervention="X"`, `status=ALL`, max_results 10.
4. If UK/EU relevance is plausible: `search_isrctn`: `"Y X"`.
5. Cross-reference: list every `NCT` or `ISRCTN` identifier mentioned in abstracts or trial summaries. For each NCT ID, pass it as `condition` with `status=ALL` and accept it only on an exact `NCT ID:` match. For each ISRCTN ID, check it against trials already returned.
6. Write up in three groups: published evidence (PMIDs), registered trials split by status (NCT/ISRCTN IDs), and gaps (registered with no paper found; paper naming a trial ARIA could not confirm).
7. Methods note: queries run, result counts, any call that returned as many results as requested, any truncated fields.

About 6-8 calls. Stop there and say what was not searched.

## B. Safety or adverse-event signal for drug X

1. `search_pubmed`: `"X"[tiab] AND (adverse effects[sh] OR safety[tiab])`, then a second query with `meta-analysis[pt] OR systematic review[pt]`.
2. `search_clinical_trials`: `intervention="X"`, `status=ALL`; read `Status` on each record to spot early stops. ARIA returns no termination reason and no results data; say so.
3. Report what the abstracts state. Abstracts are cut at the cap, so for any rate or event count that is cut, give the URL instead of estimating.

## C. Trial screening for a patient profile

This is a screening aid for discussion with the trial team and a clinician, not an eligibility determination. Don't ask for or repeat identifying patient details.

1. `search_clinical_trials` with `status=ALL`, condition and intervention if given. Keep only records whose `Status` is `RECRUITING` or `NOT_YET_RECRUITING`, since the `RECRUITING` filter hides not-yet-recruiting trials. If the call returned as many results as requested and none of them are those statuses, run a second query with `status=RECRUITING` and say further matches may exist.
2. For each kept trial, compare the profile against `Age Range`, `Sex`, `Inclusion Criteria` and `Exclusion Criteria`.
3. Rules:
   - `...` on a criteria field: the list is incomplete; label the trial "unclear" and give the URL.
   - `Exclusion Criteria: N/A`: unknown, not "none"; label the trial "unclear".
   - Only the first 3 locations are shown; do not state that a country has no site.
4. Label each trial **possible match**, **unclear** or **likely excluded**, with the field that decided it. Direct the final decision to the trial's contact and a clinician.

## D. "Is there a trial for X?"

1. `search_clinical_trials`: `condition`/`intervention` as given, `status=ALL`.
2. If empty: retry with a broader condition, then with the intervention alone, then (if relevant) `search_isrctn` with a single distinctive keyword.
3. Found: report IDs and statuses. Empty after the retries in each relevant source: answer "insufficient data: no matching registered trial found in ClinicalTrials.gov or ISRCTN via ARIA", list the queries tried, and note that ARIA covers only those two registries.

## E. Question where the sources do not support an answer

Example: efficacy figures for a drug or combination that returns no relevant papers and no trials.

1. Run the PubMed and registry queries from workflow A with at least two formulations each.
2. If everything is empty or off-topic, answer "insufficient data", list what was searched, and say what would answer it (for example a different database or full-text review).
3. Do not supply numbers, trial names or identifiers from memory.
