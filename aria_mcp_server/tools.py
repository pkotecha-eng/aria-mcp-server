"""
Clinical research data tools for ARIA MCP Server.
No API keys required — uses public NCBI and ClinicalTrials.gov APIs.
"""

import logging
from collections.abc import Callable
from typing import Any, Generic, TypeVar

import requests
import xmltodict
from pydantic import BaseModel, ConfigDict, Field, ValidationError

logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------
# Validated record models
#
# Upstream payloads (xmltodict output, CT.gov JSON) are untyped and their schemas are
# not ours, so they are not modelled field-for-field. Instead each upstream record is
# validated into one of these strict models, which are what the rest of the code uses.
# Only the identifier is required; every other field defaults to "" so the formatters
# keep rendering a missing field as "N/A", exactly as before.
# -------------------------------------------------------------------------


class RecordParseError(ValueError):
    """One upstream record could not be turned into a valid record model."""


class UpstreamParseError(RuntimeError):
    """An upstream response had records, but none of them could be parsed."""


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Paper(_Record):
    pmid: str = Field(min_length=1)
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    journal: str = ""
    year: str = ""
    abstract: str = ""
    url: str = ""


class Trial(_Record):
    """One ClinicalTrials.gov record."""

    nct_id: str = Field(min_length=1)
    title: str = ""
    status: str = ""
    phase: str = ""
    conditions: str = ""
    interventions: str = ""
    brief_summary: str = ""
    inclusion_criteria: str = ""
    exclusion_criteria: str = ""
    min_age: str = ""
    max_age: str = ""
    sex: str = ""
    sponsor: str = ""
    start_date: str = ""
    locations: list[str] = Field(default_factory=list)
    url: str = ""
    primary_outcome: str = ""
    secondary_outcomes: str = ""


class IsrctnTrial(_Record):
    """One ISRCTN registry record."""

    trial_id: str = Field(min_length=1)
    title: str = ""
    status: str = ""
    phase: str = ""
    sponsor: str = ""
    condition: str = ""
    primary_outcome: str = ""
    secondary_outcomes: str = ""
    countries: list[str] = Field(default_factory=list)
    min_age: str = ""
    max_age: str = ""
    gender: str = ""
    inclusion_criteria: str = ""
    exclusion_criteria: str = ""
    url: str = ""


R = TypeVar("R", bound=_Record)


