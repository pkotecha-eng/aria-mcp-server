# Query construction by source

## PubMed (`search_pubmed`)

The `query` string goes to NCBI unchanged. PubMed syntax therefore works. There is no sort, date or filter parameter, so express those in the query.

| Goal | Pattern |
|---|---|
| Controlled vocabulary | `"Epilepsy"[MeSH Terms]` |
| Words in title/abstract | `"ketogenic diet"[tiab]` |
| Combine | `"Epilepsy"[MeSH Terms] AND "ketogenic diet"[tiab]` |
| Synonyms | `("heart failure"[tiab] OR "cardiac failure"[tiab])` |
| Study type | `randomized controlled trial[pt]`, `meta-analysis[pt]`, `systematic review[pt]` |
| Date range | `2020:2025[dp]` |
| Exclude | `... NOT animals[mh]` |

Tips:
- Start with a precise query (MeSH AND intervention). If it returns fewer than 3 results, drop to `[tiab]` terms, then to plain words.
- Quote multi-word phrases; unquoted words are matched separately.
- Run a second query by study type (`[pt]`) instead of asking for more results: there is no pagination, so different queries are the only way to see different records.
- Misspelled or unknown drug names return empty; try the generic name, then the brand name.

## ClinicalTrials.gov (`search_clinical_trials`)

- `condition`: the disease only (`"lung cancer"`, `"pediatric epilepsy"`). `intervention`: the drug or procedure only (`"pembrolizumab"`). Do not combine them in one string.
- Prefer the generic drug name; run the brand name as a second query.
- `status`: use `ALL` when asking whether any trial exists or when reviewing the landscape; `RECRUITING` only for "what can a patient join now"; `COMPLETED` for finished trials. The default is `RECRUITING`.
- Narrowing: add `intervention`. Broadening: drop `intervention`, then use a parent condition (`"epilepsy"` instead of `"Dravet syndrome"`).
- To check one NCT ID: pass it as `condition` with `status=ALL`; accept only an exact `NCT ID:` match.
- Phase, sponsor, country and date have no parameters. Read them from results and filter yourself; say so if a call returned as many results as requested, since more matches may exist.

## ISRCTN (`search_isrctn`)

- One plain keyword query (`"insomnia adolescents"`). No operators, no status filter.
- Use distinctive words longer than 3 characters; shorter words are ignored by the relevance filter, and a query with no word longer than 3 characters is not filtered at all.
- A trial is kept if any one query word (longer than 3 characters) appears in its title or condition. Adding words therefore broadens the results, including trials that match only a generic word. Use the single most distinctive word for a precise search, add words only to widen, and read each title before treating a result as on-topic.
- Do not search by ISRCTN ID; it returns nothing.
- ISRCTN covers UK and European registrations. A trial may also appear on ClinicalTrials.gov; treat matching titles as possible duplicates, not as two trials.
