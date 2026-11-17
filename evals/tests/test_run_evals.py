"""Tests for evals/run_evals.py. No model calls: subprocess.run and run_cli are patched.

The only real subprocess used is the interpreter import check, which starts python (not claude).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from evals import run_evals as re_
from evals.score import Case, ToolCall, UnknownAriaToolError, score_run

REAL_RESOLVE = re_.resolve_server_file  # captured before the autouse patch below
TREE_SERVER = re_.REPO / "aria_mcp_server" / "server.py"
API_KEY_JSON = json.dumps({"loggedIn": True, "authMethod": "api_key", "apiKeySource": "ANTHROPIC_API_KEY", "x": 1})
OAUTH_JSON = json.dumps({"loggedIn": True, "authMethod": "oauth_token"})


@pytest.fixture(autouse=True)
def _no_real_server_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() resolves the live server with a real subprocess; keep that out of the gate tests."""
    monkeypatch.setattr(re_, "resolve_server_file", lambda: TREE_SERVER)


def make_case(**overrides: object) -> Case:
    base: dict[str, object] = {
        "id": "t1", "title": "t", "category": "literature", "data_source": "live",
        "prompt": "q", "expected_tools": [], "forbidden_tools": [], "required_args": [],
        "expected_ids": [], "min_cited_ids": 0, "max_cited_ids": None,
        "insufficient_expected": False, "answer_must": [], "answer_must_not": [],
    }
    base.update(overrides)
    return Case.model_validate(base)


def stream(*events: dict[str, object]) -> str:
    return "\n".join(json.dumps(e) for e in events)


CALL = {"type": "assistant", "message": {"content": [
    {"type": "tool_use", "id": "t1", "name": "mcp__aria-clinical-research__search_pubmed", "input": {"query": "x"}}]}}
RESULT_BLOCK = {"type": "user", "message": {"content": [
    {"type": "tool_result", "tool_use_id": "t1", "is_error": False,
     "content": [{"type": "text", "text": "URL: https://pubmed.ncbi.nlm.nih.gov/111/"}]}]}}
FINAL = {"type": "result", "subtype": "success", "is_error": False, "result": "See PMID 111", "total_cost_usd": 0.12}


# ------------------------------------------------------------ auth gate

def test_parse_auth_status_ignores_extra_fields() -> None:
    auth = re_.parse_auth_status(API_KEY_JSON)
    assert auth.method == "api_key" and auth.api_key_source == "ANTHROPIC_API_KEY"


@pytest.mark.parametrize("raw", ["not json", "{}", '{"loggedIn": true}'])
def test_parse_auth_status_fails_closed(raw: str) -> None:
    with pytest.raises(re_.HarnessError):
        re_.parse_auth_status(raw)


def test_billing_line_states_the_source() -> None:
    assert "BILLS AN API KEY" in re_.billing_line(re_.parse_auth_status(API_KEY_JSON))
    assert "cannot use it" in re_.billing_line(re_.parse_auth_status(OAUTH_JSON))
    assert "NOT LOGGED IN" in re_.billing_line(
        re_.AuthStatus(logged_in=False, method="api_key", api_key_source=None))


def test_auth_gate_requires_api_key_method_and_env_var() -> None:
    ok = re_.parse_auth_status(API_KEY_JSON)
    re_.check_auth_allows_isolated_runs(ok, {"ANTHROPIC_API_KEY": "k"})
    with pytest.raises(re_.HarnessError):
        re_.check_auth_allows_isolated_runs(ok, {})
    with pytest.raises(re_.HarnessError):
        re_.check_auth_allows_isolated_runs(re_.parse_auth_status(OAUTH_JSON), {"ANTHROPIC_API_KEY": "k"})


# ------------------------------------------------------------ plan

def test_plan_for_shipped_cases() -> None:
    plan = re_.build_plan(re_.load_cases(), runs=1, agent_cap=0.75, judge_cap=0.25)
    assert (plan.agent_runs, plan.judge_calls, plan.live_agent_runs) == (20, 20, 16)
    assert (plan.est_tool_calls_low, plan.est_tool_calls_high) == (64, 128)
    assert plan.worst_case_usd == pytest.approx(20.0)
    assert re_.build_plan(re_.load_cases(), 3, 0.75, 0.25).agent_runs == 60


