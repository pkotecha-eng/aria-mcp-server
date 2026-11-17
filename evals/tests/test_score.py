"""Unit tests for evals/score.py. Pure functions only: no network, no model calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evals.score import (
    Case,
    CaseFile,
    JudgeItem,
    JudgeVerdict,
    RequiredArg,
    ToolCall,
    UnknownAriaToolError,
    build_judge_prompt,
    extract_ids,
    ids_seen_in_calls,
    normalize_tool_name,
    parse_judge_output,
    score_gaps,
    score_ids,
    score_run,
    score_tools,
    summarize_arm,
)

CASES_PATH = Path(__file__).resolve().parent.parent / "cases.json"

PUBMED_OUT = "[Paper 1]\nTitle: T\nURL: https://pubmed.ncbi.nlm.nih.gov/11111111/"
CTGOV_OUT = "[Trial 1]\nNCT ID: NCT04368728\nStatus: COMPLETED\nURL: https://clinicaltrials.gov/study/NCT04368728"


def make_case(**overrides: object) -> Case:
    base: dict[str, object] = {
        "id": "t1", "title": "t", "category": "literature", "data_source": "live",
        "prompt": "q", "expected_tools": [], "forbidden_tools": [], "required_args": [],
        "expected_ids": [], "min_cited_ids": 0, "max_cited_ids": None,
        "insufficient_expected": False, "answer_must": [], "answer_must_not": [],
    }
    base.update(overrides)
    return Case.model_validate(base)


def call(tool: str, output: str = "", is_error: bool = False, **args: object) -> ToolCall:
    return ToolCall.model_validate({"tool": tool, "args": args, "output": output, "is_error": is_error})


# ------------------------------------------------------------ cases file

def test_shipped_cases_file_is_valid() -> None:
    cf = CaseFile.model_validate(json.loads(CASES_PATH.read_text()))
    assert len(cf.cases) == 10
    assert sum(1 for c in cf.cases if c.insufficient_expected) >= 2


def test_case_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        make_case(surprise=True)


def test_fixture_case_requires_fixture_path() -> None:
    with pytest.raises(ValidationError):
        make_case(data_source="fixture")


def test_live_case_rejects_fixture_path() -> None:
    with pytest.raises(ValidationError):
        make_case(data_source="live", fixture="fixtures/x.json")


def test_duplicate_case_ids_rejected() -> None:
    c = make_case().model_dump()
    with pytest.raises(ValidationError):
        CaseFile.model_validate({"schema_version": 1, "cases": [c, c]})


# ------------------------------------------------------------ tool names

def test_normalize_known_tool_with_prefix() -> None:
    assert normalize_tool_name("mcp__aria-clinical-research__search_pubmed") == "search_pubmed"
    assert normalize_tool_name("mcp__plugin_aria_aria-clinical-research__search_isrctn") == "search_isrctn"


def test_normalize_ignores_non_mcp_and_other_servers() -> None:
    assert normalize_tool_name("Skill") is None
    assert normalize_tool_name("mcp__other-server__search_pubmed2") is None


def test_normalize_unknown_aria_tool_fails_closed() -> None:
    with pytest.raises(UnknownAriaToolError):
        normalize_tool_name("mcp__aria-clinical-research__search_pubmeds")


# ------------------------------------------------------------ identifiers

def test_extract_ids_all_forms() -> None:
    text = "See PMID 123, https://pubmed.ncbi.nlm.nih.gov/456/, NCT04368728 and ISRCTN16753384."
    assert extract_ids(text) == {"PMID:123", "PMID:456", "NCT04368728", "ISRCTN16753384"}


def test_extract_ids_catches_malformed_ids() -> None:
    assert extract_ids("NCT123") == {"NCT123"}


def test_ids_seen_ignores_errored_calls() -> None:
    calls = [call("search_pubmed", PUBMED_OUT), call("search_clinical_trials", CTGOV_OUT, is_error=True)]
    assert ids_seen_in_calls(calls) == {"PMID:11111111"}


def test_invented_id_fails_closed() -> None:
    case = make_case()
    s = score_ids(case, "Study PMID 99999999 shows benefit.", [call("search_pubmed", PUBMED_OUT)])
    assert s.invented == ["PMID:99999999"]
    assert not s.passed


def test_id_from_tool_output_passes() -> None:
    case = make_case(min_cited_ids=1)
    s = score_ids(case, "Trial NCT04368728 is completed.", [call("search_clinical_trials", CTGOV_OUT)])
    assert s.passed and not s.invented


def test_id_from_errored_call_counts_as_invented() -> None:
    case = make_case()
    s = score_ids(case, "NCT04368728", [call("search_clinical_trials", CTGOV_OUT, is_error=True)])
    assert s.invented == ["NCT04368728"]


def test_expected_id_must_be_cited() -> None:
    case = make_case(expected_ids=["NCT04368728"], min_cited_ids=1)
    s = score_ids(case, "I could not find it.", [call("search_clinical_trials", CTGOV_OUT)])
    assert s.missing_expected == ["NCT04368728"] and not s.passed


def test_max_cited_zero_blocks_any_id() -> None:
    case = make_case(max_cited_ids=0)
    s = score_ids(case, "NCT04368728", [call("search_clinical_trials", CTGOV_OUT)])
    assert not s.count_ok and not s.passed


def test_max_cited_null_allows_real_context_ids() -> None:
    case = make_case(max_cited_ids=None)
    s = score_ids(case, "Context: NCT04368728.", [call("search_clinical_trials", CTGOV_OUT)])
    assert s.passed


def test_min_cited_enforced() -> None:
    case = make_case(min_cited_ids=2)
    s = score_ids(case, "NCT04368728", [call("search_clinical_trials", CTGOV_OUT)])
    assert not s.count_ok


# ------------------------------------------------------------ tool choice

def test_missing_expected_tool_fails() -> None:
    case = make_case(expected_tools=["search_pubmed", "search_clinical_trials"])
    s = score_tools(case, [call("search_pubmed")])
    assert s.missing_expected == ["search_clinical_trials"] and not s.passed


def test_forbidden_tool_fails() -> None:
    case = make_case(forbidden_tools=["search_isrctn"])
    assert not score_tools(case, [call("search_isrctn")]).passed


def test_required_arg_default_is_applied() -> None:
    req = RequiredArg(tool="search_clinical_trials", arg="status", value="RECRUITING")
    case = make_case(required_args=[req])
    assert score_tools(case, [call("search_clinical_trials")]).passed  # default status is RECRUITING


def test_required_arg_missing_fails() -> None:
    req = RequiredArg(tool="search_clinical_trials", arg="status", value="ALL")
    case = make_case(required_args=[req])
    s = score_tools(case, [call("search_clinical_trials")])
    assert s.arg_failures and not s.passed


def test_required_arg_any_matching_call_suffices() -> None:
    req = RequiredArg(tool="search_clinical_trials", arg="status", value="ALL")
    case = make_case(required_args=[req])
    calls = [call("search_clinical_trials", status="RECRUITING"), call("search_clinical_trials", status="ALL")]
    assert score_tools(case, calls).passed


# ------------------------------------------------------------ judge and gaps

def verdict(*items: tuple[str, str, bool]) -> JudgeVerdict:
    return JudgeVerdict.model_validate(
        {"items": [{"kind": k, "criterion": c, "holds": h, "rationale": "r"} for k, c, h in items]}
    )


def test_gaps_pass_when_all_criteria_ruled_correctly() -> None:
    case = make_case(answer_must=["says none found"], answer_must_not=["invents a trial"])
    v = verdict(("must", "says none found", True), ("must_not", "invents a trial", False))
    assert score_gaps(case, "Insufficient data.", v).passed


def test_gaps_fail_on_unmet_must() -> None:
    case = make_case(answer_must=["says none found"])
    s = score_gaps(case, "x", verdict(("must", "says none found", False)))
    assert s.failed == ["says none found"] and not s.passed


def test_gaps_fail_on_violated_must_not() -> None:
    case = make_case(answer_must_not=["invents a trial"])
    s = score_gaps(case, "x", verdict(("must_not", "invents a trial", True)))
    assert not s.passed


def test_unruled_criterion_fails_closed() -> None:
    case = make_case(answer_must=["a", "b"])
    s = score_gaps(case, "x", verdict(("must", "a", True)))
    assert s.unjudged == ["b"] and not s.passed


def test_missing_verdict_fails_when_criteria_exist() -> None:
    case = make_case(answer_must=["a"])
    assert not score_gaps(case, "x", None).passed


def test_no_criteria_passes_without_verdict() -> None:
    assert score_gaps(make_case(), "x", None).passed


def test_unexpected_judge_criterion_raises() -> None:
    case = make_case(answer_must=["a"])
    with pytest.raises(ValueError):
        score_gaps(case, "x", verdict(("must", "a", True), ("must", "extra", True)))


def test_marker_disagreement_is_informational_only() -> None:
    case = make_case(insufficient_expected=True, answer_must=["a"])
    s = score_gaps(case, "Everything is fine.", verdict(("must", "a", True)))
    assert s.passed and s.marker_disagrees


def test_judge_item_requires_rationale() -> None:
    with pytest.raises(ValidationError):
        JudgeItem(kind="must", criterion="a", holds=True, rationale="")


def test_parse_judge_output_strips_code_fence() -> None:
    raw = '```json\n{"items":[{"criterion":"a","kind":"must","holds":true,"rationale":"ok"}]}\n```'
    assert parse_judge_output(raw).items[0].holds is True


def test_parse_judge_output_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_judge_output("no json here")


def test_judge_prompt_is_blind_to_arm() -> None:
    case = make_case(answer_must=["a"])
    prompt = build_judge_prompt(case, "answer", [call("search_pubmed", PUBMED_OUT)])
    for leaked in ("mcp_only", "mcp_plus_skill", "skill"):
        assert leaked not in prompt.lower()


# ------------------------------------------------------------ run and arm summaries

def test_score_run_requires_all_three_dimensions() -> None:
    case = make_case(expected_tools=["search_pubmed"])
    good = score_run(case, "mcp_only", [call("search_pubmed", PUBMED_OUT)], "ok", None)
    assert good.passed and good.n_calls == 1
    bad = score_run(case, "mcp_only", [call("search_pubmed", PUBMED_OUT)], "PMID 99999999", None)
    assert not bad.passed


def test_summarize_arm_counts_invented_ids_and_calls() -> None:
    case = make_case()
    r1 = score_run(case, "mcp_only", [call("search_pubmed", PUBMED_OUT)], "PMID 99999999", None)
    r2 = score_run(case, "mcp_only", [call("search_pubmed", PUBMED_OUT)] * 3, "fine", None)
    r3 = score_run(case, "mcp_plus_skill", [], "fine", None)
    s = summarize_arm("mcp_only", [r1, r2, r3])
    assert s.runs == 2 and s.invented_id_runs == 1 and s.mean_calls == 2.0 and s.pass_rate == 0.5


def test_summarize_empty_arm() -> None:
    assert summarize_arm("mcp_only", []).runs == 0
