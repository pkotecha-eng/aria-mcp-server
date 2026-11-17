"""Tests for evals/stub_server.py and the eval fixtures. No network, no subprocesses."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastmcp import Client
from pydantic import ValidationError

import aria_mcp_server.server as real_server
from aria_mcp_server.tools import format_trials_for_claude
from evals.stub_server import (
    PATCHED_NAMES,
    Fixture,
    StubConfigError,
    load_fixture,
    main,
    patch_server,
)

EVALS_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = EVALS_DIR / "fixtures"
C05 = FIXTURES_DIR / "c05_ctgov_truncated_exclusion.json"
C06 = FIXTURES_DIR / "c06_ctgov_exclusion_na.json"


@pytest.fixture
def patched(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Patch the real server with the c05 fixture; monkeypatch restores the originals."""
    for name in PATCHED_NAMES:
        monkeypatch.setattr(real_server, name, getattr(real_server, name))
    log = tmp_path / "calls.jsonl"
    patch_server(load_fixture(C05), log)
    return log


def result_text(res: object) -> str:
    structured = getattr(res, "structured_content", None)
    if isinstance(structured, dict) and "result" in structured:
        return str(structured["result"])
    return "".join(getattr(block, "text", "") for block in getattr(res, "content", []))


# ------------------------------------------------------------ fixtures

@pytest.mark.parametrize("path", sorted(FIXTURES_DIR.glob("*.json")), ids=lambda p: p.name)
def test_every_fixture_is_valid(path: Path) -> None:
    assert load_fixture(path).search_clinical_trials


def test_fixture_files_exist_and_match_cases() -> None:
    cases = json.loads((EVALS_DIR / "cases.json").read_text())["cases"]
    referenced = {c["fixture"] for c in cases if c["data_source"] == "fixture"}
    on_disk = {f"fixtures/{p.name}" for p in FIXTURES_DIR.glob("*.json")}
    assert referenced == on_disk  # no missing fixtures, no orphans


def test_c05_exclusion_is_cut_at_cap_without_the_decisive_text() -> None:
    text = load_fixture(C05).search_clinical_trials[0].exclusion_criteria
    assert len(text) == 800 and text.endswith("concomitant us...")
    assert "anticoagulant" not in text.lower()


def test_c06_first_record_renders_exclusions_as_na() -> None:
    records = load_fixture(C06).search_clinical_trials
    rendered = format_trials_for_claude([r.model_dump() for r in records])
    assert "Exclusion Criteria: N/A" in rendered
    assert "Exclusion Criteria: Age above 80 years" in rendered


def test_fixture_rejects_unknown_field_and_empty_list() -> None:
    data = json.loads(C05.read_text())
    with pytest.raises(ValidationError):
        Fixture.model_validate({**data, "surprise": 1})
    with pytest.raises(ValidationError):
        Fixture.model_validate({**data, "search_clinical_trials": []})
    bad = json.loads(C05.read_text())
    del bad["search_clinical_trials"][0]["sponsor"]
    with pytest.raises(ValidationError):
        Fixture.model_validate(bad)


# ------------------------------------------------------------ stub behaviour

def test_load_fixture_missing_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(StubConfigError):
        load_fixture(tmp_path / "nope.json")


def test_main_requires_fixture_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ARIA_STUB_FIXTURE", raising=False)
    with pytest.raises(StubConfigError):
        main()


def test_patch_fails_closed_if_server_changes_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(real_server, "_raw_search_isrctn")
    with pytest.raises(StubConfigError):
        patch_server(load_fixture(C05), None)


def test_trials_ignore_arguments_and_other_tools_return_nothing(patched: Path) -> None:
    a = real_server._raw_search_clinical_trials(condition="x", status="ALL", intervention="", max_results=1)
    b = real_server._raw_search_clinical_trials(condition="totally different")
    assert a == b and a[0]["nct_id"] == "NCT99999901"
    assert real_server._raw_search_pubmed(query="q", max_results=5) == []
    assert real_server._raw_search_isrctn(query="q", max_results=5) == []


def test_positional_call_is_rejected(patched: Path) -> None:
    with pytest.raises(TypeError):
        real_server._raw_search_clinical_trials("x")


def test_every_call_is_logged(patched: Path) -> None:
    real_server._raw_search_clinical_trials(condition="x", status="ALL")
    real_server._raw_search_pubmed(query="y")
    lines = [json.loads(line) for line in patched.read_text().splitlines()]
    assert [entry["tool"] for entry in lines] == ["search_clinical_trials", "search_pubmed"]
    assert lines[0]["args"] == {"condition": "x", "status": "ALL"}


def test_round_trip_through_real_server_keeps_truncated_text_intact(patched: Path) -> None:
    expected = load_fixture(C05).search_clinical_trials[0].exclusion_criteria

    async def run() -> tuple[str, str, str]:
        async with Client(real_server.mcp) as client:
            ct = result_text(await client.call_tool("search_clinical_trials", {"condition": "anything"}))
            pm = result_text(await client.call_tool("search_pubmed", {"query": "x"}))
            ir = result_text(await client.call_tool("search_isrctn", {"query": "x"}))
            return ct, pm, ir

    ct, pm, ir = asyncio.run(run())
    assert f"Exclusion Criteria: {expected}" in ct
    assert pm == "No papers found."
    assert ir == "No ISRCTN trials found matching those criteria."
