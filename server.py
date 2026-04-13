"""
ARIA Clinical Research MCP Server

Exposes two tools to any MCP-compatible client:
  - search_pubmed: Search 35M+ biomedical papers via NCBI E-utilities
  - search_clinical_trials: Search 400K+ trials via ClinicalTrials.gov v2 API

Usage:
  python server.py                    # stdio (Claude Desktop)
  python server.py --transport http   # HTTP (remote clients)
"""

from fastmcp import FastMCP
from tools import search_pubmed, search_clinical_trials, format_results_for_claude, format_trials_for_claude

mcp = FastMCP(
    name="aria-clinical-research",
    instructions=(
        "ARIA provides real-time access to biomedical literature and clinical trials. "
        "Use search_pubmed for published research, mechanisms, drug studies, and outcomes. "
        "Use search_clinical_trials for active or completed trials, eligibility criteria, and recruiting studies. "
        "Use both together for comprehensive clinical intelligence on any condition or intervention."
    ),
)


@mcp.tool
def search_pubmed(
    query: str,
    max_results: int = 5,
) -> str:
    """
    Search PubMed for peer-reviewed biomedical literature.

    Use for: research papers, drug mechanisms, clinical outcomes, disease studies,
    safety/efficacy data, biomarkers, diagnostics, and any scientific question.

    Args:
        query: Search query (e.g. "velarixin pediatric epilepsy phase 2")
        max_results: Number of papers to return (1-10, default 5)
    """
    from tools import search_pubmed as _search, format_results_for_claude as _fmt
    max_results = max(1, min(max_results, 10))
    papers = _search(query=query, max_results=max_results)
    return _fmt(papers)


@mcp.tool
def search_clinical_trials(
    condition: str,
    status: str = "RECRUITING",
    intervention: str = "",
    max_results: int = 5,
) -> str:
    """
    Search ClinicalTrials.gov for clinical studies.

    Use for: active trials, recruiting studies, trial eligibility criteria,
    phase information, sponsor details, and trial locations.

    Args:
        condition: Disease or condition (e.g. "pediatric epilepsy", "lung cancer")
        status: Trial status — RECRUITING, COMPLETED, or ALL (default: RECRUITING)
        intervention: Optional drug or intervention name to narrow results
        max_results: Number of trials to return (1-10, default 5)
    """
    from tools import search_clinical_trials as _search, format_trials_for_claude as _fmt
    max_results = max(1, min(max_results, 10))
    trials = _search(
        condition=condition,
        status=status,
        intervention=intervention,
        max_results=max_results,
    )
    return _fmt(trials)


@mcp.resource("info://aria")
def aria_info() -> str:
    """Overview of ARIA's capabilities and data sources."""
    return """
ARIA Clinical Research MCP Server
==================================
Tools:
  - search_pubmed: 35M+ papers from NCBI PubMed (no API key required)
  - search_clinical_trials: 400K+ trials from ClinicalTrials.gov v2 (no API key required)

Best used for:
  - Life sciences researchers needing real-time literature access
  - Clinical trial coordinators checking eligibility and recruiting status
  - Drug discovery teams cross-referencing published evidence with active trials
  - Any Claude agent needing grounded biomedical knowledge

Data sources: NCBI E-utilities, ClinicalTrials.gov API v2
Built by: Pooja Kotecha (pkotecha-eng)
Portfolio: dinq.me/pkotecha-eng
"""


@mcp.prompt("clinical-research-brief")
def clinical_research_brief(condition: str) -> str:
    """Generate a structured clinical research brief for a given condition."""
    return (
        f"You are a clinical research assistant. Using your available tools, "
        f"search for both published literature AND active clinical trials for: {condition}. "
        f"Then synthesize a structured brief covering: "
        f"(1) current evidence base from PubMed, "
        f"(2) active recruiting trials with eligibility criteria, "
        f"(3) key gaps between published evidence and active research. "
        f"Ground every claim in the retrieved data."
    )


if __name__ == "__main__":
    import sys
    transport = "http" if "--transport" in sys.argv and "http" in sys.argv else "stdio"
    if transport == "http":
        mcp.run(transport="http", port=8000)
    else:
        mcp.run(transport="stdio")