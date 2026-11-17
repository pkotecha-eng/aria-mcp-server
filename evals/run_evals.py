"""Harness for the ARIA skill evals. See evals/README.md.

Dry run by default: prints the plan, the billing source and the worst-case spend, then exits.
Nothing calls `claude -p` unless --execute is passed. Runs are sequential (NCBI rate limit).

Isolation: every `claude -p` call gets a fresh, empty CLAUDE_CONFIG_DIR (no user CLAUDE.md,
memory, plugins or skills), a fresh working directory, strict MCP config containing only the
ARIA server, and a minimal environment. Arm `mcp_plus_skill` additionally gets the skill copied
into that config directory's skills/ folder. Authentication therefore has to come from
ANTHROPIC_API_KEY; the harness refuses to run otherwise.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from evals.score import (
    Arm,
    ArmSummary,
    Case,
    CaseFile,
    JudgeVerdict,
    RunScore,
    ToolCall,
    UnknownAriaToolError,
    build_judge_prompt,
    normalize_tool_name,
    parse_judge_output,
    score_run,
    summarize_arm,
)

REPO = Path(__file__).resolve().parent.parent
EVALS = REPO / "evals"
SKILL_DIR = REPO / "skills" / "aria-clinical-research"
RESULTS_DIR = EVALS / "results"
RESULTS_MD = EVALS / "RESULTS.md"

AGENT_MODEL = "claude-sonnet-5-5"
JUDGE_MODEL = "claude-opus-5-5"
SERVER_KEY = "aria-clinical-research"
ARMS: tuple[Arm, ...] = ("mcp_only", "mcp_plus_skill")
LIVE_CALLS_PER_RUN = (4, 8)  # planning estimate only
RUN_TIMEOUT_S = 600


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HarnessError(RuntimeError):
    """A run could not be completed or parsed. Recorded as a failed run, never skipped."""


# ------------------------------------------------------------------ authentication gate

class AuthStatus(_Strict):
    logged_in: bool
    method: str
    api_key_source: str | None


def parse_auth_status(raw: str) -> AuthStatus:
    """Boundary parser for `claude auth status`: take only the fields we need."""
    try:
        data = json.loads(raw)
        return AuthStatus(
            logged_in=bool(data["loggedIn"]),
            method=str(data["authMethod"]),
            api_key_source=data.get("apiKeySource"),
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise HarnessError(f"cannot parse `claude auth status` output: {exc}") from exc


def billing_line(auth: AuthStatus) -> str:
    if not auth.logged_in:
        return "NOT LOGGED IN: runs cannot proceed."
    if auth.method == "api_key":
        return f"BILLS AN API KEY (source: {auth.api_key_source}); it does not use a subscription."
    return f"auth method '{auth.method}' is not an API key; isolated runs cannot use it."


def check_auth_allows_isolated_runs(auth: AuthStatus, env: dict[str, str]) -> None:
    if not auth.logged_in or auth.method != "api_key":
        raise HarnessError(f"refusing to run: {billing_line(auth)}")
    if not env.get("ANTHROPIC_API_KEY"):
        raise HarnessError(
            "refusing to run: isolated runs read the key from ANTHROPIC_API_KEY, which is not set "
            f"(auth source reported as {auth.api_key_source})."
        )


# ------------------------------------------------------------------ plan

class Plan(_Strict):
    cases: int
    runs_per_case: int
    agent_runs: int
    judge_calls: int
    live_agent_runs: int
    est_tool_calls_low: int
    est_tool_calls_high: int
    worst_case_usd: float


def build_plan(cases: list[Case], runs: int, agent_cap: float, judge_cap: float) -> Plan:
    agent_runs = len(cases) * len(ARMS) * runs
    live = sum(1 for c in cases if c.data_source == "live") * len(ARMS) * runs
    return Plan(
        cases=len(cases), runs_per_case=runs, agent_runs=agent_runs, judge_calls=agent_runs,
        live_agent_runs=live,
        est_tool_calls_low=live * LIVE_CALLS_PER_RUN[0], est_tool_calls_high=live * LIVE_CALLS_PER_RUN[1],
        worst_case_usd=agent_runs * agent_cap + agent_runs * judge_cap,
    )


def render_plan(
    plan: Plan, auth: AuthStatus, execute: bool, server_version: str, server_file: Path, in_tree: bool
) -> str:
    return "\n".join([
        f"Agent model: {AGENT_MODEL}   Judge model: {JUDGE_MODEL}",
        f"ARIA server: version {server_version}, imported from {server_file}"
        + ("" if in_tree else "  *** NOT THIS WORKING TREE: --execute will refuse ***"),
        f"Cases: {plan.cases}   Arms: {len(ARMS)}   Runs per case: {plan.runs_per_case}",
        f"Agent runs: {plan.agent_runs}   Judge calls: {plan.judge_calls}   "
        f"Total `claude -p` calls: {plan.agent_runs + plan.judge_calls}",
        f"Live agent runs: {plan.live_agent_runs} -> est. {plan.est_tool_calls_low}-"
        f"{plan.est_tool_calls_high} live ARIA tool calls (fixture cases make none)",
        f"Worst-case spend (every call hits its budget cap): ${plan.worst_case_usd:.2f}",
        f"Billing: {billing_line(auth)}",
        "Mode: EXECUTE" if execute else "Mode: DRY RUN (nothing will be called)",
    ])


# ------------------------------------------------------------------ command construction

LIVE_IMPORT_PROBE = "import aria_mcp_server.server as s, inspect; print(inspect.getfile(s))"


def live_server_spec() -> dict[str, object]:
    """How the harness launches the live ARIA server: this interpreter, this working tree first on the path."""
    return {
        "command": sys.executable,
        "args": ["-m", "aria_mcp_server.server"],
        "env": {"PYTHONPATH": str(REPO)},
    }


def resolve_server_file() -> Path:
    """Import the live server module exactly as the MCP config launches it and return its file.

    Same interpreter and environment as live_server_spec(), from an empty temp cwd, so a stray
    installed copy of aria-mcp-server cannot be picked up silently.
    """
    spec = live_server_spec()
    base = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
    env = {**base, **dict(spec["env"])}  # type: ignore[call-overload]
    with tempfile.TemporaryDirectory() as tmp:
        try:
            done = subprocess.run(
                [str(spec["command"]), "-c", LIVE_IMPORT_PROBE],
                capture_output=True, text=True, env=env, cwd=tmp, timeout=60,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise HarnessError(f"cannot import the live server: {exc}") from exc
    lines = done.stdout.strip().splitlines()
    if done.returncode != 0 or not lines:
        raise HarnessError(f"cannot import the live server: {done.stderr.strip()[-300:]}")
    return Path(lines[-1]).resolve()


def server_in_working_tree(server_file: Path, repo: Path) -> bool:
    return server_file.resolve().is_relative_to(repo.resolve())


def mcp_config(case: Case, call_log: Path | None) -> dict[str, object]:
    env = {"PYTHONPATH": str(REPO)}
    if case.data_source == "live":
        server = live_server_spec()
    else:
        assert case.fixture is not None  # guaranteed by Case validation
        env["ARIA_STUB_FIXTURE"] = str(EVALS / case.fixture)
        if call_log is not None:
            env["ARIA_STUB_CALL_LOG"] = str(call_log)
        server = {"command": sys.executable, "args": [str(EVALS / "stub_server.py")], "env": env}
    return {"mcpServers": {SERVER_KEY: server}}


def build_agent_command(arm: Arm, model: str, config_path: Path, budget_usd: float) -> list[str]:
    skill = arm == "mcp_plus_skill"
    allowed = f"mcp__{SERVER_KEY}__*" + (",Skill" if skill else "")
    return [
        "claude", "-p", "--output-format", "stream-json", "--verbose", "--model", model,
        f"--mcp-config={config_path}", "--strict-mcp-config",
        f"--tools={'Skill' if skill else ''}", f"--allowedTools={allowed}",
        "--permission-mode", "dontAsk", "--max-budget-usd", f"{budget_usd:.2f}",
        "--no-session-persistence",
    ]


def build_judge_command(model: str, budget_usd: float) -> list[str]:
    return [
        "claude", "-p", "--output-format", "json", "--model", model,
        '--mcp-config={"mcpServers":{}}', "--strict-mcp-config", "--tools=",
        "--permission-mode", "dontAsk", "--max-budget-usd", f"{budget_usd:.2f}",
        "--no-session-persistence",
    ]


def isolated_env(config_dir: Path) -> dict[str, str]:
    """Minimal environment: nothing from the user's shell beyond what the run needs."""
    keep = {k: os.environ[k] for k in ("PATH", "HOME", "LANG", "ANTHROPIC_API_KEY") if k in os.environ}
    return {**keep, "CLAUDE_CONFIG_DIR": str(config_dir), "NO_COLOR": "1"}


