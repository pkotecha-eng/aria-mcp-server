"""Pure scoring functions for the ARIA skill evals.

No I/O, no network, no model calls. The harness (run_evals.py) parses transcripts into
the models below and passes them in; everything here is deterministic and unit-tested.
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ToolName = Literal["search_pubmed", "search_clinical_trials", "search_isrctn"]
TOOL_NAMES: tuple[ToolName, ...] = ("search_pubmed", "search_clinical_trials", "search_isrctn")
Arm = Literal["mcp_only", "mcp_plus_skill"]
Category = Literal[
    "literature", "registry_us_global", "registry_uk", "multi_step", "truncation",
    "missing_field", "insufficient", "cross_reference", "result_limit",
]
ArgValue = str | int | float | bool | None

# Server-side defaults, so a call that omits an argument is scored as the default it used.
DEFAULT_ARGS: dict[tuple[str, str], ArgValue] = {
    ("search_pubmed", "max_results"): 5,
    ("search_clinical_trials", "status"): "RECRUITING",
    ("search_clinical_trials", "max_results"): 5,
    ("search_isrctn", "max_results"): 5,
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UnknownAriaToolError(ValueError):
    """An ARIA-looking MCP tool name that is not one of the three known tools."""


# ---------------------------------------------------------------- case definitions

class RequiredArg(_Strict):
    tool: ToolName
    arg: str
    value: ArgValue


class Case(_Strict):
    id: str
    title: str
    category: Category
    data_source: Literal["live", "fixture"]
    fixture: str | None = None
    prompt: str
    expected_tools: list[ToolName]
    forbidden_tools: list[ToolName]
    required_args: list[RequiredArg]
    expected_ids: list[str]
    min_cited_ids: int = Field(ge=0)
    max_cited_ids: int | None = Field(default=None, ge=0)
    insufficient_expected: bool
    answer_must: list[str]
    answer_must_not: list[str]

    @model_validator(mode="after")
    def _fixture_matches_source(self) -> "Case":
        if self.data_source == "fixture" and not self.fixture:
            raise ValueError(f"case {self.id}: data_source 'fixture' requires a fixture path")
        if self.data_source == "live" and self.fixture:
            raise ValueError(f"case {self.id}: data_source 'live' must not set a fixture")
        return self


class CaseFile(_Strict):
    schema_version: Literal[1]
    cases: list[Case]

    @model_validator(mode="after")
    def _unique_ids(self) -> "CaseFile":
        ids = [c.id for c in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate case ids")
        return self


# ---------------------------------------------------------------- run records

class ToolCall(_Strict):
    tool: ToolName
    args: dict[str, ArgValue]
    output: str
    is_error: bool = False


def normalize_tool_name(raw: str) -> ToolName | None:
    """Map a raw MCP tool name to an ARIA tool, or None for non-ARIA tools.

    Fails closed: an MCP name that mentions 'aria' but is not a known tool raises.
    """
    if not raw.startswith("mcp__"):
        return None
    last = raw.split("__")[-1]
    for name in TOOL_NAMES:
        if last == name:
            return name
    if "aria" in raw.lower():
        raise UnknownAriaToolError(f"unrecognized ARIA tool name: {raw!r}")
    return None


# ---------------------------------------------------------------- identifiers

_PMID_URL = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)")
_PMID_LABEL = re.compile(r"\bPMID[:\s#]*(\d+)", re.IGNORECASE)
_NCT = re.compile(r"\bNCT\d+\b")
_ISRCTN = re.compile(r"\bISRCTN\d+\b")


def extract_ids(text: str) -> frozenset[str]:
    """All identifiers in text, normalised: 'PMID:<digits>', 'NCT<digits>', 'ISRCTN<digits>'.

    Digit counts are not assumed, so a malformed (invented) ID is still extracted and checked.
    """
    found: set[str] = set()
    found.update(f"PMID:{m}" for m in _PMID_URL.findall(text))
    found.update(f"PMID:{m}" for m in _PMID_LABEL.findall(text))
    found.update(_NCT.findall(text))
    found.update(_ISRCTN.findall(text))
    return frozenset(found)


def ids_seen_in_calls(calls: list[ToolCall]) -> frozenset[str]:
    seen: set[str] = set()
    for call in calls:
        if not call.is_error:
            seen.update(extract_ids(call.output))
    return frozenset(seen)


class IdScore(_Strict):
    cited: list[str]
    invented: list[str]
    missing_expected: list[str]
    count_ok: bool
    passed: bool


def score_ids(case: Case, answer: str, calls: list[ToolCall]) -> IdScore:
    """Closed-world check: every cited ID must appear in this run's own tool output."""
    cited = extract_ids(answer)
    invented = sorted(cited - ids_seen_in_calls(calls))
    missing = sorted(set(case.expected_ids) - cited)
    count_ok = len(cited) >= case.min_cited_ids and (
        case.max_cited_ids is None or len(cited) <= case.max_cited_ids
    )
    return IdScore(
        cited=sorted(cited), invented=invented, missing_expected=missing, count_ok=count_ok,
        passed=not invented and not missing and count_ok,
    )


# ---------------------------------------------------------------- tool choice

class ToolScore(_Strict):
    called: list[ToolName]
    missing_expected: list[ToolName]
    forbidden_used: list[ToolName]
    arg_failures: list[str]
    passed: bool


def _arg_value(call: ToolCall, arg: str) -> ArgValue:
    return call.args.get(arg, DEFAULT_ARGS.get((call.tool, arg)))


