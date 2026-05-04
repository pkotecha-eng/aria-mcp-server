"""
Clinical research data tools for ARIA MCP Server.
No API keys required — uses public NCBI and ClinicalTrials.gov APIs.
"""

import requests
import xmltodict
from typing import Any


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


def _parse_article(article_xml: dict) -> dict | None:
    try:
        medline = article_xml.get("MedlineCitation") or article_xml
        if not medline:
            return None
        pmid_el = medline.get("PMID")
        pmid = _get_text(pmid_el) if isinstance(pmid_el, dict) else str(pmid_el or "").strip()
        if not pmid:
            return None
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
        return {
            "pmid": pmid,
            "title": title,
            "authors": authors,
            "journal": journal,
            "year": year,
            "abstract": abstract,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        }
    except (KeyError, TypeError, AttributeError):
        return None


def search_pubmed(query: str, max_results: int = 5) -> list[dict]:
    """Search PubMed via NCBI E-utilities. No API key required."""
    if not query or not query.strip():
        return []
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
        return []
    pmids = [id_el] if isinstance(id_el, str) else list(id_el)[:max_results]
    if not pmids:
        return []

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
    articles = root.get("PubmedArticle") or root.get("PubmedData")
    if not articles:
        return []
    if isinstance(articles, dict):
        articles = [articles]
    return [p for p in (_parse_article(a) for a in articles) if p]


def format_results_for_claude(results: list[dict]) -> str:
    """Format PubMed results as readable text."""
    if not results:
        return "No papers found."
    lines = []
    for i, paper in enumerate(results, 1):
        authors_str = "; ".join(paper.get("authors") or [])
        journal_year = paper.get("journal", "").strip()
        year = paper.get("year", "").strip()
        if journal_year and year:
            journal_year = f"{journal_year} ({year})"
        elif year:
            journal_year = year
        lines.append("\n".join([
            f"[Paper {i}]",
            f"Title: {paper.get('title') or 'N/A'}",
            f"Authors: {authors_str or 'N/A'}",
            f"Journal/Year: {journal_year or 'N/A'}",
            f"Abstract: {paper.get('abstract') or 'N/A'}",
            f"URL: {paper.get('url') or 'N/A'}",
            "",
        ]))
    return "\n".join(lines).strip()


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


