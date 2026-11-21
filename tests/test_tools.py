"""Tests for the record models and the no-silent-drops behaviour of aria_mcp_server.tools.

No network: requests.get is replaced with canned upstream payloads.
"""

from __future__ import annotations

from typing import Any

import pytest
import xmltodict
from pydantic import ValidationError

import aria_mcp_server.tools as tools
from aria_mcp_server.tools import (
    Paper,
    RecordParseError,
    SearchResult,
    Trial,
    UpstreamParseError,
    format_isrctn_for_claude,
    format_results_for_claude,
    format_trials_for_claude,
)


class _Resp:
    def __init__(self, content: bytes = b"", payload: dict[str, Any] | None = None) -> None:
        self.content = content
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _patch_get(monkeypatch: pytest.MonkeyPatch, routes: dict[str, _Resp]) -> None:
    def fake_get(url: str, params: dict[str, Any] | None = None, timeout: int | None = None) -> _Resp:
        for fragment, resp in routes.items():
            if fragment in url:
                return resp
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(tools.requests, "get", fake_get)


def _esearch(*pmids: str) -> _Resp:
    ids = "".join(f"<Id>{p}</Id>" for p in pmids)
    return _Resp(content=f"<eSearchResult><IdList>{ids}</IdList></eSearchResult>".encode())


def _efetch(*pmids: str) -> _Resp:
    arts = "".join(
        f"<PubmedArticle><MedlineCitation><PMID>{p}</PMID>"
        f"<Article><ArticleTitle>Title {p}</ArticleTitle></Article></MedlineCitation></PubmedArticle>"
        for p in pmids
    )
    return _Resp(content=f"<PubmedArticleSet>{arts}</PubmedArticleSet>".encode())


def _study(nct: str) -> dict[str, Any]:
    return {"protocolSection": {"identificationModule": {"nctId": nct, "briefTitle": f"Trial {nct}"}}}


# ------------------------------------------------------------ models

def test_models_require_an_id_and_forbid_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        Paper(pmid="")
    with pytest.raises(ValidationError):
        Trial(nct_id="NCT1", surprise="x")  # type: ignore[call-arg]


def test_record_missing_its_id_is_a_parse_error() -> None:
    with pytest.raises(RecordParseError):
        tools._parse_article({"MedlineCitation": {"Article": {}}})
    with pytest.raises(RecordParseError):
        tools._parse_trial({"protocolSection": {}})


def test_isrctn_empty_outcome_element_keeps_the_record() -> None:
    # <primary_outcome/> parses to None; the old code raised AttributeError and dropped the trial.
    trial = xmltodict.parse(
        "<trials><trial><main><trial_id>ISRCTN1</trial_id></main><primary_outcome/></trial></trials>"
    )["trials"]["trial"]
    parsed = tools._parse_isrctn_trial(trial)
    assert parsed.trial_id == "ISRCTN1" and parsed.primary_outcome == ""


# ------------------------------------------------------------ nothing vanishes silently

def test_pubmed_article_efetch_did_not_return_is_counted_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, {"esearch": _esearch("1", "2", "3"), "efetch": _efetch("1", "2")})
    result = tools.search_pubmed("q", max_results=3)
    assert [p.pmid for p in result.records] == ["1", "2"] and result.dropped == 1
    assert "Note: 1 PubMed record(s) could not be parsed and were omitted" in format_results_for_claude(result)


def test_pubmed_ids_but_no_articles_raises_instead_of_reporting_no_results(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, {"esearch": _esearch("1", "2"), "efetch": _Resp(content=b"<PubmedArticleSet/>")})
    with pytest.raises(UpstreamParseError):
        tools.search_pubmed("q")


def test_ctgov_malformed_study_is_counted_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    studies = [_study("NCT1"), {"protocolSection": {}}, {"protocolSection": {
        "identificationModule": {"nctId": "NCT3"},
        "outcomesModule": {"primaryOutcomes": ["not a dict"]},
    }}]
    _patch_get(monkeypatch, {"clinicaltrials.gov": _Resp(payload={"studies": studies})})
    result = tools.search_clinical_trials("c")
    assert [t.nct_id for t in result.records] == ["NCT1"] and result.dropped == 2
    assert "Note: 2 ClinicalTrials.gov record(s) could not be parsed" in format_trials_for_claude(result)


def test_ctgov_every_study_malformed_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_get(monkeypatch, {"clinicaltrials.gov": _Resp(payload={"studies": [{"protocolSection": {}}]})})
    with pytest.raises(UpstreamParseError):
        tools.search_clinical_trials("c")


def test_isrctn_note_is_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    xml = (
        "<trials>"
        "<trial><main><trial_id>ISRCTN1</trial_id><public_title>asthma</public_title></main></trial>"
        "<trial><main><public_title>asthma</public_title></main></trial>"
        "</trials>"
    )
    _patch_get(monkeypatch, {"isrctn.com": _Resp(content=xml.encode())})
    result = tools.search_isrctn("asthma")
    assert [t.trial_id for t in result.records] == ["ISRCTN1"] and result.dropped == 1
    assert "Note: 1 ISRCTN record(s)" in format_isrctn_for_claude(result)


def test_no_note_and_clean_empty_message_when_nothing_dropped() -> None:
    ok = SearchResult[Trial](records=[Trial(nct_id="NCT1")])
    assert "Note:" not in format_trials_for_claude(ok)
    assert format_trials_for_claude(SearchResult[Trial](records=[])) == (
        "No clinical trials found matching those criteria."
    )
