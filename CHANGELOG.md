# Changelog

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
