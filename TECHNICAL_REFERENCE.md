# ARIA Clinical Research MCP Server — Technical Reference

> **Version:** 0.1.9 · **Python:** ≥3.10 · **Transport:** stdio (default) or HTTP

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
        │ Python function calls
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
- `search_clinical_trials` → active/completed trials, eligibility, recruiting studies
- `search_isrctn` → UK/European trials not on ClinicalTrials.gov
- All three together → comprehensive global clinical intelligence

---

## 6. Tools

All tools are registered with `@mcp.tool(description=…, output_schema=…)` and are **synchronous**. Each tool:

1. Clamps `max_results` to `[1, 10]`
2. Calls the corresponding function from `tools.py`
3. Formats and returns a plain-text string

---

### 6.1 `search_pubmed`

**Purpose:** Search PubMed for peer-reviewed biomedical literature.

**Signature:**
```python
def search_pubmed(
    query: Annotated[str, "Search query e.g. 'velarixin pediatric epilepsy phase 2'"],
    max_results: Annotated[int, "Number of papers to return, between 1 and 10"] = 5,
) -> str
```

**Parameters:**

| Parameter    | Type  | Required | Default | Constraints      | Description                          |
|--------------|-------|----------|---------|------------------|--------------------------------------|
| `query`      | `str` | ✅        | —       | Non-empty        | PubMed search query string           |
| `max_results`| `int` | ❌        | `5`     | Clamped to 1–10  | Number of papers to return           |

