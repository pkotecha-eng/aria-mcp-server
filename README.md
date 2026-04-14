# 🧬 ARIA Clinical Research MCP Server
A standalone MCP server that gives any Claude agent real-time access to biomedical literature and clinical trials — no API keys required.
[![aria-mcp-server MCP server](https://glama.ai/mcp/servers/pkotecha-eng/aria-mcp-server/badges/score.svg)](https://glama.ai/mcp/servers/pkotecha-eng/aria-mcp-server)
## Tools

| Tool | Description |
|------|-------------|
| `search_pubmed` | Search 35M+ peer-reviewed papers via NCBI E-utilities |
| `search_clinical_trials` | Search 400K+ trials via ClinicalTrials.gov v2 API |


## Prompts

| Prompt | Description |
|--------|-------------|
| `clinical-research-brief` | Comprehensive evidence + trial brief for a condition |
| `adverse-event-analysis` | Structured AE and safety profile for a drug |
| `trial-eligibility-checker` | Patient eligibility assessment across recruiting trials |

## Resources

| Resource | Description |
|----------|-------------|
| `info://aria` | Overview of ARIA capabilities and data sources |
| `reference://trial-phases` | Phase I–IV definitions and FDA special designations |
| `reference://high-impact-journals` | Curated high-impact journals by specialty |
| `reference://fda-databases` | FDA databases for drug, safety, and regulatory research |

## Example queries

Once connected, ask Claude:

- "Search PubMed for ketogenic diet pediatric epilepsy outcomes"
- "Find recruiting trials for lung cancer immunotherapy"
- "What does the literature say about veliparib in ovarian cancer, and are there active trials?"

## Quickstart

```bash
git clone https://github.com/pkotecha-eng/aria-mcp-server
cd aria-mcp-server
pip install -r requirements.txt
python3 server.py
```

## Connect to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aria-clinical-research": {
      "command": "python3",
      "args": ["/absolute/path/to/aria-mcp-server/server.py"]
    }
  }
}
```

Restart Claude Desktop. The server will appear under Settings → Developer → Local MCP Servers.

## Data sources

- **PubMed** — NCBI E-utilities (public API, no key required)
- **ClinicalTrials.gov** — v2 REST API (public API, no key required)

## Tech stack

- [FastMCP](https://gofastmcp.com) — MCP server framework
- Python 3.12+

## Use cases

- Life sciences researchers needing real-time literature access
- Clinical trial coordinators checking eligibility and recruiting status
- Drug discovery teams cross-referencing published evidence with active trials
- Any Claude agent needing grounded biomedical knowledge

## Built by

Pooja Kotecha · [dinq.me/pkotecha-eng](https://dinq.me/pkotecha-eng)
