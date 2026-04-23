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
from aria_mcp_server.tools import search_pubmed, search_clinical_trials, format_results_for_claude, format_trials_for_claude

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
    from aria_mcp_server.tools import search_pubmed as _search, format_results_for_claude as _fmt
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
    from aria_mcp_server.tools import search_clinical_trials as _search, format_trials_for_claude as _fmt
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

@mcp.resource("reference://trial-phases")
def trial_phases() -> str:
    """Clinical trial phase definitions and characteristics."""
    return """
CLINICAL TRIAL PHASES — Reference Guide
========================================

PHASE I
  Purpose: Safety, dosing, side effects
  Participants: 20–100 healthy volunteers or patients
  Duration: Several months
  Key questions: Is it safe? What is the right dose?

PHASE II
  Purpose: Efficacy and side effects
  Participants: 100–300 patients with the condition
  Duration: Several months to 2 years
  Key questions: Does it work? What are the side effects?

PHASE III
  Purpose: Efficacy vs. standard treatment, adverse reactions
  Participants: 1,000–3,000 patients
  Duration: 1–4 years
  Key questions: Is it better than existing treatments?
  Note: Required for FDA approval

PHASE IV (Post-marketing)
  Purpose: Long-term safety and effectiveness
  Participants: Thousands of patients in real-world settings
  Duration: Ongoing
  Key questions: What are the long-term effects?

SPECIAL DESIGNATIONS
  - Breakthrough Therapy: Serious condition, preliminary evidence of substantial improvement
  - Fast Track: Serious condition, unmet medical need
  - Accelerated Approval: Surrogate endpoint used
  - Priority Review: Significant improvement over available therapy
"""


@mcp.resource("reference://high-impact-journals")
def high_impact_journals() -> str:
    """Curated list of high-impact biomedical and clinical research journals."""
    return """
HIGH-IMPACT CLINICAL RESEARCH JOURNALS
=======================================

GENERAL MEDICINE
  - New England Journal of Medicine (NEJM) — IF ~100
  - The Lancet — IF ~170
  - JAMA (Journal of the American Medical Association) — IF ~120
  - BMJ (British Medical Journal) — IF ~93

ONCOLOGY
  - Journal of Clinical Oncology (JCO) — IF ~45
  - Nature Cancer — IF ~23
  - Cancer Cell — IF ~38
  - Clinical Cancer Research — IF ~13

CARDIOLOGY
  - Circulation — IF ~37
  - Journal of the American College of Cardiology (JACC) — IF ~24
  - European Heart Journal — IF ~35

NEUROLOGY
  - Brain — IF ~15
  - Neurology — IF ~12
  - JAMA Neurology — IF ~29

RARE DISEASE & PEDIATRICS
  - Orphanet Journal of Rare Diseases — IF ~4
  - Pediatrics — IF ~8
  - JAMA Pediatrics — IF ~26

CLINICAL TRIALS METHODOLOGY
  - Clinical Trials — IF ~4
  - Trials — IF ~3
  - Contemporary Clinical Trials — IF ~3

Note: Impact factors approximate and updated annually.
"""