def test_plan_text_shows_server_version_and_path() -> None:
    auth = re_.parse_auth_status(API_KEY_JSON)
    plan = re_.build_plan(re_.load_cases(), 1, 0.75, 0.25)
    ok = re_.render_plan(plan, auth, False, "0.2.2", TREE_SERVER, True)
    assert f"version 0.2.2, imported from {TREE_SERVER}" in ok and "NOT THIS WORKING TREE" not in ok
    bad = re_.render_plan(plan, auth, False, "0.2.2", Path("/venv/site-packages/aria_mcp_server/server.py"), False)
    assert "NOT THIS WORKING TREE" in bad


# ------------------------------------------------------------ server version and import location

def test_read_server_version_from_the_real_pyproject() -> None:
    version = re_.read_server_version((re_.REPO / "pyproject.toml").read_text())
    assert version.count(".") == 2 and version[0].isdigit()


def test_read_server_version_is_anchored_to_the_project_table() -> None:
    text = '[tool.other]\nversion = "9.9.9"\n\n[project]\nname = "x"\nversion = "1.2.3"\n\n[project.scripts]\nversion = "7"\n'
    assert re_.read_server_version(text) == "1.2.3"


def test_read_server_version_fails_closed() -> None:
    with pytest.raises(re_.HarnessError):
        re_.read_server_version('[tool.other]\nversion = "9.9.9"\n[project]\nname = "x"\n')


def test_live_config_runs_this_interpreter_with_the_repo_first_on_pythonpath() -> None:
    spec = re_.live_server_spec()
    assert spec["command"] == sys.executable and spec["args"] == ["-m", "aria_mcp_server.server"]
    assert spec["env"] == {"PYTHONPATH": str(re_.REPO)}
    live = re_.mcp_config(make_case(), None)["mcpServers"]["aria-clinical-research"]  # type: ignore[index]
    assert live["env"]["PYTHONPATH"] == str(re_.REPO)


def test_live_server_imports_this_working_tree_not_an_installed_copy() -> None:
    """Real interpreter, the config's env, an empty temp cwd: must resolve to this repo's server.py."""
    assert REAL_RESOLVE() == TREE_SERVER.resolve()


def test_import_failure_raises_harness_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ModuleNotFoundError: fastmcp")

    monkeypatch.setattr(re_.subprocess, "run", fake_run)
    with pytest.raises(re_.HarnessError, match="cannot import the live server"):
        REAL_RESOLVE()


@pytest.mark.parametrize("exc", [subprocess.TimeoutExpired(cmd="python", timeout=60), FileNotFoundError("python")])
def test_import_probe_timeout_or_missing_interpreter_raises_harness_error(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        raise exc

    monkeypatch.setattr(re_.subprocess, "run", fake_run)
    with pytest.raises(re_.HarnessError, match="cannot import the live server"):
        REAL_RESOLVE()


def test_execute_stops_on_import_failure_before_any_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["claude", "auth"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"loggedIn": True, "authMethod": "api_key", "apiKeySource": "ANTHROPIC_API_KEY"}), stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ModuleNotFoundError")

    def failing_resolve() -> Path:
        raise re_.HarnessError("cannot import the live server: boom")

    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(re_, "resolve_server_file", failing_resolve)
    monkeypatch.setattr(re_.subprocess, "run", fake_run)
    with pytest.raises(re_.HarnessError, match="cannot import"):
        re_.main(["--execute", "--runs", "1"])
    assert not any(c[:2] == ["claude", "-p"] or "-p" in c for c in calls)


def test_server_in_working_tree() -> None:
    assert re_.server_in_working_tree(TREE_SERVER, re_.REPO)
    assert not re_.server_in_working_tree(Path("/venv/site-packages/aria_mcp_server/server.py"), re_.REPO)


# ------------------------------------------------------------ command construction