class CliResult(_Strict):
    stdout: str
    stderr: str
    returncode: int


def run_cli(cmd: list[str], stdin: str, env: dict[str, str], cwd: Path) -> CliResult:
    try:
        done = subprocess.run(
            cmd, input=stdin, capture_output=True, text=True, env=env, cwd=cwd, timeout=RUN_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(f"timed out after {RUN_TIMEOUT_S}s: {cmd[:3]}") from exc
    return CliResult(stdout=done.stdout, stderr=done.stderr, returncode=done.returncode)


# ------------------------------------------------------------------ transcript parsing

class ParsedRun(_Strict):
    calls: list[ToolCall]
    answer: str
    cost_usd: float
    error: str | None
    skill_invoked: bool


def _text_of(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return ""


def parse_stream(stdout: str) -> ParsedRun:
    """Parse `claude -p --output-format stream-json` into ARIA tool calls and the final answer.

    Fails closed: malformed lines, a missing result event, or an unrecognized ARIA tool name
    raise instead of being skipped. A tool call with no result is kept and marked as an error.
    """
    pending: dict[str, tuple[str, dict[str, object]]] = {}
    order: list[str] = []
    outputs: dict[str, tuple[str, bool]] = {}
    result: dict[str, object] | None = None
    skill_invoked = False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError as exc:
            raise HarnessError(f"non-JSON line in stream: {line[:80]!r}") from exc
        kind = event.get("type")
        content = (event.get("message") or {}).get("content")
        if kind == "assistant" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    pending[block["id"]] = (block["name"], block.get("input") or {})
                    order.append(block["id"])
                    if block["name"] == "Skill" and "aria-clinical-research" in json.dumps(block.get("input")):
                        skill_invoked = True
        elif kind == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    outputs[block["tool_use_id"]] = (_text_of(block.get("content")), bool(block.get("is_error")))
        elif kind == "result":
            result = event
    if result is None:
        raise HarnessError("stream has no result event")
    calls: list[ToolCall] = []
    for tool_id in order:
        raw_name, args = pending[tool_id]
        tool = normalize_tool_name(raw_name)  # raises UnknownAriaToolError on an unknown ARIA tool
        if tool is None:
            continue
        output, is_error = outputs.get(tool_id, ("", True))
        calls.append(ToolCall(tool=tool, args=args, output=output, is_error=is_error))  # type: ignore[arg-type]
    subtype = result.get("subtype")
    error = None if (subtype == "success" and not result.get("is_error")) else f"result subtype {subtype!r}"
    answer = result.get("result")
    return ParsedRun(
        calls=calls, answer=answer if isinstance(answer, str) else "",
        cost_usd=float(result.get("total_cost_usd") or 0.0), error=error, skill_invoked=skill_invoked,
    )


def parse_judge_response(stdout: str) -> tuple[JudgeVerdict, float]:
    try:
        data = json.loads(stdout)
        return parse_judge_output(str(data["result"])), float(data.get("total_cost_usd") or 0.0)
    except (ValueError, KeyError, TypeError) as exc:
        raise HarnessError(f"cannot parse judge response: {exc}") from exc


# ------------------------------------------------------------------ one run

class RunRecord(_Strict):
    case_id: str
    arm: Arm
    run: int
    error: str | None
    judge_error: str | None
    score: RunScore | None
    cost_usd: float
    skill_invoked: bool


def _write(path: Path, text: str) -> None:
    path.write_text(text)


def execute_one(case: Case, arm: Arm, idx: int, out_dir: Path, agent_cap: float, judge_cap: float) -> RunRecord:
    run_dir = out_dir / case.id / arm / f"run{idx}"
    run_dir.mkdir(parents=True, exist_ok=True)
    call_log = run_dir / "stub_calls.jsonl" if case.data_source == "fixture" else None
    config_path = run_dir / "mcp.json"
    _write(config_path, json.dumps(mcp_config(case, call_log), indent=2))
    cost = 0.0
    try:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir, work = Path(tmp) / "config", Path(tmp) / "work"
            config_dir.mkdir()
            work.mkdir()
            if arm == "mcp_plus_skill":
                shutil.copytree(SKILL_DIR, config_dir / "skills" / SKILL_DIR.name)
            env = isolated_env(config_dir)
            # The judge gets its own empty config dir and work dir for both arms: no skill, no CLAUDE.md.
            judge_config, judge_work = Path(tmp) / "judge_config", Path(tmp) / "judge_work"
            judge_config.mkdir()
            judge_work.mkdir()
            judge_env = isolated_env(judge_config)
            agent = run_cli(build_agent_command(arm, AGENT_MODEL, config_path, agent_cap), case.prompt, env, work)
            _write(run_dir / "transcript.jsonl", agent.stdout)
            _write(run_dir / "stderr.txt", agent.stderr)
            parsed = parse_stream(agent.stdout)
            cost = parsed.cost_usd
            _write(run_dir / "calls.json", json.dumps([c.model_dump() for c in parsed.calls], indent=2))
            _write(run_dir / "answer.md", parsed.answer)
            if parsed.error:
                return RunRecord(case_id=case.id, arm=arm, run=idx, error=parsed.error, judge_error=None,
                                 score=None, cost_usd=cost, skill_invoked=parsed.skill_invoked)
            prompt = build_judge_prompt(case, parsed.answer, parsed.calls)
            _write(run_dir / "judge_prompt.txt", prompt)
            verdict: JudgeVerdict | None = None
            judge_error: str | None = None
            try:
                judged = run_cli(build_judge_command(JUDGE_MODEL, judge_cap), prompt, judge_env, judge_work)
                _write(run_dir / "judge_raw.json", judged.stdout)
                verdict, judge_cost = parse_judge_response(judged.stdout)
                cost += judge_cost
                _write(run_dir / "judge.json", verdict.model_dump_json(indent=2))  # includes rationales
            except HarnessError as exc:
                judge_error = str(exc)
            score = score_run(case, arm, parsed.calls, parsed.answer, verdict)
            _write(run_dir / "score.json", score.model_dump_json(indent=2))
            return RunRecord(case_id=case.id, arm=arm, run=idx, error=None, judge_error=judge_error,
                             score=score, cost_usd=cost, skill_invoked=parsed.skill_invoked)
    except (HarnessError, UnknownAriaToolError, ValidationError, ValueError) as exc:
        return RunRecord(case_id=case.id, arm=arm, run=idx, error=f"{type(exc).__name__}: {exc}",
                         judge_error=None, score=None, cost_usd=cost, skill_invoked=False)


# ------------------------------------------------------------------ reporting (pure)

class ArmReport(_Strict):
    arm: Arm
    attempted: int
    errored: int
    passed: int
    summary: ArmSummary
    skill_invoked_runs: int
    cost_usd: float


class Report(_Strict):
    date: str
    server_version: str
    label: str
    agent_model: str
    judge_model: str
    auth_method: str
    arms: list[ArmReport]
    failures: list[str]
    spot_checks: list[str]
    total_cost_usd: float


def read_server_version(pyproject_text: str) -> str:
    """Version from the [project] table only.

    The Python floor is 3.10 (requires-python = ">=3.10"), so tomllib (3.11+) is not available;
    this scans the [project] table and ignores `version` keys in any other table.
    """
    in_project = False
    for line in pyproject_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_project = stripped == "[project]"
            continue
        if in_project:
            match = re.fullmatch(r'version\s*=\s*"([^"]+)"', stripped)
            if match:
                return match.group(1)
    raise HarnessError("no version in the [project] table of pyproject.toml")


def failure_reasons(rec: RunRecord) -> list[str]:
    if rec.error:
        return [f"run error: {rec.error}"]
    assert rec.score is not None
    s, reasons = rec.score, []
    if s.tools.missing_expected:
        reasons.append(f"missing tools {s.tools.missing_expected}")
    if s.tools.forbidden_used:
        reasons.append(f"forbidden tools used {s.tools.forbidden_used}")
    reasons += s.tools.arg_failures
    if s.ids.invented:
        reasons.append(f"invented IDs {s.ids.invented}")
    if s.ids.missing_expected:
        reasons.append(f"expected IDs not cited {s.ids.missing_expected}")
    if not s.ids.count_ok:
        reasons.append("citation count out of bounds")
    if s.gaps.failed:
        reasons.append(f"criteria failed: {s.gaps.failed}")
    if s.gaps.unjudged:
        reasons.append(f"criteria not judged: {s.gaps.unjudged}")
    if rec.judge_error:
        reasons.append(f"judge error: {rec.judge_error}")
    return reasons


def build_report(
    records: list[RunRecord], label: str, auth_method: str, today: str, server_version: str
) -> Report:
    arms: list[ArmReport] = []
    for arm in ARMS:
        mine = [r for r in records if r.arm == arm]
        scores = [r.score for r in mine if r.score is not None]
        arms.append(ArmReport(
            arm=arm, attempted=len(mine), errored=sum(1 for r in mine if r.error),
            passed=sum(1 for s in scores if s.passed), summary=summarize_arm(arm, scores),
            skill_invoked_runs=sum(1 for r in mine if r.skill_invoked), cost_usd=sum(r.cost_usd for r in mine),
        ))
    failures = [
        f"{r.case_id} / {r.arm} / run {r.run}: " + "; ".join(failure_reasons(r))
        for r in records if r.error or (r.score is not None and not r.score.passed) or r.judge_error
    ]
    spot = [
        f"{r.case_id} / {r.arm} / run {r.run}" for r in records
        if r.score is not None and r.score.gaps.marker_disagrees
    ]
    return Report(date=today, server_version=server_version, label=label, agent_model=AGENT_MODEL, judge_model=JUDGE_MODEL,
                  auth_method=auth_method, arms=arms, failures=failures, spot_checks=spot,
                  total_cost_usd=sum(r.cost_usd for r in records))


def render_results_md(rep: Report) -> str:
    def pct(x: float) -> str:
        return f"{100 * x:.0f}%"

    rows = [
        "| Arm | Runs | Errored | Pass | Tool choice | IDs | Gap honesty | Runs with invented IDs | Mean tool calls | Skill invoked |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for a in rep.arms:
        s = a.summary
        rows.append(
            f"| `{a.arm}` | {a.attempted} | {a.errored} | {a.passed}/{a.attempted} | {pct(s.tool_pass_rate)} | "
            f"{pct(s.id_pass_rate)} | {pct(s.gap_pass_rate)} | {s.invented_id_runs} | {s.mean_calls:.1f} | "
            f"{a.skill_invoked_runs if a.arm == 'mcp_plus_skill' else 'n/a'} |"
        )
    failures = "\n".join(f"- {f}" for f in rep.failures) or "- None"
    spot = "\n".join(f"- {s}" for s in rep.spot_checks) or "- None"
    return "\n".join([
        "# Eval results",
        "",
        f"- **Date:** {rep.date}",
        f"- **Run:** {rep.label}",
        f"- **Agent model:** `{rep.agent_model}`   **Judge model:** `{rep.judge_model}`",
        f"- **ARIA server version:** {rep.server_version} (this working tree, launched with "
        "`python -m aria_mcp_server.server`)",
        f"- **Auth:** {rep.auth_method}   **Recorded cost:** ${rep.total_cost_usd:.2f}",
        "",
        "Pass rates are passed runs over attempted runs; errored runs count as failures. "
        "Tool choice, IDs and gap honesty are per-dimension pass rates over scored runs. "
        "See [README.md](README.md) for what each dimension checks.",
        "",
        *rows,
        "",
        "## Notable failures",
        "",
        failures,
        "",
        "## Judge spot-checks (insufficiency case passed with no insufficiency wording)",
        "",
        spot,
        "",
    ])


# ------------------------------------------------------------------ probe, CLI

PROBE_PROMPT = (
    "Answer in two short lines. Line 1: the exact names of any skills available to you, or 'none'. "
    "Line 2: whether you have any instructions that mention 'Agentic system invariants' (yes/no)."
)


def run_probe() -> int:
    """Two tiny calls (one per arm, no ARIA server) to confirm isolation and skill visibility."""
    for arm in ARMS:
        with tempfile.TemporaryDirectory() as tmp:
            config_dir, work = Path(tmp) / "config", Path(tmp) / "work"
            config_dir.mkdir()
            work.mkdir()
            if arm == "mcp_plus_skill":
                shutil.copytree(SKILL_DIR, config_dir / "skills" / SKILL_DIR.name)
            cmd = [
                "claude", "-p", "--output-format", "json", "--model", AGENT_MODEL,
                '--mcp-config={"mcpServers":{}}', "--strict-mcp-config",
                f"--tools={'Skill' if arm == 'mcp_plus_skill' else ''}",
                "--permission-mode", "dontAsk", "--max-budget-usd", "0.05", "--no-session-persistence",
            ]
            out = run_cli(cmd, PROBE_PROMPT, isolated_env(config_dir), work)
            print(f"--- probe {arm} (exit {out.returncode}) ---\n{out.stdout.strip()}\n{out.stderr.strip()[:300]}")
    return 0


def load_cases() -> list[Case]:
    return CaseFile.model_validate(json.loads((EVALS / "cases.json").read_text())).cases


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the ARIA skill evals (dry run unless --execute).")
    p.add_argument("--execute", action="store_true", help="actually call `claude -p`")
    p.add_argument("--runs", type=int, default=1, help="runs per case per arm (default 1)")
    p.add_argument("--cases", default="", help="comma-separated case ids (default: all)")
    p.add_argument("--max-budget-usd", type=float, default=0.75, help="cap per agent run")
    p.add_argument("--judge-budget-usd", type=float, default=0.25, help="cap per judge call")
    p.add_argument("--max-total-usd", type=float, default=10.0, help="stop starting new runs past this recorded cost")
    p.add_argument("--probe", action="store_true", help="with --execute: two tiny isolation/skill-visibility calls only")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases = load_cases()
    if args.cases:
        wanted = {c.strip() for c in args.cases.split(",") if c.strip()}
        unknown = wanted - {c.id for c in cases}
        if unknown:
            raise HarnessError(f"unknown case ids: {sorted(unknown)}")
        cases = [c for c in cases if c.id in wanted]
    done = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, timeout=60)
    auth = parse_auth_status(done.stdout)
    plan = build_plan(cases, args.runs, args.max_budget_usd, args.judge_budget_usd)
    server_version = read_server_version((REPO / "pyproject.toml").read_text())
    server_file = resolve_server_file()
    in_tree = server_in_working_tree(server_file, REPO)
    print(render_plan(plan, auth, args.execute, server_version, server_file, in_tree))
    if not args.execute:
        print("Dry run: nothing was called. Re-run with --execute to run.")
        return 0
    check_auth_allows_isolated_runs(auth, dict(os.environ))
    if not in_tree:
        raise HarnessError(
            f"refusing to run: the live server imports {server_file}, not this working tree ({REPO})"
        )
    if args.probe:
        return run_probe()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = RESULTS_DIR / stamp
    records: list[RunRecord] = []
    total, stopped = 0.0, False
    for case in cases:
        for idx in range(1, args.runs + 1):
            for arm in ARMS:
                if total >= args.max_total_usd:
                    stopped = True
                    break
                rec = execute_one(case, arm, idx, out_dir, args.max_budget_usd, args.judge_budget_usd)
                records.append(rec)
                total += rec.cost_usd
                status = "ERROR" if rec.error else ("pass" if rec.score and rec.score.passed else "FAIL")
                print(f"{case.id} {arm} run{idx}: {status}  ${rec.cost_usd:.3f}  (total ${total:.2f})")
            if stopped:
                break
        if stopped:
            break
    label = ("smoke test, N=1" if args.runs == 1 else f"N={args.runs} per case") + (
        f" (cases: {','.join(c.id for c in cases)})" if args.cases else ""
    ) + (" - STOPPED EARLY: total cost cap reached" if stopped else "")
    report = build_report(records, label, auth.method, dt.date.today().isoformat(), server_version)
    _write(out_dir / "summary.json", report.model_dump_json(indent=2))
    _write(RESULTS_MD, render_results_md(report))
    print(f"Wrote {out_dir}/summary.json and {RESULTS_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