@mcp.resource("reference://fda-databases")
def fda_databases() -> str:
    """FDA databases and resources relevant to clinical research."""
    return """
FDA DATABASES & RESOURCES FOR CLINICAL RESEARCH
================================================

DRUG INFORMATION
  - Drugs@FDA: https://www.accessdata.fda.gov/scripts/cder/daf/
    Approved drug products, labels, and approval history
    
  - FDA Drug Shortages: https://www.accessdata.fda.gov/scripts/drugshortages/
    Current and resolved drug shortage information

CLINICAL TRIALS & SAFETY
  - FDA Adverse Event Reporting System (FAERS):
    https://www.fda.gov/drugs/questions-science-research-drug-approvals/fda-adverse-event-reporting-system-faers
    Post-market safety surveillance data
    
  - MedWatch: https://www.fda.gov/safety/medwatch-fda-safety-information-and-adverse-event-reporting-program
    Safety alerts and adverse event reporting

REGULATORY GUIDANCE
  - FDA Guidance Documents: https://www.fda.gov/regulatory-information/search-fda-guidance-documents
    ICH guidelines, GCP standards, trial design guidance
    
  - Orphan Drug Designations: https://www.accessdata.fda.gov/scripts/opdlisting/oopd/
    Rare disease drug designations and approvals

DEVICE & BIOLOGICS
  - 510(k) Database: https://www.accessdata.fda.gov/scripts/cdrh/cfdocs/cfpmn/pmn.cfm
    Medical device clearances
    
  - BLA (Biologics License Applications): https://www.fda.gov/vaccines-blood-biologics/development-approval-process-cber
    Biologic product approvals

BIOMARKERS & ENDPOINTS
  - FDA Biomarker Qualification Program:
    https://www.fda.gov/drugs/drug-development-tool-qualification-programs/biomarker-qualification-program
"""

@mcp.prompt("clinical-research-brief")
def clinical_research_brief(condition: str) -> str:
    """Generate a comprehensive clinical research brief for a given condition."""
    return (
        f"You are a senior clinical research analyst. Using your available tools, "
        f"search for both published literature AND active clinical trials for: {condition}. "
        f"Synthesize a structured brief with these sections:\n\n"
        f"1. EVIDENCE BASE: Key findings from PubMed — efficacy, safety, mechanisms\n"
        f"2. ACTIVE TRIALS: Recruiting trials with NCT IDs, phase, eligibility criteria, and locations\n"
        f"3. EVIDENCE GAPS: What the literature doesn't yet answer that trials are investigating\n"
        f"4. CLINICAL IMPLICATIONS: What this means for trial coordinators or researchers\n\n"
        f"Ground every claim in retrieved data. Flag any limitations in the available evidence."
    )


@mcp.prompt("adverse-event-analysis")
def adverse_event_analysis(drug: str, condition: str) -> str:
    """Analyze adverse event profile for a drug in a specific condition."""
    return (
        f"You are a pharmacovigilance specialist. Using PubMed, search for adverse event "
        f"and safety data for {drug} in patients with {condition}.\n\n"
        f"Structure your analysis as:\n"
        f"1. COMMON ADVERSE EVENTS: Frequency and severity (Grade 1-4 where reported)\n"
        f"2. SERIOUS/RARE EVENTS: Black box warnings, SAEs, discontinuation rates\n"
        f"3. POPULATION-SPECIFIC RISKS: Pediatric, elderly, renal/hepatic impairment\n"
        f"4. MONITORING RECOMMENDATIONS: What to watch for in clinical practice\n"
        f"5. REGULATORY STATUS: Any FDA safety communications or label updates\n\n"
        f"Cite the specific papers supporting each finding."
    )


@mcp.prompt("trial-eligibility-checker")
def trial_eligibility_checker(condition: str, patient_profile: str) -> str:
    """Find and assess clinical trial eligibility for a patient profile."""
    return (
        f"You are a clinical trial coordinator. Search ClinicalTrials.gov for recruiting trials "
        f"for {condition}.\n\n"
        f"Patient profile: {patient_profile}\n\n"
        f"For each trial found:\n"
        f"1. STATE the NCT ID, title, phase, and sponsor\n"
        f"2. LIST key inclusion criteria and whether this patient likely meets them\n"
        f"3. LIST key exclusion criteria and flag any potential disqualifiers\n"
        f"4. PROVIDE the trial location(s) and contact information if available\n"
        f"5. RATE eligibility: Likely Eligible / Possibly Eligible / Likely Ineligible\n\n"
        f"Always remind the user that final eligibility must be confirmed with the trial site."
    )

def run():
    import sys
    transport = "http" if "--transport" in sys.argv and "http" in sys.argv else "stdio"
    if transport == "http":
        mcp.run(transport="http", port=8000)
    else:
        mcp.run(transport="stdio")

if __name__ == "__main__":
    run()