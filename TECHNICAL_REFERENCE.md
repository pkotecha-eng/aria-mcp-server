# ARIA Clinical Research MCP Server — Technical Reference

> **Version:** 0.2.1 · **Python:** ≥3.10 · **Transport:** stdio (default) or HTTP

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Project Structure](#2-project-structure)
3. [Installation & Entry Points](#3-installation--entry-points)
4. [Transport Modes](#4-transport-modes)
5. [MCP Server Object](#5-mcp-server-object)
6. [Tools](#6-tools)
   - [search_pubmed](#61-search_pubmed)
   - [search_clinical_trials](#62-search_clinical_trials)
   - [search_isrctn](#63-search_isrctn)
7. [Resources](#7-resources)
8. [Prompts](#8-prompts)
9. [Internal Data Layer (`tools.py`)](#9-internal-data-layer-toolspy)
   - [PubMed pipeline](#91-pubmed-pipeline)
   - [ClinicalTrials.gov pipeline](#92-clinicaltrialsgov-pipeline)
   - [ISRCTN pipeline](#93-isrctn-pipeline)
10. [Data Models](#10-data-models)
11. [Error Handling](#11-error-handling)
12. [Rate Limits & Constraints](#12-rate-limits--constraints)
13. [Dependencies](#13-dependencies)
14. [Registry Metadata](#14-registry-metadata)
15. [Changelog Summary](#15-changelog-summary)

---

## 1. Architecture Overview

```
MCP Client (Claude Desktop / any MCP host)
        │  MCP protocol (stdio or HTTP)
        ▼
┌─────────────────────────────────────────────────────┐
│                  server.py                          │
│  FastMCP instance: "aria-clinical-research"         │
│  ┌──────────┐  ┌────────────────────┐  ┌─────────┐ │
│  │  Tools   │  │     Resources      │  │ Prompts │ │
│  │  × 3     │  │  × 4 (read-only)   │  │  × 3   │ │
│  └────┬─────┘  └────────────────────┘  └─────────┘ │
└───────┼─────────────────────────────────────────────┘
        │ Python function calls (module-level aliased imports)
        ▼
┌─────────────────────────────────────────────────────┐
│                  tools.py                           │
│  search_pubmed()          → format_results_for_claude() │
│  search_clinical_trials() → format_trials_for_claude()  │
│  search_isrctn()          → format_isrctn_for_claude()  │
└─────────────────────────────────────────────────────┘
        │  HTTP (requests)
        ▼
┌─────────────────────┬───────────────────────┬──────────────────────┐
│  NCBI E-utilities   │  ClinicalTrials.gov    │  ISRCTN Registry     │
│  PubMed XML API     │  v2 REST JSON API      │  WHO-format XML API  │
│  eutils.ncbi.nlm…   │  clinicaltrials.gov/…  │  isrctn.com/api/…    │
└─────────────────────┴───────────────────────┴──────────────────────┘
```

ARIA is a **read-only, zero-authentication** MCP server. All three upstream APIs are public; no API keys are stored or required.

---

## 2. Project Structure

```
aria-mcp-server/
├── aria_mcp_server/
│   ├── __init__.py           # package marker
│   ├── server.py             # MCP surface: tools, resources, prompts, entry point
│   └── tools.py              # HTTP clients, parsers, formatters
├── pyproject.toml            # build config, deps, entry-point script
├── requirements.txt          # pinned deps for Docker / Glama
├── server.json               # Official MCP Registry metadata
├── glama.json                # Glama registry metadata
├── CHANGELOG.md
├── README.md
└── LICENSE
```

---

## 3. Installation & Entry Points

### PyPI install (recommended)

```bash
pip install aria-mcp-server
aria-mcp-server                        # stdio mode
aria-mcp-server --transport http       # HTTP mode on port 8000
```

The `aria-mcp-server` console script is declared in `pyproject.toml`:

```toml
[project.scripts]
aria-mcp-server = "aria_mcp_server.server:run"
```

### Direct module execution

```bash
python -m aria_mcp_server.server
python -m aria_mcp_server.server --transport http
```

### Claude Desktop config

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aria-clinical-research": {
      "command": "aria-mcp-server"
    }
  }
}
```

### npx / uvx (if published to npm/uvx index)

```bash
uvx aria-mcp-server
```

---

## 4. Transport Modes

The `run()` function in `server.py` selects the transport at startup:

```python
def run():
    import sys
    transport = "http" if "--transport" in sys.argv and "http" in sys.argv else "stdio"
    if transport == "http":
        mcp.run(transport="http", port=8000)
    else:
        mcp.run(transport="stdio")
```

| Mode    | Flag               | Port | Use case                                      |
|---------|--------------------|------|-----------------------------------------------|
| `stdio` | _(default)_        | n/a  | Claude Desktop, local Claude Code MCP clients |
| `http`  | `--transport http` | 8000 | Remote clients, Docker, cloud deployment      |

---

## 5. MCP Server Object

```python
from fastmcp import FastMCP

mcp = FastMCP(
    name="aria-clinical-research",
    instructions="…"   # system-level guidance injected into the MCP host context
)
```

`instructions` tells the MCP host **when to invoke which tool**:

- `search_pubmed` → published research, mechanisms, outcomes
- `search_clinical_trials` → active/completed trials, inclusion/exclusion criteria, recruiting studies
- `search_isrctn` → UK/European trials not on ClinicalTrials.gov
- All three together → comprehensive global clinical intelligence

---

## 6. Tools

All tools are registered with `@mcp.tool(description=…, output_schema=…)` and are **synchronous**. Each tool:

1. **Validates** inputs via Pydantic `Field` constraints declared inline in the signature (`Annotated[type, Field(...)]`) — invalid input (out-of-range `max_results`, empty `query`/`condition`, an invalid `status` literal) is rejected with a `ValidationError` **before the function body runs**. This replaced an earlier pattern where `max_results` was silently clamped into range with `max(1, min(max_results, 10))`; that clamp is now dead code (Pydantic already guarantees the bound) and has been removed.
2. Calls the corresponding function from `tools.py` via a module-level aliased import (e.g. `_raw_search_pubmed`, `_fmt_pubmed`) — the alias avoids a name collision, since each `@mcp.tool` function is defined with the *same name* as the `tools.py` function it wraps (`def search_pubmed(...)` would otherwise shadow the imported `search_pubmed` the moment the `def` executes).
3. Formats the result and returns a **dict** — `{"result": "<formatted string>"}` — matching the declared `output_schema`. (Earlier versions returned a bare string, which mismatched the schema and broke `structured_content` for any MCP client that enforces it.)

---

### 6.1 `search_pubmed`

**Purpose:** Search PubMed for peer-reviewed biomedical literature.

**Signature:**
```python
def search_pubmed(
    query: Annotated[str, Field(min_length=1, description="Search query e.g. 'velarixin pediatric epilepsy phase 2'")],
    max_results: Annotated[int, Field(ge=1, le=10, description="Number of papers to return, between 1 and 10")] = 5,
) -> dict
```

**Parameters:**

| Parameter    | Type  | Required | Default | Constraints              | Description                          |
|--------------|-------|----------|---------|---------------------------|--------------------------------------|
| `query`      | `str` | ✅        | —       | `min_length=1`             | PubMed search query string           |
| `max_results`| `int` | ❌        | `5`     | `ge=1, le=10` (rejected if out of range) | Number of papers to return |

**Returns:** `{"result": "<formatted string>"}` (see [§10 Data Models](#10-data-models) for field list).

**Returns on no results:** `{"result": "No papers found."}`

**API used:** NCBI E-utilities (`esearch.fcgi` + `efetch.fcgi`)

**Rate limit:** ~3 requests/sec (unauthenticated NCBI public API)

**Output schema:**
```json
{
  "type": "object",
  "properties": {
    "result": {
      "type": "string",
      "description": "Formatted list of papers with title, authors, journal, year, PMID, and abstract. Returns 'no results' message if nothing found."
    }
  },
  "required": ["result"]
}
```

---

### 6.2 `search_clinical_trials`

**Purpose:** Search ClinicalTrials.gov for clinical studies.

**Signature:**
```python
def search_clinical_trials(
    condition: Annotated[str, Field(min_length=1, description="Disease or condition e.g. 'pediatric epilepsy', 'lung cancer'")],
    status: Annotated[Literal["RECRUITING", "COMPLETED", "ALL"], Field(description="Trial status: RECRUITING, COMPLETED, or ALL")] = "RECRUITING",
    intervention: Annotated[str, Field(description="Optional drug or intervention name to narrow results")] = "",
    max_results: Annotated[int, Field(ge=1, le=10, description="Number of trials to return, between 1 and 10")] = 5,
) -> dict
```

**Parameters:**

| Parameter      | Type  | Required | Default       | Constraints                                    | Description                             |
|----------------|-------|----------|---------------|-------------------------------------------------|------------------------------------------|
| `condition`    | `str` | ✅        | —             | `min_length=1`                                   | Disease or condition name               |
| `status`       | `str` | ❌        | `RECRUITING`  | `Literal["RECRUITING", "COMPLETED", "ALL"]` (rejected if not one of these) | Trial status filter |
| `intervention` | `str` | ❌        | `""`          | Optional                                         | Drug or intervention name               |
| `max_results`  | `int` | ❌        | `5`           | `ge=1, le=10` (rejected if out of range)         | Number of trials to return              |

**Returns:** `{"result": "<formatted string>"}` with trial details, including separate `Inclusion Criteria:` and `Exclusion Criteria:` lines (see [§9.2](#92-clinicaltrialsgov-pipeline) and [§10](#10-data-models)).

**Returns on no results:** `{"result": "No clinical trials found matching those criteria."}`

**API used:** ClinicalTrials.gov v2 REST API (`https://clinicaltrials.gov/api/v2/studies`)

**Query parameters sent:**

| CT.gov param           | Derived from          |
|------------------------|-----------------------|
| `query.cond`           | `condition`           |
| `query.intr`           | `intervention` (if set)|
| `filter.overallStatus` | `status` (if ≠ ALL)   |
| `pageSize`             | `max_results`         |
| `format`               | `"json"` (hardcoded)  |
| `countTotal`           | `"true"` (hardcoded)  |

**Output schema:**
```json
{
  "type": "object",
  "properties": {
    "result": {
      "type": "string",
      "description": "Formatted list of trials with NCT ID, title, phase, status, sponsor, conditions, interventions, and eligibility criteria (inclusion and exclusion, listed separately). Returns 'no results' message if nothing found."
    }
  },
  "required": ["result"]
}
```

---

### 6.3 `search_isrctn`

**Purpose:** Search the ISRCTN registry for UK and European clinical trials.

**Signature:**
```python
def search_isrctn(
    query: Annotated[str, Field(min_length=1, description="Condition or search terms e.g. 'pediatric epilepsy', 'type 2 diabetes'")],
    max_results: Annotated[int, Field(ge=1, le=10, description="Number of trials to return, between 1 and 10")] = 5,
) -> dict
```

**Parameters:**

| Parameter    | Type  | Required | Default | Constraints                              | Description                         |
|--------------|-------|----------|---------|--------------------------------------------|--------------------------------------|
| `query`      | `str` | ✅        | —       | `min_length=1`                             | Condition or free-text search terms |
| `max_results`| `int` | ❌        | `5`     | `ge=1, le=10` (rejected if out of range)   | Number of trials to return          |

**Returns:** `{"result": "<formatted string>"}` with ISRCTN trial details, including separate `Inclusion Criteria:` and `Exclusion Criteria:` lines.

**Returns on no results:** `{"result": "No ISRCTN trials found matching those criteria."}`

**API used:** ISRCTN WHO-format XML API (`https://www.isrctn.com/api/query/format/who`)

**Relevance filtering:** After the API response is fetched, results are post-filtered — any word from `query` with length >3 must appear in the trial's `public_title` or `hc_freetext` fields. This prevents off-topic results from broad keyword matches.

**Output schema:**
```json
{
  "type": "object",
  "properties": {
    "result": {
      "type": "string",
      "description": "Formatted list of trials with ISRCTN ID, title, phase, status, sponsor, condition, outcomes, countries, and inclusion/exclusion criteria."
    }
  },
  "required": ["result"]
}
```

---

## 7. Resources

Resources are **read-only, static text** exposed via `@mcp.resource(uri)`. They do not make API calls.

| URI                            | Function                    | Content                                                        |
|--------------------------------|-----------------------------|----------------------------------------------------------------|
| `info://aria`                  | `aria_info()`               | ARIA capability overview, data sources, author credits         |
| `reference://trial-phases`     | `trial_phases()`            | Phase I–IV definitions, duration, participant counts, FDA special designations |
| `reference://high-impact-journals` | `high_impact_journals()` | Curated high-impact journals by specialty with approximate impact factors |
| `reference://fda-databases`    | `fda_databases()`           | FDA database URLs for drug info, adverse events, regulatory guidance, devices, biomarkers |

---

## 8. Prompts

Prompts are **parameterised system-level instructions** returned as strings via `@mcp.prompt(name)`. The MCP host injects them as the initial user or system message.

### `clinical-research-brief`

```python
def clinical_research_brief(condition: str) -> str
```

Instructs the model to act as a senior clinical research analyst and produce a 4-section structured brief: Evidence Base, Active Trials, Evidence Gaps, Clinical Implications.

**Parameters:** `condition` (str) — the disease/condition to research.

---

### `adverse-event-analysis`

```python
def adverse_event_analysis(drug: str, condition: str) -> str
```

Instructs the model to act as a pharmacovigilance specialist and structure a 5-section AE analysis: Common AEs, Serious/Rare Events, Population-Specific Risks, Monitoring Recommendations, Regulatory Status.

**Parameters:** `drug` (str), `condition` (str).

---

### `trial-eligibility-checker`

```python
def trial_eligibility_checker(condition: str, patient_profile: str) -> str
```

Instructs the model to act as a clinical trial coordinator, search for recruiting trials, and assess each trial's inclusion/exclusion criteria against the given patient profile with an eligibility rating (Likely / Possibly / Likely Ineligible).

**Parameters:** `condition` (str), `patient_profile` (str).

---

## 9. Internal Data Layer (`tools.py`)

`tools.py` contains all HTTP clients, XML/JSON parsers, and text formatters. It is **never exposed directly to the MCP protocol** — `server.py` is the only public surface. Its public names (`search_pubmed`, `search_clinical_trials`, `search_isrctn`, and their `format_*_for_claude` counterparts) are imported into `server.py` under `_raw_*`/`_fmt_*` aliases to avoid collisions with the identically-named `@mcp.tool` functions.

### 9.1 PubMed Pipeline

**Base URL:** `https://eutils.ncbi.nlm.nih.gov/entrez/eutils`

**Two-step fetch pattern:**

```
esearch.fcgi?db=pubmed&term=<query>&retmax=<n>&retmode=xml
    → IdList of PMIDs

efetch.fcgi?db=pubmed&id=<comma-sep PMIDs>&rettype=xml
    → PubmedArticleSet XML
```

**Key internal functions:**

| Function | Signature | Purpose |
|----------|-----------|---------|
| `_get_text(node)` | `(Any) → str` | Safely extracts text from xmltodict string, dict with `#text` key, or other types |
| `_extract_authors(author_list)` | `(Any) → list[str]` | Parses AuthorList dict into `["Last First", …]` strings |
| `_parse_article(article_xml)` | `(dict) → dict \| None` | Extracts PMID, title, authors, journal, year, abstract (≤500 chars), URL from one PubmedArticle element |
| `search_pubmed(query, max_results)` | `(str, int) → list[dict]` | Runs esearch + efetch; returns list of parsed article dicts |
| `format_results_for_claude(results)` | `(list[dict]) → str` | Renders articles as numbered `[Paper N]` blocks |

**Abstract truncation:** abstracts are truncated to 500 characters (`abstract[:497] + "..."`), conditional on `len(abstract) > 500`.

---

### 9.2 ClinicalTrials.gov Pipeline

**Base URL:** `https://clinicaltrials.gov/api/v2/studies`

**Response format:** JSON

**Key internal functions:**

| Function | Signature | Purpose |
|----------|-----------|---------|
| `_ct_get(d, *keys, default="")` | `(Any, *str, str) → str` | Safely traverses nested dicts via a key path; joins lists with `"; "` |
| `_parse_trial(study)` | `(dict) → dict \| None` | Extracts all fields from a `protocolSection` JSON object |
| `search_clinical_trials(condition, status, intervention, max_results)` | `(str, str, str, int) → list[dict]` | Calls CT.gov v2 API; returns list of parsed trial dicts |
| `format_trials_for_claude(trials)` | `(list[dict]) → str` | Renders trials as numbered `[Trial N]` blocks |

**Modules parsed from `protocolSection`:**

| Module key                      | Fields extracted                                      |
|---------------------------------|-------------------------------------------------------|
| `identificationModule`          | `nctId`, `briefTitle`                                 |
| `statusModule`                  | `overallStatus`, `startDateStruct.date`               |
| `designModule`                  | `phases`                                              |
| `descriptionModule`             | `briefSummary` (≤400 chars)                           |
| `eligibilityModule`             | `eligibilityCriteria` (see split below), `minimumAge`, `maximumAge`, `sex` |
| `sponsorCollaboratorsModule`    | `leadSponsor.name`                                    |
| `conditionsModule`              | `conditions`                                          |
| `armsInterventionsModule`       | `interventions[].name`                                |
| `contactsLocationsModule`       | `locations[]` (first 3: facility, city, country)      |
| `outcomesModule`                | `primaryOutcomes[0].measure` (≤300 chars), `secondaryOutcomes[:3].measure` (≤300 chars) |

**Eligibility criteria split (as of v0.2.0):** `eligibilityCriteria` is a single free-text blob from the API in the form:
```
Inclusion Criteria:
- criterion 1
- criterion 2

Exclusion Criteria:
- criterion 1
```
`_parse_trial` splits this on the literal string `"Exclusion Criteria:"`:
```python
criteria_parts = criteria_raw.split("Exclusion Criteria:")
inclusion_text = criteria_parts[0].replace("Inclusion Criteria:", "").strip()
inclusion_criteria = inclusion_text[:597] + "..." if len(inclusion_text) > 600 else inclusion_text
exclusion_text = criteria_parts[1].strip() if len(criteria_parts) > 1 else ""
exclusion_criteria = exclusion_text[:797] + "..." if len(exclusion_text) > 800 else exclusion_text
```
This produces two separate dict fields, `inclusion_criteria` (≤600 chars) and `exclusion_criteria` (≤800 chars, empty string if the source text has no exclusion section), replacing the single flat `eligibility_criteria` field used before v0.2.0. Truncation only appends `"..."` when the source text actually exceeds the cap — not unconditionally.

---

### 9.3 ISRCTN Pipeline

**Base URL:** `https://www.isrctn.com/api/query/format/who`

**Response format:** WHO-standard XML (parsed with `xmltodict`)

**Key internal functions:**

| Function | Signature | Purpose |
|----------|-----------|---------|
| `_is_relevant_isrctn(trial, query)` | `(dict, str) → bool` | Post-fetch relevance filter — returns `True` if any query word >3 chars appears in `public_title` or `hc_freetext` |
| `_parse_isrctn_trial(trial)` | `(dict) → dict \| None` | Extracts fields from a `trial` XML element |
| `search_isrctn(query, max_results)` | `(str, int) → list[dict]` | Calls ISRCTN API, applies relevance filter, returns parsed trials |
| `format_isrctn_for_claude(trials)` | `(list[dict]) → str` | Renders trials as numbered `[ISRCTN Trial N]` blocks |

**XML structure accessed:**

| XML path               | Maps to field              |
|------------------------|-----------------------------|
| `main.trial_id`        | `trial_id`                  |
| `main.public_title`    | `title`                     |
| `main.recruitment_status` | `status`                 |
| `main.phase`           | `phase`                     |
| `main.primary_sponsor` | `sponsor`                   |
| `main.hc_freetext`     | `condition`                 |
| `main.url`             | `url`                       |
| `primary_outcome.prim_outcome` | `primary_outcome` (≤300 chars, conditional truncation) |
| `secondary_outcome.sec_outcome` | `secondary_outcomes` (≤300 chars, conditional truncation) |
| `countries.country2`   | `countries` (list or str)   |
| `criteria.inclusion_criteria` | `inclusion_criteria` (≤600 chars, conditional truncation) |
| `criteria.exclusion_criteria` | `exclusion_criteria` (≤800 chars, conditional truncation) |
| `criteria.agemin`      | `min_age`                   |
| `criteria.agemax`      | `max_age`                   |
| `criteria.gender`      | `gender`                    |

**Two bugs fixed in v0.2.0:**

1. **Exclusion criteria was never reliably surfacing.** The pre-v0.2.0 code built a single combined `eligibility_criteria` string by conditionally appending an `"Exclusion: …"` segment onto an `"Inclusion: …"` segment. `inclusion_criteria` and `exclusion_criteria` are now read directly into their own dict fields — mirroring the CT.gov structure in §9.2 — rather than being merged into one field:
   ```python
   inclusion_raw = _get_text(criteria.get("inclusion_criteria"))
   inclusion_criteria = inclusion_raw[:597] + "..." if len(inclusion_raw) > 600 else inclusion_raw

   exclusion_raw = _get_text(criteria.get("exclusion_criteria"))
   exclusion_criteria = exclusion_raw[:797] + "..." if len(exclusion_raw) > 800 else exclusion_raw
   ```

2. **`primary_outcome`/`secondary_outcomes` always appended `"..."`, even when nothing was truncated.** The pre-v0.2.0 line was:
   ```python
   "primary_outcome": _get_text(...)[:300] + "..." if _get_text(...) else "",
   ```
   Due to operator precedence, `"..."` was concatenated onto *every* non-empty outcome — including ones far shorter than the 300-char cap — because the ternary's `if` clause only gated whether the field was empty, not whether truncation occurred. Fixed to mirror the CT.gov pattern (conditional on `len(...) > 300`), matching the fix applied to the criteria fields above.

---

## 10. Data Models

### PubMed article dict

```python
{
    "pmid":     str,   # PubMed ID
    "title":    str,   # Article title
    "authors":  list[str],  # ["Last First", …]
    "journal":  str,   # Journal name
    "year":     str,   # Publication year
    "abstract": str,   # Truncated to 500 chars (conditional)
    "url":      str,   # https://pubmed.ncbi.nlm.nih.gov/<pmid>/
}
```

### ClinicalTrials.gov trial dict

```python
{
    "nct_id":               str,        # NCT identifier
    "title":                str,
    "status":               str,        # e.g. RECRUITING, COMPLETED
    "phase":                str,        # e.g. "PHASE2; PHASE3"
    "conditions":           str,        # "; "-joined list
    "interventions":        str,        # "; "-joined intervention names
    "brief_summary":        str,        # Truncated to 400 chars (conditional)
    "inclusion_criteria":   str,        # Truncated to 600 chars (conditional) — split from eligibilityCriteria
    "exclusion_criteria":   str,        # Truncated to 800 chars (conditional) — split from eligibilityCriteria; "" if no exclusion section
    "min_age":              str,
    "max_age":              str,
    "sex":                  str,        # MALE, FEMALE, ALL
    "sponsor":               str,        # Lead sponsor name
    "start_date":           str,
    "locations":            list[str],  # First 3: "Facility, City, Country"
    "url":                  str,        # https://clinicaltrials.gov/study/<nct_id>
    "primary_outcome":      str,        # Truncated to 300 chars (conditional)
    "secondary_outcomes":   str,        # First 3, "; "-joined, truncated to 300 chars (conditional)
}
```

> `eligibility_criteria` (single flat field, ≤600 chars, unconditional `"..."`) was **removed** in v0.2.0 and replaced by the `inclusion_criteria`/`exclusion_criteria` pair above.

### ISRCTN trial dict

```python
{
    "trial_id":             str,        # ISRCTN identifier
    "title":                str,
    "status":               str,        # e.g. Ongoing, Completed
    "phase":                str,
    "sponsor":               str,        # Primary sponsor
    "condition":            str,        # hc_freetext
    "primary_outcome":      str,        # Truncated to 300 chars (conditional)
    "secondary_outcomes":   str,        # Truncated to 300 chars (conditional)
    "countries":            list[str],
    "min_age":              str,
    "max_age":              str,
    "gender":                str,
    "inclusion_criteria":   str,        # Truncated to 600 chars (conditional)
    "exclusion_criteria":   str,        # Truncated to 800 chars (conditional)
    "url":                  str,
}
```

> `eligibility_criteria` (single combined field built from conditionally-appended `"Inclusion: …"`/`"Exclusion: …"` substrings, each hard-capped at 300 chars with an unconditional `"..."`) was **removed** in v0.2.0 and replaced by the `inclusion_criteria`/`exclusion_criteria` pair above — see [§9.3](#93-isrctn-pipeline) for the bug this fixes.

---

## 11. Error Handling

Two layers of error handling now exist, in order:

1. **MCP-layer input validation (new in v0.2.0).** Each `@mcp.tool` parameter is typed as `Annotated[type, Field(...)]` (or `Literal[...]` for `status`). FastMCP/Pydantic validates arguments against these constraints *before* the tool function body runs. Invalid input (empty `query`/`condition`, `max_results` outside 1–10, an unrecognized `status` value) raises a `pydantic.ValidationError`, surfaced to the MCP client as a `ToolError`. No clamping or silent correction happens at this layer.
2. **`tools.py`-layer error handling (unchanged).** All three pipeline functions (`search_pubmed`, `search_clinical_trials`, `search_isrctn`) follow the same pattern:
   - **HTTP errors / network timeouts** → re-raised as `RuntimeError` with a descriptive message, e.g. `"PubMed search failed: <original exception>"`. The MCP tool layer propagates this to the client as a tool error.
   - **Missing / malformed fields** → individual record parsers (`_parse_article`, `_parse_trial`, `_parse_isrctn_trial`) catch `KeyError`, `TypeError`, `AttributeError` and return `None`. `None` records are filtered out silently.
   - **Empty query** → `search_pubmed` and `search_isrctn` return `[]` immediately. `search_clinical_trials` returns `[]` if `condition` is empty. (In practice, the MCP-layer `min_length=1` constraint above now rejects empty strings before they ever reach these functions when called through the MCP tool surface; this guard remains relevant for direct callers of `tools.py`.)
   - **Request timeouts:** all `requests.get` calls use `timeout=15` (esearch) or `timeout=20` (efetch).

---

## 12. Rate Limits & Constraints

| Source            | Rate Limit                        | Auth required | Max results/call |
|-------------------|-----------------------------------|---------------|-----------------|
| NCBI E-utilities  | ~3 req/sec (unauthenticated)      | No            | 10 (MCP-layer validated) |
| ClinicalTrials.gov v2 | Not publicly documented       | No            | 10 (MCP-layer validated) |
| ISRCTN            | Not publicly documented           | No            | 10 (MCP-layer validated) |

`max_results` outside `[1, 10]` is **rejected with a `ValidationError`** at the MCP tool layer (`Field(ge=1, le=10)`) — it is no longer silently clamped into range as in versions prior to 0.2.0. The underlying `tools.py` functions retain their own, wider internal clamps (`max(1, min(max_results, 100))` for PubMed, `max(1, min(max_results, 20))` for CT.gov/ISRCTN) as a defensive bound for callers that invoke `tools.py` directly, bypassing the MCP/Pydantic validation layer — those clamps were intentionally left in place.

---

## 13. Dependencies

Declared in `pyproject.toml`:

| Package     | Min version | Role                                                    |
|-------------|-------------|-----------------------------------------------------------|
| `fastmcp`   | ≥3.2.4      | MCP server framework — tool/resource/prompt decorators, transport |
| `pydantic`  | ≥2.0        | `Field`/`Literal` input validation on tool parameters (added v0.2.0; previously only a transitive dependency of `fastmcp`, now imported directly in `server.py`) |
| `requests`  | ≥2.31.0     | HTTP client for all three upstream APIs                 |
| `xmltodict` | ≥0.14.2     | XML→dict parser for PubMed and ISRCTN XML responses     |

**Removed in v0.2.0:** `httpx` — declared as a direct dependency since the initial `v0.1.0` release but never imported anywhere in the codebase (`requests` has always been the actual HTTP client). It remains installed transitively via `fastmcp`, which depends on it internally, so removing the redundant direct pin has no functional effect.

**Build system:** `hatchling`

**Python requirement:** ≥3.10 (uses `dict | None` union type syntax, `list[str]` built-in generics)

---

## 14. Registry Metadata

### `server.json` (Official MCP Registry)

```json
{
  "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
  "name": "io.github.pkotecha-eng/aria-mcp-server",
  "description": "Search 35M+ PubMed, 400K+ ClinicalTrials.gov & ISRCTN trials. Prompts included. No API key.",
  "repository": { "url": "https://github.com/pkotecha-eng/aria-mcp-server", "source": "github" },
  "version": "0.2.0",
  "packages": [{
    "registryType": "pypi",
    "identifier": "aria-mcp-server",
    "version": "0.2.0",
    "transport": { "type": "stdio" }
  }]
}
```

### `glama.json`

```json
{ "$schema": "https://glama.ai/mcp/schemas/server.json", "maintainers": ["pkotecha-eng"] }
```

---

## 15. Changelog Summary

| Version | Date       | Changes                                                                                              |
|---------|------------|--------------------------------------------------------------------------------------------------------|
| 0.2.0   | 2026-07-12 | **Breaking:** `eligibility_criteria` split into `inclusion_criteria`/`exclusion_criteria` on both trial tools; tool inputs validated via Pydantic (`max_results` 1–10, `status` enum, non-empty `query`/`condition`) — out-of-range values now rejected, not clamped. **Fixed:** `structured_content` (tools returned raw strings against a dict `output_schema`); ISRCTN exclusion criteria never reliably surfacing; ISRCTN outcome fields always appending `"..."` regardless of truncation; unmarked truncation on criteria fields. **Changed:** removed dead `max_results` clamp code in `server.py`; corrected stale docstrings; aliased `tools.py` imports at module level to fix a name-shadowing bug that forced per-call re-imports. **Removed:** unused `httpx` direct dependency. |
| 0.1.9   | 2026-05-04 | ISRCTN relevance filter relaxed from `all()` to `any()` — improves recall for synonym-heavy conditions |
| 0.1.8   | 2026-05-03 | Added `search_isrctn` tool; primary/secondary outcomes fields added to CT.gov + ISRCTN; relevance filtering |
| 0.1.7   | 2026-04-28 | Behavioral disclosure added to tool descriptions (rate limits, auth, pagination)                    |
| 0.1.6   | 2026-04-27 | `output_schema` with result descriptions for all tools                                              |
| 0.1.5   | 2026-04-26 | `Annotated` type hints with descriptions on all tool parameters                                     |
| 0.1.4   | 2026-04-25 | Improved tool docstrings (Returns + Notes sections)                                                 |
| 0.1.2   | 2026-04-23 | Initial PyPI publication; MCP Registry listing                                                      |
| 0.1.0   | 2026-04-14 | Initial release: `search_pubmed`, `search_clinical_trials`; resources; prompts                      |

---

## Appendix: Full Data Flow — `search_pubmed` Example

```
Client sends:  search_pubmed(query="ketogenic diet pediatric epilepsy", max_results=3)
                      │
                      ▼ FastMCP/Pydantic input validation
                      │  query: min_length=1 → passes
                      │  max_results: ge=1, le=10 → 3 passes (values outside 1-10 would raise
                      │  ValidationError here, before the tool body runs)
                      │
                      ▼ server.py tool handler (search_pubmed)
                      │  calls _raw_search_pubmed(query=…, max_results=3)   # aliased import, no re-import needed
                      │
                      ▼ tools.search_pubmed()
                      │  GET eutils.../esearch.fcgi?db=pubmed&term=ketogenic+diet+...&retmax=3
                      │  parse XML → IdList → ["39201234", "38901122", "38556789"]
                      │  GET eutils.../efetch.fcgi?db=pubmed&id=39201234,38901122,38556789
                      │  parse XML → PubmedArticleSet
                      │  _parse_article(×3) → [dict, dict, dict]
                      │
                      ▼ tools.format_results_for_claude([…])
                      │  renders "[Paper 1]\nTitle: …\nAuthors: …\n…"
                      │
                      ▼ server.py wraps as {"result": "<formatted string>"}
                      │  matches declared output_schema → structured_content populated correctly
                      │
                      ▼ MCP protocol response → client
```

---

*Built by Pooja Kotecha · [dinq.me/pkotecha-eng](https://dinq.me/pkotecha-eng)*
