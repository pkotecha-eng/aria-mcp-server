"""Stub ARIA MCP server for fixture eval cases (evals/cases.json, data_source "fixture").

It is the real server (same tool names, descriptions, schemas, validation and output
formatting) with the three data-fetching functions replaced, so no call reaches the network:

  - search_clinical_trials: returns the fixture's records for ANY arguments, so results do not
    depend on how the agent phrased the query.
  - search_pubmed, search_isrctn: return no records, so the real formatters produce the real
    empty-result strings ("No papers found." / "No ISRCTN trials found matching those criteria.").

Each call is also appended to the JSONL file named by ARIA_STUB_CALL_LOG (optional) as an
independent cross-check. The scored record is the transcript the harness captures from
`claude -p`, which records tool name and arguments for stub and live servers alike.

Environment:
  ARIA_STUB_FIXTURE   required, path to a fixture JSON (see evals/fixtures/)
  ARIA_STUB_CALL_LOG  optional, path of a JSONL file to append one line per tool call
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

import aria_mcp_server.server as real_server

PATCHED_NAMES: tuple[str, ...] = (
    "_raw_search_pubmed",
    "_raw_search_clinical_trials",
    "_raw_search_isrctn",
)


class StubConfigError(RuntimeError):
    """The stub cannot start safely (bad fixture, or the real server changed shape)."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TrialRecord(_Strict):
    """One ClinicalTrials.gov record, with the keys format_trials_for_claude reads."""

    nct_id: str
    title: str
    status: str
    phase: str
    conditions: str
    interventions: str
    brief_summary: str
    inclusion_criteria: str
    exclusion_criteria: str
    min_age: str
    max_age: str
    sex: str
    sponsor: str
    start_date: str
    locations: list[str]
    url: str
    primary_outcome: str
    secondary_outcomes: str


class Fixture(_Strict):
    description: str
    search_clinical_trials: list[TrialRecord] = Field(min_length=1)


def load_fixture(path: Path) -> Fixture:
    try:
        return Fixture.model_validate(json.loads(path.read_text()))
    except (OSError, ValueError) as exc:
        raise StubConfigError(f"cannot load fixture {path}: {exc}") from exc


def log_call(log_path: Path | None, tool: str, args: dict[str, object]) -> None:
    if log_path is None:
        return
    with log_path.open("a") as fh:
        fh.write(json.dumps({"tool": tool, "args": args}) + "\n")


def patch_server(fixture: Fixture, log_path: Path | None) -> None:
    """Replace the real server's three fetch functions. Fails closed if they are missing."""
    missing = [n for n in PATCHED_NAMES if not hasattr(real_server, n)]
    if missing:
        raise StubConfigError(f"aria_mcp_server.server no longer defines {missing}; update the stub")
    records = [r.model_dump() for r in fixture.search_clinical_trials]

    # Keyword-only on purpose: the real wrappers call these by keyword, and a positional
    # call (the server changed) raises TypeError instead of being silently accepted.
    def fake_trials(**kwargs: object) -> list[dict[str, object]]:
        log_call(log_path, "search_clinical_trials", kwargs)
        return [dict(r) for r in records]

    def fake_pubmed(**kwargs: object) -> list[dict[str, object]]:
        log_call(log_path, "search_pubmed", kwargs)
        return []

    def fake_isrctn(**kwargs: object) -> list[dict[str, object]]:
        log_call(log_path, "search_isrctn", kwargs)
        return []

    real_server._raw_search_clinical_trials = fake_trials  # type: ignore[attr-defined]
    real_server._raw_search_pubmed = fake_pubmed  # type: ignore[attr-defined]
    real_server._raw_search_isrctn = fake_isrctn  # type: ignore[attr-defined]


def main() -> None:
    fixture_path = os.environ.get("ARIA_STUB_FIXTURE")
    if not fixture_path:
        raise StubConfigError("ARIA_STUB_FIXTURE is not set")
    log_env = os.environ.get("ARIA_STUB_CALL_LOG")
    patch_server(load_fixture(Path(fixture_path)), Path(log_env) if log_env else None)
    real_server.mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
