# Changelog

## [0.2.1] - 2026-07-13
### Changed
- Consistency pass: all references to search_clinical_trials eligibility 
  criteria (tool description, output_schema, docstring, server instructions 
  block) now uniformly describe inclusion/exclusion as separate fields, 
  matching actual v0.2.0 return behavior
- TECHNICAL_REFERENCE.md corrected to match (two output_schema description 
  mismatches from the v0.2.0 doc regeneration also fixed)

## [0.2.0] - 2026-07-12

### Breaking
- `eligibility_criteria` field split into `inclusion_criteria` (600 char cap) and `exclusion_criteria` (800 char cap) on `search_clinical_trials` and `search_isrctn`
- Tool inputs now validated via Pydantic `Field` constraints — previously-accepted out-of-range values (`max_results` outside 1-10, invalid `status` literal, empty `query`/`condition`) now raise a `ValidationError` instead of being silently passed through or clamped

### Fixed
- `structured_content` was broken for all three tools — `output_schema` declared a dict but tool functions returned raw strings, causing `ToolError` for any MCP client enforcing the schema
- ISRCTN exclusion criteria was never reliably surfacing due to a flawed combined-string parse; now reads `inclusion_criteria`/`exclusion_criteria` directly from separate upstream fields, mirroring the CT.gov structure
- ISRCTN `primary_outcome`/`secondary_outcomes` always appended `"..."` regardless of whether truncation occurred (present since v0.1.8) — now conditional, matching the pattern already used elsewhere in the file
- Truncated `inclusion_criteria`/`exclusion_criteria` fields (both CT.gov and ISRCTN) now signal truncation with `"..."` only when content is actually cut, instead of silently ending mid-sentence at the char cap

### Changed
- Removed dead `max_results` clamp logic in `server.py` — redundant with Pydantic's `Field(ge=1, le=10)` validation, which already rejects out-of-range values before the function body runs
- Docstrings corrected: `max_results` behavior now described as "rejected" rather than "clamped"

### Removed
- `httpx` dependency (declared since v0.1.0, never actually imported or used anywhere in the codebase — `requests` has been the real HTTP client throughout)

## [0.1.9] - 2026-05-04
### Fixed
- Relaxed ISRCTN relevance filter from `all()` to `any()` on significant words (>3 chars) — improves recall for medical conditions with synonyms (e.g. "beta thalassemia" now matches "thalassemia major")

## [0.1.8] - 2026-05-03
### Added
- `search_isrctn` tool: UK/European clinical trials via ISRCTN registry (no API key required)
- Primary and secondary outcomes fields for both ClinicalTrials.gov and ISRCTN results
- Relevance filtering for ISRCTN results (query terms must appear in title or condition)

## [0.1.7] - 2026-04-28
- Add behavioral disclosure to tool descriptions (rate limits, auth, pagination)
- Add output_schema descriptions for improved tool definition quality

## [0.1.6] - 2026-04-27
- Add output_schema with result descriptions for both tools

## [0.1.5] - 2026-04-26
- Add Annotated type hints with descriptions to tool parameters

## [0.1.4] - 2026-04-25
- Improve tool docstrings with Returns and Notes sections

## [0.1.2] - 2026-04-23
- Initial publication to Official Anthropic MCP Registry
- Add PyPI packaging with pyproject.toml

## [0.1.0] - 2026-04-14
- Initial release
- search_pubmed and search_clinical_trials tools
- Resources: trial phases, FDA databases, high-impact journals
- Prompts: clinical-research-brief, adverse-event-analysis, trial-eligibility-checker