**Returns:** Formatted string (see [§10 Data Models](#10-data-models) for field list).

**Returns on no results:** `"No papers found."`

**API used:** NCBI E-utilities (`esearch.fcgi` + `efetch.fcgi`)

**Rate limit:** ~3 requests/sec (unauthenticated NCBI public API)

**Output schema:**
```json
{
  "type": "object",
  "properties": {
    "result": {
      "type": "string",
      "description": "Formatted list of papers with title, authors, journal, year, PMID, and abstract."
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
    condition: Annotated[str, "Disease or condition e.g. 'pediatric epilepsy', 'lung cancer'"],
    status: Annotated[str, "Trial status: RECRUITING, COMPLETED, or ALL"] = "RECRUITING",
    intervention: Annotated[str, "Optional drug or intervention name to narrow results"] = "",
    max_results: Annotated[int, "Number of trials to return, between 1 and 10"] = 5,
) -> str
```

**Parameters:**

| Parameter      | Type  | Required | Default       | Constraints          | Description                             |
|----------------|-------|----------|---------------|----------------------|-----------------------------------------|
| `condition`    | `str` | ✅        | —             | Non-empty            | Disease or condition name               |
| `status`       | `str` | ❌        | `RECRUITING`  | `RECRUITING`, `COMPLETED`, `ALL` | Trial status filter        |
| `intervention` | `str` | ❌        | `""`          | Optional             | Drug or intervention name               |
| `max_results`  | `int` | ❌        | `5`           | Clamped to 1–10      | Number of trials to return              |

**Returns:** Formatted string with trial details.

**Returns on no results:** `"No clinical trials found matching those criteria."`

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
      "description": "Formatted list of trials with NCT ID, title, phase, status, sponsor, conditions, interventions, and eligibility criteria."
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
    query: Annotated[str, "Condition or search terms e.g. 'pediatric epilepsy', 'type 2 diabetes'"],
    max_results: Annotated[int, "Number of trials to return, between 1 and 10"] = 5,
) -> str
```

**Parameters:**

| Parameter    | Type  | Required | Default | Constraints     | Description                         |
|--------------|-------|----------|---------|-----------------|-------------------------------------|
| `query`      | `str` | ✅        | —       | Non-empty       | Condition or free-text search terms |
| `max_results`| `int` | ❌        | `5`     | Clamped to 1–10 | Number of trials to return          |

**Returns:** Formatted string with ISRCTN trial details.

**Returns on no results:** `"No ISRCTN trials found matching those criteria."`

**API used:** ISRCTN WHO-format XML API (`https://www.isrctn.com/api/query/format/who`)

**Relevance filtering:** After the API response is fetched, results are post-filtered — any word from `query` with length >3 must appear in the trial's `public_title` or `hc_freetext` fields. This prevents off-topic results from broad keyword matches.

**Output schema:**
```json
{
  "type": "object",
  "properties": {
    "result": {
      "type": "string",
      "description": "Formatted list of trials with ISRCTN ID, title, phase, status, sponsor, condition, outcomes, countries, and eligibility criteria."
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

`tools.py` contains all HTTP clients, XML/JSON parsers, and text formatters. It is **never exposed directly to the MCP protocol** — `server.py` is the only public surface.

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

**Abstract truncation:** abstracts are truncated to 500 characters (`abstract[:497] + "..."`).

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
| `eligibilityModule`             | `eligibilityCriteria` (≤600 chars), `minimumAge`, `maximumAge`, `sex` |
| `sponsorCollaboratorsModule`    | `leadSponsor.name`                                    |
| `conditionsModule`              | `conditions`                                          |
| `armsInterventionsModule`       | `interventions[].name`                                |
| `contactsLocationsModule`       | `locations[]` (first 3: facility, city, country)      |
| `outcomesModule`                | `primaryOutcomes[0].measure` (≤300 chars), `secondaryOutcomes[:3].measure` (≤300 chars) |

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
|------------------------|----------------------------|
| `main.trial_id`        | `trial_id`                 |
| `main.public_title`    | `title`                    |
| `main.recruitment_status` | `status`              |
| `main.phase`           | `phase`                    |
| `main.primary_sponsor` | `sponsor`                  |
| `main.hc_freetext`     | `condition`                |
| `main.url`             | `url`                      |
| `primary_outcome.prim_outcome` | `primary_outcome` (≤300 chars) |
| `secondary_outcome.sec_outcome` | `secondary_outcomes` (≤300 chars) |
| `countries.country2`   | `countries` (list or str)  |
| `criteria.inclusion_criteria` | part of `eligibility_criteria` (≤300 chars) |
| `criteria.exclusion_criteria` | part of `eligibility_criteria` (≤300 chars) |
| `criteria.agemin`      | `min_age`                  |
| `criteria.agemax`      | `max_age`                  |
| `criteria.gender`      | `gender`                   |

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
    "abstract": str,   # Truncated to 500 chars
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
    "brief_summary":        str,        # Truncated to 400 chars
    "eligibility_criteria": str,        # Truncated to 600 chars
    "min_age":              str,
    "max_age":              str,
    "sex":                  str,        # MALE, FEMALE, ALL
    "sponsor":              str,        # Lead sponsor name
    "start_date":           str,
    "locations":            list[str],  # First 3: "Facility, City, Country"
    "url":                  str,        # https://clinicaltrials.gov/study/<nct_id>
    "primary_outcome":      str,        # Truncated to 300 chars
    "secondary_outcomes":   str,        # First 3, "; "-joined, truncated to 300 chars
}
```

### ISRCTN trial dict

```python
{
    "trial_id":             str,        # ISRCTN identifier
    "title":                str,
    "status":               str,        # e.g. Ongoing, Completed
    "phase":                str,
    "sponsor":              str,        # Primary sponsor
    "condition":            str,        # hc_freetext
    "primary_outcome":      str,        # Truncated to 300 chars
    "secondary_outcomes":   str,        # Truncated to 300 chars
    "countries":            list[str],
    "min_age":              str,
    "max_age":              str,
    "gender":               str,
    "eligibility_criteria": str,        # Inclusion + Exclusion, each ≤300 chars
    "url":                  str,
}
```

---

## 11. Error Handling

All three pipeline functions (`search_pubmed`, `search_clinical_trials`, `search_isrctn`) follow the same pattern:

- **HTTP errors / network timeouts** → re-raised as `RuntimeError` with a descriptive message, e.g. `"PubMed search failed: <original exception>"`. The MCP tool layer propagates this to the client as a tool error.
- **Missing / malformed fields** → individual record parsers (`_parse_article`, `_parse_trial`, `_parse_isrctn_trial`) catch `KeyError`, `TypeError`, `AttributeError` and return `None`. `None` records are filtered out silently.
- **Empty query** → `search_pubmed` and `search_isrctn` return `[]` immediately. `search_clinical_trials` returns `[]` if `condition` is empty.
- **Request timeouts:** all `requests.get` calls use `timeout=15` (esearch) or `timeout=20` (efetch).

---

## 12. Rate Limits & Constraints

| Source            | Rate Limit                        | Auth required | Max results/call |
|-------------------|-----------------------------------|---------------|-----------------|
| NCBI E-utilities  | ~3 req/sec (unauthenticated)      | No            | 10 (server-enforced) |
| ClinicalTrials.gov v2 | Not publicly documented       | No            | 10 (server-enforced) |
| ISRCTN            | Not publicly documented           | No            | 10 (server-enforced) |

The server clamps `max_results` to `[1, 10]` regardless of the value the MCP client sends. The underlying tool layer (`tools.py`) allows up to 100 (PubMed) or 20 (CT.gov, ISRCTN) to support future pagination, but the MCP layer caps this at 10.

---

## 13. Dependencies

Declared in `pyproject.toml`:

| Package     | Min version | Role                                                    |
|-------------|-------------|---------------------------------------------------------|
| `fastmcp`   | ≥3.2.4      | MCP server framework — tool/resource/prompt decorators, transport |
| `requests`  | ≥2.31.0     | HTTP client for all three upstream APIs                 |
| `xmltodict` | ≥0.14.2     | XML→dict parser for PubMed and ISRCTN XML responses     |
| `httpx`     | ≥0.28.1     | Declared dependency (used by FastMCP internally)        |

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
  "repository": { "url": "https://github.com/pkotecha-eng/aria-mcp-server" },
  "version": "0.1.9",
  "packages": [{
    "registryType": "pypi",
    "identifier": "aria-mcp-server",
    "version": "0.1.9",
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
|---------|------------|------------------------------------------------------------------------------------------------------|
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
                      ▼ server.py tool handler
                      │  clamps max_results → max(1, min(3, 10)) = 3
                      │  calls tools._search(query=…, max_results=3)
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
                      ▼ MCP protocol response → client
```

---

*Built by Pooja Kotecha · [dinq.me/pkotecha-eng](https://dinq.me/pkotecha-eng)*