class SearchResult(BaseModel, Generic[R]):
    """Parsed records plus how many upstream records could not be parsed and were omitted."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    records: list[R]
    dropped: int = Field(default=0, ge=0)


def _build(model: type[R], source: str, **fields: Any) -> R:
    try:
        return model(**fields)
    except ValidationError as e:
        raise RecordParseError(f"{source} record failed validation: {e}") from e


def _parse_all(
    source: str,
    raw_items: list[Any],
    parse: Callable[[Any], R],
    expected: int | None = None,
) -> SearchResult[R]:
    """Parse every raw record. A record that fails is logged and counted, never silently lost.

    `expected` is how many records the upstream said it was returning, when that is known
    separately from `raw_items` (PubMed: esearch IDs vs. efetch articles). If there were
    records to parse and none survived, raise, so a parse failure cannot read as "no results".
    """
    records: list[R] = []
    for raw in raw_items:
        try:
            records.append(parse(raw))
        except RecordParseError as e:
            logger.warning("%s: omitting record: %s", source, e)
    total = expected if expected is not None else len(raw_items)
    dropped = total - len(records)
    if dropped and not records:
        raise UpstreamParseError(f"{source}: all {total} returned record(s) failed to parse")
    return SearchResult(records=records, dropped=dropped)


def _omitted_note(source: str, dropped: int) -> str:
    if not dropped:
        return ""
    return f"Note: {dropped} {source} record(s) could not be parsed and were omitted; this list may be incomplete."


def _with_note(body: str, source: str, dropped: int) -> str:
    note = _omitted_note(source, dropped)
    return f"{body}\n\n{note}" if note else body


# -------------------------------------------------------------------------
# PubMed (NCBI E-utilities)
# -------------------------------------------------------------------------

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _get_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, dict) and "#text" in node:
        return (node["#text"] or "").strip()
    return str(node).strip() if node else ""


def _extract_authors(author_list: Any) -> list[str]:
    if not author_list:
        return []
    authors = author_list.get("Author") if isinstance(author_list, dict) else None
    if not authors:
        return []
    if isinstance(authors, dict):
        authors = [authors]
    names = []
    for a in authors:
        if not isinstance(a, dict):
            continue
        last = _get_text(a.get("LastName"))
        fore = _get_text(a.get("ForeName") or a.get("Initials", ""))
        if last or fore:
            names.append(f"{last} {fore}".strip())
    return names


def _parse_article(article_xml: dict) -> Paper:
    try:
        medline = article_xml.get("MedlineCitation") or article_xml
        pmid_el = medline.get("PMID")
        pmid = _get_text(pmid_el) if isinstance(pmid_el, dict) else str(pmid_el or "").strip()
        article = (medline.get("Article") or {}) if isinstance(medline, dict) else {}
        if isinstance(article, str):
            article = {}
        title = _get_text(article.get("ArticleTitle"))
        author_list = article.get("AuthorList") or {}
        authors = _extract_authors(author_list)
        journal = ""
        year = ""
        journal_el = article.get("Journal")
        if isinstance(journal_el, dict):
            journal = _get_text(journal_el.get("Title"))
            issue = journal_el.get("JournalIssue") or {}
            if isinstance(issue, dict):
                pub_date = issue.get("PubDate") or {}
                if isinstance(pub_date, dict):
                    year = _get_text(pub_date.get("Year"))
        abstract_el = article.get("Abstract")
        abstract = ""
        if isinstance(abstract_el, dict):
            abstract_parts = abstract_el.get("AbstractText")
            if isinstance(abstract_parts, list):
                abstract = " ".join(_get_text(p.get("#text") if isinstance(p, dict) else p) for p in abstract_parts)
            elif isinstance(abstract_parts, dict):
                abstract = _get_text(abstract_parts.get("#text") or abstract_parts)
            else:
                abstract = _get_text(abstract_parts)
        else:
            abstract = _get_text(abstract_el)
        if len(abstract) > 500:
            abstract = abstract[:497] + "..."
    except (KeyError, TypeError, AttributeError) as e:
        raise RecordParseError(f"PubMed record could not be read ({type(e).__name__}: {e})") from e
    return _build(
        Paper,
        "PubMed",
        pmid=pmid,
        title=title,
        authors=authors,
        journal=journal,
        year=year,
        abstract=abstract,
        url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    )


def search_pubmed(query: str, max_results: int = 5) -> SearchResult[Paper]:
    """Search PubMed via NCBI E-utilities. No API key required."""
    if not query or not query.strip():
        return SearchResult[Paper](records=[])
    query = query.strip()
    max_results = max(1, min(max_results, 100))
    try:
        r = requests.get(
            f"{PUBMED_BASE}/esearch.fcgi",
            params={"db": "pubmed", "term": query, "retmax": max_results, "retmode": "xml"},
            timeout=15,
        )
        r.raise_for_status()
        data = xmltodict.parse(r.content)
    except Exception as e:
        raise RuntimeError(f"PubMed search failed: {e}") from e

    id_list = (data.get("eSearchResult") or {}).get("IdList") or {}
    id_el = id_list.get("Id") if isinstance(id_list, dict) else None
    if not id_el:
        return SearchResult[Paper](records=[])
    pmids = [id_el] if isinstance(id_el, str) else list(id_el)[:max_results]
    if not pmids:
        return SearchResult[Paper](records=[])

    try:
        r2 = requests.get(
            f"{PUBMED_BASE}/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(pmids), "rettype": "xml"},
            timeout=20,
        )
        r2.raise_for_status()
        fetch_data = xmltodict.parse(r2.content)
    except Exception as e:
        raise RuntimeError(f"PubMed fetch failed: {e}") from e

    root = fetch_data.get("PubmedArticleSet") or fetch_data
    articles = root.get("PubmedArticle") or root.get("PubmedData") or []
    if isinstance(articles, dict):
        articles = [articles]
    # esearch said len(pmids) articles exist; any that efetch did not return, or that fail
    # to parse, are counted as dropped rather than vanishing.
    return _parse_all("PubMed", articles, _parse_article, expected=len(pmids))


def format_results_for_claude(result: SearchResult[Paper]) -> str:
    """Format PubMed results as readable text."""
    if not result.records:
        return "No papers found."
    lines = []
    for i, paper in enumerate(result.records, 1):
        authors_str = "; ".join(paper.authors)
        journal_year = paper.journal.strip()
        year = paper.year.strip()
        if journal_year and year:
            journal_year = f"{journal_year} ({year})"
        elif year:
            journal_year = year
        lines.append("\n".join([
            f"[Paper {i}]",
            f"Title: {paper.title or 'N/A'}",
            f"Authors: {authors_str or 'N/A'}",
            f"Journal/Year: {journal_year or 'N/A'}",
            f"Abstract: {paper.abstract or 'N/A'}",
            f"URL: {paper.url or 'N/A'}",
            "",
        ]))
    return _with_note("\n".join(lines).strip(), "PubMed", result.dropped)


# -------------------------------------------------------------------------
# ClinicalTrials.gov v2 API
# -------------------------------------------------------------------------

CT_BASE = "https://clinicaltrials.gov/api/v2/studies"


def _ct_get(d: Any, *keys, default: str = "") -> str:
    for key in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(key)
    if d is None:
        return default
    if isinstance(d, list):
        return "; ".join(str(i) for i in d if i)
    return str(d).strip()


def _parse_trial(study: dict) -> Trial:
    try:
        proto = study.get("protocolSection") or {}
        id_mod = proto.get("identificationModule") or {}
        status_mod = proto.get("statusModule") or {}
        design_mod = proto.get("designModule") or {}
        desc_mod = proto.get("descriptionModule") or {}
        elig_mod = proto.get("eligibilityModule") or {}
        sponsor_mod = proto.get("sponsorCollaboratorsModule") or {}
        conditions_mod = proto.get("conditionsModule") or {}
        interventions_mod = proto.get("armsInterventionsModule") or {}
        contacts_mod = proto.get("contactsLocationsModule") or {}
        outcomes_mod = proto.get("outcomesModule") or {}

        primary_outcomes = outcomes_mod.get("primaryOutcomes") or []
        primary_outcome = primary_outcomes[0].get("measure", "") if primary_outcomes else ""
        if len(primary_outcome) > 300:
          primary_outcome = primary_outcome[:297] + "..."

        secondary_outcomes_list = outcomes_mod.get("secondaryOutcomes") or []
        secondary_outcome_strs = [
          o.get("measure", "") for o in secondary_outcomes_list[:3] if isinstance(o, dict)
        ]
        secondary_outcome = "; ".join(secondary_outcome_strs)
        if len(secondary_outcome) > 300:
          secondary_outcome = secondary_outcome[:297] + "..."

        nct_id = _ct_get(id_mod, "nctId")

        interventions = interventions_mod.get("interventions") or []
        intervention_names = "; ".join(
            _ct_get(i, "name") for i in interventions if isinstance(i, dict)
        )
        locations = contacts_mod.get("locations") or []
        location_strs = []
        for loc in locations[:3]:
            if not isinstance(loc, dict):
                continue
            parts = [_ct_get(loc, "facility"), _ct_get(loc, "city"), _ct_get(loc, "country")]
            loc_str = ", ".join(p for p in parts if p)
            if loc_str:
                location_strs.append(loc_str)

        criteria_raw = _ct_get(elig_mod, "eligibilityCriteria")
        criteria_parts = criteria_raw.split("Exclusion Criteria:")
        inclusion_text = criteria_parts[0].replace("Inclusion Criteria:", "").strip()
        inclusion_criteria = inclusion_text[:597] + "..." if len(inclusion_text) > 600 else inclusion_text
        exclusion_text = criteria_parts[1].strip() if len(criteria_parts) > 1 else ""
        exclusion_criteria = exclusion_text[:797] + "..." if len(exclusion_text) > 800 else exclusion_text

        summary = _ct_get(desc_mod, "briefSummary")
        if len(summary) > 400:
            summary = summary[:397] + "..."

        fields = {
            "nct_id": nct_id,
            "title": _ct_get(id_mod, "briefTitle"),
            "status": _ct_get(status_mod, "overallStatus"),
            "phase": "; ".join(design_mod.get("phases") or []),
            "conditions": "; ".join(conditions_mod.get("conditions") or []),
            "interventions": intervention_names,
            "brief_summary": summary,
            "inclusion_criteria": inclusion_criteria,
            "exclusion_criteria": exclusion_criteria,
            "min_age": _ct_get(elig_mod, "minimumAge"),
            "max_age": _ct_get(elig_mod, "maximumAge"),
            "sex": _ct_get(elig_mod, "sex"),
            "sponsor": _ct_get(sponsor_mod, "leadSponsor", "name"),
            "start_date": _ct_get(status_mod, "startDateStruct", "date"),
            "locations": location_strs,
            "url": f"https://clinicaltrials.gov/study/{nct_id}",
            "primary_outcome": primary_outcome,
            "secondary_outcomes": secondary_outcome,
        }
    except (KeyError, TypeError, AttributeError) as e:
        raise RecordParseError(f"ClinicalTrials.gov record could not be read ({type(e).__name__}: {e})") from e
    return _build(Trial, "ClinicalTrials.gov", **fields)


def search_clinical_trials(
    condition: str,
    status: str = "RECRUITING",
    intervention: str = "",
    max_results: int = 5,
) -> SearchResult[Trial]:
    """Search ClinicalTrials.gov v2 API. No API key required."""
    condition = (condition or "").strip()
    if not condition:
        return SearchResult[Trial](records=[])
    max_results = max(1, min(max_results, 20))
    params: dict[str, Any] = {
        "format": "json",
        "query.cond": condition,
        "pageSize": max_results,
        "countTotal": "true",
    }
    if intervention:
        params["query.intr"] = intervention.strip()
    if status and status.upper() != "ALL":
        params["filter.overallStatus"] = status.upper()

    try:
        r = requests.get(CT_BASE, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"ClinicalTrials.gov search failed: {e}") from e

    studies = data.get("studies") or []
    return _parse_all("ClinicalTrials.gov", studies, _parse_trial)


def format_trials_for_claude(result: SearchResult[Trial]) -> str:
    """Format ClinicalTrials.gov results as readable text."""
    if not result.records:
        return "No clinical trials found matching those criteria."
    lines = []
    for i, t in enumerate(result.records, 1):
        locations_str = "; ".join(t.locations) or "N/A"
        lines.append("\n".join([
            f"[Trial {i}]",
            f"NCT ID: {t.nct_id or 'N/A'}",
            f"Title: {t.title or 'N/A'}",
            f"Status: {t.status or 'N/A'}",
            f"Phase: {t.phase or 'N/A'}",
            f"Condition(s): {t.conditions or 'N/A'}",
            f"Intervention(s): {t.interventions or 'N/A'}",
            f"Sponsor: {t.sponsor or 'N/A'}",
            f"Age Range: {t.min_age or 'N/A'} – {t.max_age or 'N/A'}",
            f"Sex: {t.sex or 'N/A'}",
            f"Locations: {locations_str}",
            f"Summary: {t.brief_summary or 'N/A'}",
            f"Primary Outcome: {t.primary_outcome or 'N/A'}",
            f"Secondary Outcomes: {t.secondary_outcomes or 'N/A'}",
            f"Inclusion Criteria: {t.inclusion_criteria or 'N/A'}",
            f"Exclusion Criteria: {t.exclusion_criteria or 'N/A'}",
            f"URL: {t.url or 'N/A'}",
            "",
        ]))
    return _with_note("\n".join(lines).strip(), "ClinicalTrials.gov", result.dropped)


# -------------------------------------------------------------------------
# ISRCTN Registry (UK/European clinical trials)
# -------------------------------------------------------------------------

ISRCTN_BASE = "https://www.isrctn.com/api/query/format/who"

def _is_relevant_isrctn(trial: dict, query: str) -> bool:
    """Check if query terms appear in title or condition — not full document."""
    query_words = set(query.lower().split())
    main = trial.get("main") or {}
    searchable = " ".join([
        _get_text(main.get("public_title")),
        _get_text(main.get("hc_freetext")),
    ]).lower()
    significant_words = [w for w in query_words if len(w) > 3]
    if not significant_words:
      return True
    return any(word in searchable for word in significant_words)

def _parse_isrctn_trial(trial: dict) -> IsrctnTrial:
    try:
        main = trial.get("main") or {}
        criteria = trial.get("criteria") or {}
        countries_el = trial.get("countries") or {}

        trial_id = _get_text(main.get("trial_id"))

        # Handle countries — could be string or list
        country_raw = countries_el.get("country2")
        if isinstance(country_raw, list):
            countries = [c for c in country_raw if c]
        elif country_raw:
            countries = [country_raw]
        else:
            countries = []

        inclusion_raw = _get_text(criteria.get("inclusion_criteria"))
        inclusion_criteria = inclusion_raw[:597] + "..." if len(inclusion_raw) > 600 else inclusion_raw

        exclusion_raw = _get_text(criteria.get("exclusion_criteria"))
        exclusion_criteria = exclusion_raw[:797] + "..." if len(exclusion_raw) > 800 else exclusion_raw

        # An empty <primary_outcome/> parses to None, not {}, so `.get(key, {})` would
        # return None and the next `.get` would raise; `or {}` covers both.
        primary_outcome_raw = _get_text((trial.get("primary_outcome") or {}).get("prim_outcome"))
        primary_outcome = primary_outcome_raw[:297] + "..." if len(primary_outcome_raw) > 300 else primary_outcome_raw

        secondary_outcome_raw = _get_text((trial.get("secondary_outcome") or {}).get("sec_outcome"))
        secondary_outcome = secondary_outcome_raw[:297] + "..." if len(secondary_outcome_raw) > 300 else secondary_outcome_raw

        fields = {
            "trial_id": trial_id,
            "title": _get_text(main.get("public_title")),
            "status": _get_text(main.get("recruitment_status")),
            "phase": _get_text(main.get("phase")),
            "sponsor": _get_text(main.get("primary_sponsor")),
            "condition": _get_text(main.get("hc_freetext")),
            "primary_outcome": primary_outcome,
            "secondary_outcomes": secondary_outcome,
            "countries": countries,
            "min_age": _get_text(criteria.get("agemin")),
            "max_age": _get_text(criteria.get("agemax")),
            "gender": _get_text(criteria.get("gender")),
            "inclusion_criteria": inclusion_criteria,
            "exclusion_criteria": exclusion_criteria,
            "url": _get_text(main.get("url")),
        }
    except (KeyError, TypeError, AttributeError) as e:
        raise RecordParseError(f"ISRCTN record could not be read ({type(e).__name__}: {e})") from e
    return _build(IsrctnTrial, "ISRCTN", **fields)


def search_isrctn(query: str, max_results: int = 5) -> SearchResult[IsrctnTrial]:
    """Search ISRCTN registry for UK/European clinical trials. No API key required."""
    query = (query or "").strip()
    if not query:
        return SearchResult[IsrctnTrial](records=[])
    max_results = max(1, min(max_results, 20))
    try:
        r = requests.get(
            ISRCTN_BASE,
            params={"q": query, "limit": max_results},
            timeout=15,
        )
        r.raise_for_status()
        data = xmltodict.parse(r.content)
    except Exception as e:
        raise RuntimeError(f"ISRCTN search failed: {e}") from e

    trials_el = (data.get("trials") or {}).get("trial")
    if not trials_el:
        return SearchResult[IsrctnTrial](records=[])
    if isinstance(trials_el, dict):
        trials_el = [trials_el]

    relevant = [tr for tr in trials_el if _is_relevant_isrctn(tr, query)]
    return _parse_all("ISRCTN", relevant, _parse_isrctn_trial)


def format_isrctn_for_claude(result: SearchResult[IsrctnTrial]) -> str:
    """Format ISRCTN results as readable text."""
    if not result.records:
        return "No ISRCTN trials found matching those criteria."
    lines = []
    for i, t in enumerate(result.records, 1):
        countries_str = "; ".join(t.countries) or "N/A"
        lines.append("\n".join([
            f"[ISRCTN Trial {i}]",
            f"ISRCTN ID: {t.trial_id or 'N/A'}",
            f"Title: {t.title or 'N/A'}",
            f"Status: {t.status or 'N/A'}",
            f"Phase: {t.phase or 'N/A'}",
            f"Condition: {t.condition or 'N/A'}",
            f"Primary Outcome: {t.primary_outcome or 'N/A'}",
            f"Secondary Outcomes: {t.secondary_outcomes or 'N/A'}",
            f"Sponsor: {t.sponsor or 'N/A'}",
            f"Countries: {countries_str}",
            f"Age Range: {t.min_age or 'N/A'} – {t.max_age or 'N/A'}",
            f"Gender: {t.gender or 'N/A'}",
            f"Inclusion Criteria: {t.inclusion_criteria or 'N/A'}",
            f"Exclusion Criteria: {t.exclusion_criteria or 'N/A'}",
            f"URL: {t.url or 'N/A'}",
            "",
        ]))
    return _with_note("\n".join(lines).strip(), "ISRCTN", result.dropped)

__all__ = [
    "search_pubmed", "format_results_for_claude",
    "search_clinical_trials", "format_trials_for_claude",
    "search_isrctn", "format_isrctn_for_claude",
    "Paper", "Trial", "IsrctnTrial", "SearchResult",
    "RecordParseError", "UpstreamParseError",
]