def test_mcp_config_live_vs_fixture() -> None:
    live = re_.mcp_config(make_case(), None)["mcpServers"]["aria-clinical-research"]  # type: ignore[index]
    assert live["args"] == ["-m", "aria_mcp_server.server"]
    fx = make_case(data_source="fixture", fixture="fixtures/c05_ctgov_truncated_exclusion.json")
    stub = re_.mcp_config(fx, Path("/tmp/log.jsonl"))["mcpServers"]["aria-clinical-research"]  # type: ignore[index]
    assert stub["args"][0].endswith("evals/stub_server.py")
    assert stub["env"]["ARIA_STUB_FIXTURE"].endswith("fixtures/c05_ctgov_truncated_exclusion.json")
    assert stub["env"]["ARIA_STUB_CALL_LOG"] == "/tmp/log.jsonl"
    assert stub["env"]["PYTHONPATH"] == str(re_.REPO)


def test_agent_command_differs_only_by_the_skill_tool() -> None:
    a = re_.build_agent_command("mcp_only", "m", Path("/c.json"), 0.75)
    b = re_.build_agent_command("mcp_plus_skill", "m", Path("/c.json"), 0.75)
    assert "--tools=" in a and "--allowedTools=mcp__aria-clinical-research__*" in a
    assert "--tools=Skill" in b and "--allowedTools=mcp__aria-clinical-research__*,Skill" in b
    for cmd in (a, b):
        assert "--strict-mcp-config" in cmd and "--no-session-persistence" in cmd and "--bare" not in cmd
        assert cmd[cmd.index("--max-budget-usd") + 1] == "0.75"

    def shared(cmd: list[str]) -> list[str]:
        return [x for x in cmd if not x.startswith(("--tools=", "--allowedTools="))]

    assert shared(a) == shared(b)


def test_judge_command_has_no_tools_and_no_skill() -> None:
    cmd = re_.build_judge_command("j", 0.25)
    assert "--tools=" in cmd and not any("Skill" in c for c in cmd) and "json" in cmd


def test_isolated_env_is_minimal(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("SECRET_TOKEN", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    env = re_.isolated_env(tmp_path)
    assert "SECRET_TOKEN" not in env and env["ANTHROPIC_API_KEY"] == "k"
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path)


# ------------------------------------------------------------ transcript parsing

def test_parse_stream_extracts_calls_answer_cost_and_skill() -> None:
    skill = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "s1", "name": "Skill", "input": {"skill": "aria-clinical-research"}}]}}
    read = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "r1", "name": "Read", "input": {"file_path": "x"}}]}}
    run = re_.parse_stream(stream(skill, read, CALL, RESULT_BLOCK, FINAL))
    assert [c.tool for c in run.calls] == ["search_pubmed"]  # Read and Skill are not ARIA calls
    assert run.calls[0].output.endswith("/111/") and not run.calls[0].is_error
    assert run.answer == "See PMID 111" and run.cost_usd == 0.12 and run.skill_invoked and run.error is None


def test_parse_stream_marks_unanswered_call_as_error() -> None:
    run = re_.parse_stream(stream(CALL, FINAL))
    assert run.calls[0].is_error


def test_parse_stream_flags_non_success_result() -> None:
    bad = {**FINAL, "subtype": "error_max_budget_usd", "is_error": True}
    assert re_.parse_stream(stream(CALL, RESULT_BLOCK, bad)).error is not None


def test_parse_stream_fails_closed() -> None:
    with pytest.raises(re_.HarnessError):
        re_.parse_stream(stream(CALL, RESULT_BLOCK))  # no result event
    with pytest.raises(re_.HarnessError):
        re_.parse_stream("not json")
    unknown = {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "u", "name": "mcp__aria-clinical-research__search_pubmeds", "input": {}}]}}
    with pytest.raises(UnknownAriaToolError):
        re_.parse_stream(stream(unknown, FINAL))