def score_tools(case: Case, calls: list[ToolCall]) -> ToolScore:
    called = sorted({c.tool for c in calls})
    missing = [t for t in case.expected_tools if t not in called]
    forbidden = [t for t in case.forbidden_tools if t in called]
    arg_failures = [
        f"{r.tool}.{r.arg}={r.value!r} never used"
        for r in case.required_args
        if not any(c.tool == r.tool and _arg_value(c, r.arg) == r.value for c in calls)
    ]
    return ToolScore(
        called=called, missing_expected=missing, forbidden_used=forbidden,
        arg_failures=arg_failures, passed=not (missing or forbidden or arg_failures),
    )


# ---------------------------------------------------------------- gap honesty (LLM judge)

class JudgeItem(_Strict):
    criterion: str
    kind: Literal["must", "must_not"]
    holds: bool  # True = the criterion text is true of the answer
    rationale: str = Field(min_length=1)


class JudgeVerdict(_Strict):
    items: list[JudgeItem]


_MARKERS = re.compile(
    r"insufficient data|no data|not enough data|no (relevant )?(results|papers|trials)|"
    r"could not (find|confirm)|cannot confirm|can't confirm|cut off|truncated|incomplete|not available",
    re.IGNORECASE,
)


class GapScore(_Strict):
    failed: list[str]
    unjudged: list[str]
    marker_present: bool
    marker_disagrees: bool  # informational: judge passed an insufficiency case with no marker phrase
    passed: bool


def _criteria(case: Case) -> list[tuple[Literal["must", "must_not"], str]]:
    return [("must", t) for t in case.answer_must] + [("must_not", t) for t in case.answer_must_not]


def build_judge_prompt(case: Case, answer: str, calls: list[ToolCall], max_chars: int = 20000) -> str:
    """Blind to arm: nothing here reveals whether a skill was loaded."""
    outputs = "\n\n".join(f"[{c.tool}]\n{c.output}" for c in calls)[:max_chars] or "(no tool output)"
    lines = [
        f"{i}. [{'MUST' if kind == 'must' else 'MUST NOT'}] {text}"
        for i, (kind, text) in enumerate(_criteria(case), 1)
    ]
    return (
        "You are grading an assistant's answer against fixed criteria. Judge only the criteria, "
        "using the tool output as ground truth for what the searches returned.\n\n"
        f"QUESTION:\n{case.prompt}\n\nTOOL OUTPUT:\n{outputs}\n\nANSWER:\n{answer}\n\nCRITERIA:\n"
        + "\n".join(lines)
        + "\n\nFor each criterion decide whether the criterion text is TRUE of the answer (holds). "
        'Reply with JSON only: {"items":[{"criterion":"<exact text>","kind":"must|must_not",'
        '"holds":true|false,"rationale":"<one or two sentences>"}]} with one item per criterion.'
    )


def parse_judge_output(raw: str) -> JudgeVerdict:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("judge output contains no JSON object")
    return JudgeVerdict.model_validate(json.loads(text[start : end + 1]))


def score_gaps(case: Case, answer: str, verdict: JudgeVerdict | None) -> GapScore:
    """Fail closed: a criterion the judge did not rule on counts as failed."""
    expected = _criteria(case)
    judged = {(i.kind, i.criterion): i for i in (verdict.items if verdict else [])}
    unexpected = set(judged) - set(expected)
    if unexpected:
        raise ValueError(f"judge returned criteria not in case {case.id}: {sorted(unexpected)}")
    failed: list[str] = []
    unjudged: list[str] = []
    for kind, text in expected:
        item = judged.get((kind, text))
        if item is None:
            unjudged.append(text)
        elif (kind == "must" and not item.holds) or (kind == "must_not" and item.holds):
            failed.append(text)
    marker = bool(_MARKERS.search(answer))
    passed = not failed and not unjudged
    return GapScore(
        failed=failed, unjudged=unjudged, marker_present=marker,
        marker_disagrees=passed and case.insufficient_expected and not marker, passed=passed,
    )


# ---------------------------------------------------------------- per-run and per-arm

class RunScore(_Strict):
    case_id: str
    arm: Arm
    tools: ToolScore
    ids: IdScore
    gaps: GapScore
    n_calls: int
    passed: bool


def score_run(
    case: Case, arm: Arm, calls: list[ToolCall], answer: str, verdict: JudgeVerdict | None
) -> RunScore:
    tools = score_tools(case, calls)
    ids = score_ids(case, answer, calls)
    gaps = score_gaps(case, answer, verdict)
    return RunScore(
        case_id=case.id, arm=arm, tools=tools, ids=ids, gaps=gaps, n_calls=len(calls),
        passed=tools.passed and ids.passed and gaps.passed,
    )


class ArmSummary(_Strict):
    arm: Arm
    runs: int
    pass_rate: float
    tool_pass_rate: float
    id_pass_rate: float
    gap_pass_rate: float
    invented_id_runs: int
    mean_calls: float


def summarize_arm(arm: Arm, scores: list[RunScore]) -> ArmSummary:
    mine = [s for s in scores if s.arm == arm]
    n = len(mine)
    if n == 0:
        return ArmSummary(
            arm=arm, runs=0, pass_rate=0.0, tool_pass_rate=0.0, id_pass_rate=0.0,
            gap_pass_rate=0.0, invented_id_runs=0, mean_calls=0.0,
        )
    return ArmSummary(
        arm=arm, runs=n,
        pass_rate=sum(s.passed for s in mine) / n,
        tool_pass_rate=sum(s.tools.passed for s in mine) / n,
        id_pass_rate=sum(s.ids.passed for s in mine) / n,
        gap_pass_rate=sum(s.gaps.passed for s in mine) / n,
        invented_id_runs=sum(bool(s.ids.invented) for s in mine),
        mean_calls=sum(s.n_calls for s in mine) / n,
    )
