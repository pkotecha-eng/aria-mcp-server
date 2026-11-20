<!-- mcp-name: io.github.pkotecha-eng/aria-mcp-server -->

# 🧬 ARIA Clinical Research MCP Server
A standalone MCP server that gives any Claude agent real-time access to biomedical literature and clinical trials — no API keys required.
[![aria-mcp-server MCP server](https://glama.ai/mcp/servers/pkotecha-eng/aria-mcp-server/badges/score.svg)](https://glama.ai/mcp/servers/pkotecha-eng/aria-mcp-server)
## Tools

| Tool | Description |
|------|-------------|
| `search_pubmed` | Search 35M+ peer-reviewed papers via NCBI E-utilities |
| `search_clinical_trials` | Search 400K+ trials via ClinicalTrials.gov v2 API |
| `search_isrctn` | Search UK/European trials via ISRCTN registry |


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
- "Find UK and European trials for pediatric epilepsy via ISRCTN"

## Quickstart

```bash
pip install aria-mcp-server
aria-mcp-server
```

## Install as a Claude Code plugin

The plugin bundles the MCP server with the `aria-clinical-research` skill, which teaches the agent when and how to use the tools. In Claude Code:

```
/plugin install aria --marketplace pkotecha-eng/aria-mcp-server
```

Claude Code first asks **Add marketplace?** (y/n), then a scope picker:

- **Install for you (user scope)**
- **Install for all collaborators on this repository (project scope)**
- **Install for you, in this repo only (local scope)**

To check the install:

- `/plugin` shows **ARIA Clinical Research @ pkotecha-eng** with its scope, version, `Skills: aria-clinical-research` and `MCP servers: aria-clinical-research`. When a newer version is published, the same details screen offers **Update now**.
- `/mcp` shows `plugin:aria:aria-clinical-research` as connected with 3 tools.

The plugin starts the server with `uvx --from "aria-mcp-server>=0.2.2" aria-mcp-server`, so [uv](https://docs.astral.sh/uv/) must be on your `PATH`. Without `uv`, use the standalone server from the Quickstart instead (the plugin's skill is not installed that way).

Run the plugin **or** a standalone `aria-mcp-server` entry in your MCP config, not both, or the tools appear twice.

### Updating

Plugin updates are published as new plugin versions. To update, open `/plugin` → **Installed** → **ARIA Clinical Research** → **Update now**, then run `/reload-plugins`. No restart is needed. The details screen then shows the new version.

Tested on Claude Code 2.1.326. If `/plugin` is missing or behaves differently, run `which claude` and `claude --version` to make sure an older copy isn't shadowing the current one.

## Connect to Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "aria-clinical-research": {
      "command": "aria-mcp-server"
    }
  }
}
```

Restart Claude Desktop. The server will appear under Settings → Developer → Local MCP Servers.

## Data sources

- **PubMed** — NCBI E-utilities (public API, no key required)
- **ClinicalTrials.gov** — v2 REST API (public API, no key required)
- **ISRCTN** — UK/European clinical trials registry (public API, no key required)

## Tech stack

- [FastMCP](https://gofastmcp.com) — MCP server framework
- Python 3.10+

## Use cases

- Life sciences researchers needing real-time literature access
- Clinical trial coordinators checking eligibility and recruiting status
- Drug discovery teams cross-referencing published evidence with active trials
- Any Claude agent needing grounded biomedical knowledge


## Project Structure

```
aria-mcp-server/          ← root, where mcp-publisher runs
├── aria_mcp_server/      ← Python package
│   ├── server.py
│   └── tools.py
├── .claude-plugin/       ← Claude Code plugin + marketplace manifests
├── skills/               ← aria-clinical-research agent skill
├── evals/                ← eval harness
├── server.json           ← MCP registry metadata
├── pyproject.toml
├── README.md
├── LICENSE
└── glama.json
```

## Built by

Pooja Kotecha · [dinq.me/pkotecha-eng](https://dinq.me/pkotecha-eng)