def test_parse_judge_response() -> None:
    inner = json.dumps({"items": [{"criterion": "a", "kind": "must", "holds": True, "rationale": "ok"}]})
    verdict, cost = re_.parse_judge_response(json.dumps({"result": inner, "total_cost_usd": 0.3}))
    assert verdict.items[0].holds and cost == 0.3
    with pytest.raises(re_.HarnessError):
        re_.parse_judge_response("garbage")


# ------------------------------------------------------------ reporting

def record(case: Case, arm: str, run: int, answer: str, error: str | None = None, skill: bool = False) -> re_.RunRecord:
    calls = [ToolCall(tool="search_pubmed", args={}, output="URL: https://pubmed.ncbi.nlm.nih.gov/111/")]
    score = None if error else score_run(case, arm, calls, answer, None)  # type: ignore[arg-type]
    return re_.RunRecord(case_id=case.id, arm=arm, run=run, error=error, judge_error=None,  # type: ignore[arg-type]
                         score=score, cost_usd=0.1, skill_invoked=skill)


def test_report_counts_errors_as_failures_and_lists_invented_ids() -> None:
    case = make_case()
    recs = [
        record(case, "mcp_only", 1, "fine"),
        record(case, "mcp_only", 2, "PMID 999"),  # invented
        record(case, "mcp_plus_skill", 1, "fine", skill=True),
        record(case, "mcp_plus_skill", 2, "", error="HarnessError: boom"),
    ]
    rep = re_.build_report(recs, "smoke test, N=1", "api_key", "2026-11-17", "0.2.2")
    only, with_skill = rep.arms
    assert (only.attempted, only.passed, only.summary.invented_id_runs) == (2, 1, 1)
    assert (with_skill.attempted, with_skill.errored, with_skill.passed, with_skill.skill_invoked_runs) == (2, 1, 1, 1)
    assert any("invented IDs" in f and "PMID:999" in f for f in rep.failures)
    assert any("boom" in f for f in rep.failures)
    assert rep.total_cost_usd == pytest.approx(0.4)


def test_results_markdown_contents() -> None:
    case = make_case()
    rep = re_.build_report([record(case, "mcp_only", 1, "fine")], "smoke test, N=1", "api_key", "2026-11-17", "0.2.2")
    md = re_.render_results_md(rep)
    for needle in ("2026-11-17", re_.AGENT_MODEL, re_.JUDGE_MODEL, "smoke test, N=1", "`mcp_only`",
                   "`mcp_plus_skill`", "## Notable failures", "- None", "ARIA server version:** 0.2.2"):
        assert needle in md
    assert "/Users/" not in md  # no local paths in the committed summary


# ------------------------------------------------------------ one run, fully offline

class FakeCli:
    """Stands in for run_cli: records what each config and work directory contained at call time."""

    def __init__(self) -> None:
        self.agent_dirs: list[set[str]] = []
        self.judge_dirs: list[set[str]] = []
        self.agent_cwds: list[Path] = []
        self.judge_cwds: list[Path] = []
        self.judge_cwd_listing: list[set[str]] = []

    def __call__(self, cmd: list[str], stdin: str, env: dict[str, str], cwd: Path) -> re_.CliResult:
        cfg = Path(env["CLAUDE_CONFIG_DIR"])
        listing = {p.relative_to(cfg).as_posix() for p in cfg.rglob("*")}
        if "stream-json" in cmd:
            self.agent_dirs.append(listing)
            self.agent_cwds.append(cwd)
            return re_.CliResult(stdout=stream(CALL, RESULT_BLOCK, FINAL), stderr="", returncode=0)
        self.judge_dirs.append(listing)
        self.judge_cwds.append(cwd)
        self.judge_cwd_listing.append({p.name for p in cwd.iterdir()})
        inner = json.dumps({"items": [
            {"criterion": "Names a PMID.", "kind": "must", "holds": True, "rationale": "It cites PMID 111."}]})
        return re_.CliResult(stdout=json.dumps({"result": inner, "total_cost_usd": 0.2}), stderr="", returncode=0)


def run_offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arm: str) -> tuple[FakeCli, Path, re_.RunRecord]:
    fake = FakeCli()
    monkeypatch.setattr(re_, "run_cli", fake)
    case = make_case(answer_must=["Names a PMID."])
    rec = re_.execute_one(case, arm, 1, tmp_path, 0.75, 0.25)  # type: ignore[arg-type]
    return fake, tmp_path / "t1" / arm / "run1", rec