def _parse_trial(study: dict) -> dict | None:
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
        if not nct_id:
            return None

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

        criteria = _ct_get(elig_mod, "eligibilityCriteria")
        if len(criteria) > 600:
            criteria = criteria[:597] + "..."

        summary = _ct_get(desc_mod, "briefSummary")
        if len(summary) > 400:
            summary = summary[:397] + "..."

        return {
            "nct_id": nct_id,
            "title": _ct_get(id_mod, "briefTitle"),
            "status": _ct_get(status_mod, "overallStatus"),
            "phase": "; ".join(design_mod.get("phases") or []),
            "conditions": "; ".join(conditions_mod.get("conditions") or []),
            "interventions": intervention_names,
            "brief_summary": summary,
            "eligibility_criteria": criteria,
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
    except (KeyError, TypeError, AttributeError):
        return None


def search_clinical_trials(
    condition: str,
    status: str = "RECRUITING",
    intervention: str = "",
    max_results: int = 5,
) -> list[dict]:
    """Search ClinicalTrials.gov v2 API. No API key required."""
    condition = (condition or "").strip()
    if not condition:
        return []
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
    return [t for t in (_parse_trial(s) for s in studies) if t]


def format_trials_for_claude(trials: list[dict]) -> str:
    """Format ClinicalTrials.gov results as readable text."""
    if not trials:
        return "No clinical trials found matching those criteria."
    lines = []
    for i, t in enumerate(trials, 1):
        locations_str = "; ".join(t.get("locations") or []) or "N/A"
        lines.append("\n".join([
            f"[Trial {i}]",
            f"NCT ID: {t.get('nct_id') or 'N/A'}",
            f"Title: {t.get('title') or 'N/A'}",
            f"Status: {t.get('status') or 'N/A'}",
            f"Phase: {t.get('phase') or 'N/A'}",
            f"Condition(s): {t.get('conditions') or 'N/A'}",
            f"Intervention(s): {t.get('interventions') or 'N/A'}",
            f"Sponsor: {t.get('sponsor') or 'N/A'}",
            f"Age Range: {t.get('min_age') or 'N/A'} – {t.get('max_age') or 'N/A'}",
            f"Sex: {t.get('sex') or 'N/A'}",
            f"Locations: {locations_str}",
            f"Summary: {t.get('brief_summary') or 'N/A'}",
            f"Primary Outcome: {t.get('primary_outcome') or 'N/A'}",
            f"Secondary Outcomes: {t.get('secondary_outcomes') or 'N/A'}",
            f"Eligibility: {t.get('eligibility_criteria') or 'N/A'}",
            f"URL: {t.get('url') or 'N/A'}",
            "",
        ]))
    return "\n".join(lines).strip()


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

def _parse_isrctn_trial(trial: dict) -> dict | None:
    try:
        main = trial.get("main") or {}
        criteria = trial.get("criteria") or {}
        countries_el = trial.get("countries") or {}

        trial_id = _get_text(main.get("trial_id"))
        if not trial_id:
            return None

        # Handle countries — could be string or list
        country_raw = countries_el.get("country2")
        if isinstance(country_raw, list):
            countries = [c for c in country_raw if c]
        elif country_raw:
            countries = [country_raw]
        else:
            countries = []

        inclusion = _get_text(criteria.get("inclusion_criteria"))
        exclusion = _get_text(criteria.get("exclusion_criteria"))
        eligibility = ""
        if inclusion:
            eligibility += f"Inclusion: {inclusion[:300]}..."
        if exclusion:
            eligibility += f"\nExclusion: {exclusion[:300]}..."

        return {
            "trial_id": trial_id,
            "title": _get_text(main.get("public_title")),
            "status": _get_text(main.get("recruitment_status")),
            "phase": _get_text(main.get("phase")),
            "sponsor": _get_text(main.get("primary_sponsor")),
            "condition": _get_text(main.get("hc_freetext")),
            "primary_outcome": _get_text(trial.get("primary_outcome", {}).get("prim_outcome"))[:300] + "..." if _get_text(trial.get("primary_outcome", {}).get("prim_outcome")) else "",
            "secondary_outcomes": _get_text(trial.get("secondary_outcome", {}).get("sec_outcome"))[:300] + "..." if _get_text(trial.get("secondary_outcome", {}).get("sec_outcome")) else "",
            "countries": countries,
            "min_age": _get_text(criteria.get("agemin")),
            "max_age": _get_text(criteria.get("agemax")),
            "gender": _get_text(criteria.get("gender")),
            "eligibility_criteria": eligibility,
            "url": _get_text(main.get("url")),
        }
    except (KeyError, TypeError, AttributeError):
        return None


def search_isrctn(query: str, max_results: int = 5) -> list[dict]:
    """Search ISRCTN registry for UK/European clinical trials. No API key required."""
    query = (query or "").strip()
    if not query:
        return []
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
        return []
    if isinstance(trials_el, dict):
        trials_el = [trials_el]

    relevant = [tr for tr in trials_el if _is_relevant_isrctn(tr, query)]
    return [t for t in (_parse_isrctn_trial(tr) for tr in relevant) if t]


def format_isrctn_for_claude(trials: list[dict]) -> str:
    """Format ISRCTN results as readable text."""
    if not trials:
        return "No ISRCTN trials found matching those criteria."
    lines = []
    for i, t in enumerate(trials, 1):
        countries_str = "; ".join(t.get("countries") or []) or "N/A"
        lines.append("\n".join([
            f"[ISRCTN Trial {i}]",
            f"ISRCTN ID: {t.get('trial_id') or 'N/A'}",
            f"Title: {t.get('title') or 'N/A'}",
            f"Status: {t.get('status') or 'N/A'}",
            f"Phase: {t.get('phase') or 'N/A'}",
            f"Condition: {t.get('condition') or 'N/A'}",
            f"Primary Outcome: {t.get('primary_outcome') or 'N/A'}",
            f"Secondary Outcomes: {t.get('secondary_outcomes') or 'N/A'}",
            f"Sponsor: {t.get('sponsor') or 'N/A'}",
            f"Countries: {countries_str}",
            f"Age Range: {t.get('min_age') or 'N/A'} – {t.get('max_age') or 'N/A'}",
            f"Gender: {t.get('gender') or 'N/A'}",
            f"Eligibility: {t.get('eligibility_criteria') or 'N/A'}",
            f"URL: {t.get('url') or 'N/A'}",
            "",
        ]))
    return "\n".join(lines).strip()

__all__ = [
    "search_pubmed", "format_results_for_claude",
    "search_clinical_trials", "format_trials_for_claude",
    "search_isrctn", "format_isrctn_for_claude",
]