@pytest.mark.parametrize("arm", ["mcp_only", "mcp_plus_skill"])
def test_judge_prompt_is_blind_to_arm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arm: str) -> None:
    _, run_dir, rec = run_offline(monkeypatch, tmp_path, arm)
    prompt = (run_dir / "judge_prompt.txt").read_text().lower()
    for leaked in ("mcp_only", "mcp_plus_skill", "skill"):
        assert leaked not in prompt
    assert rec.error is None and rec.score is not None and rec.score.passed


def test_arm_b_config_dir_has_the_skill_and_no_claude_md(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake, _, _ = run_offline(monkeypatch, tmp_path, "mcp_plus_skill")
    listing = fake.agent_dirs[0]
    assert "skills/aria-clinical-research/SKILL.md" in listing
    assert not any(Path(p).name == "CLAUDE.md" for p in listing)


def test_arm_a_config_dir_has_no_skills_and_no_claude_md(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake, _, _ = run_offline(monkeypatch, tmp_path, "mcp_only")
    listing = fake.agent_dirs[0]
    assert not any(p == "skills" or p.startswith("skills/") for p in listing)
    assert not any(Path(p).name == "CLAUDE.md" for p in listing)


@pytest.mark.parametrize("arm", ["mcp_only", "mcp_plus_skill"])
def test_judge_runs_in_a_clean_config_dir_for_both_arms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arm: str
) -> None:
    fake, _, _ = run_offline(monkeypatch, tmp_path, arm)
    listing = fake.judge_dirs[0]
    assert not any(p == "skills" or p.startswith("skills/") for p in listing)
    assert not any(Path(p).name == "CLAUDE.md" for p in listing)


@pytest.mark.parametrize("arm", ["mcp_only", "mcp_plus_skill"])
def test_judge_gets_its_own_empty_work_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, arm: str) -> None:
    fake, _, _ = run_offline(monkeypatch, tmp_path, arm)
    assert fake.judge_cwds[0] != fake.agent_cwds[0]
    assert fake.judge_cwd_listing[0] == set()


# ------------------------------------------------------------ the execute gate (no real claude)

def patch_subprocess(monkeypatch: pytest.MonkeyPatch, auth_json: str) -> list[list[str]]:
    seen: list[list[str]] = []

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=auth_json, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return seen


def test_dry_run_only_asks_for_auth_status(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    seen = patch_subprocess(monkeypatch, API_KEY_JSON)
    assert re_.main([]) == 0
    assert seen == [["claude", "auth", "status"]]
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "BILLS AN API KEY" in out and "ARIA server: version" in out
    assert "imported from" in out


def test_execute_refuses_non_api_key_auth_before_any_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")  # so the refusal is about the auth method, not the env var
    seen = patch_subprocess(monkeypatch, OAUTH_JSON)
    with pytest.raises(re_.HarnessError, match="not an API key"):
        re_.main(["--execute"])
    assert seen == [["claude", "auth", "status"]]


def test_execute_refuses_when_api_key_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = patch_subprocess(monkeypatch, API_KEY_JSON)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(re_.HarnessError, match="ANTHROPIC_API_KEY"):
        re_.main(["--execute"])
    assert seen == [["claude", "auth", "status"]]


def test_execute_refuses_when_server_is_not_this_working_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(re_, "resolve_server_file", lambda: Path("/venv/site-packages/aria_mcp_server/server.py"))
    seen = patch_subprocess(monkeypatch, API_KEY_JSON)
    with pytest.raises(re_.HarnessError, match="working tree"):
        re_.main(["--execute"])
    assert seen == [["claude", "auth", "status"]]


def test_unknown_case_id_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = patch_subprocess(monkeypatch, API_KEY_JSON)
    with pytest.raises(re_.HarnessError):
        re_.main(["--cases", "c99"])
    assert seen in ([], [["claude", "auth", "status"]])
