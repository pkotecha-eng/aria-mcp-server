# ARIA skill evals

Does the `aria-clinical-research` skill make an agent use ARIA's tools better? Each case is run in two arms and scored on tool choice, identifiers, honest handling of gaps, and tool-call count.

For the latest published results, see [RESULTS.md](RESULTS.md).

## Arms

| Arm | Setup |
|---|---|
| `mcp_only` | ARIA MCP server only, no skill |
| `mcp_plus_skill` | Same MCP server, plus the skill from `skills/aria-clinical-research/` |

Only the skill differs. Both arms use the same agent model, the same strict MCP config (no other servers), no user memory or settings, and a fresh working directory. This is why `claude plugin eval` is not used: its baseline arm removes the whole plugin, including the MCP server.

## Cases

`cases.json`, validated by `score.CaseFile`. Ten cases: literature, US/global registry, UK registry, a multi-step review, a truncated exclusion list, an `N/A` exclusion list, two "insufficient data" cases with fabricated drugs, a paper-to-trial cross-reference, and a request for an exhaustive list.

Cases 5 and 6 use recorded tool responses (`fixtures/`) served by `stub_server.py`, so truncation and `N/A` are guaranteed. The other eight use live ARIA calls against PubMed, ClinicalTrials.gov and ISRCTN.

## Scoring

| Dimension | How | Where |
|---|---|---|
| Tool choice | Expected tools called, forbidden tools not, required arguments used (defaults applied) | `score_tools` |
| Identifiers | Every PMID/NCT/ISRCTN in the answer must appear in that run's own tool output; pinned IDs must be cited; citation count within bounds | `score_ids` |
| Gap honesty | LLM judge rules on each `answer_must` / `answer_must_not` criterion; an unruled criterion counts as failed | `score_gaps` |
| Tool calls | Count of ARIA tool calls; reported, not gated | `score_run` |

A run passes only if tool choice, identifiers and gap honesty all pass.

## Judge

- **Runs on every agent run**: every case has answer criteria, so each agent run gets one judge call.
- **Model**: `claude-opus-5-5`, pinned with `--model` and recorded in every result file. Agent runs use `claude-sonnet-5-5`, also pinned. The judge is a different model from the agent.
- **Blind to arm**: `build_judge_prompt` receives the question, tool output, answer and criteria, and nothing about which arm produced the answer. The judge also runs in its own empty config directory and its own empty work directory, so it never sees the skill or any `CLAUDE.md`.
- **Rationales are saved**: every criterion verdict includes a rationale, stored in `results/<run>/<case>/<arm>/judge.json` for spot-checking. The summary lists runs where the judge passed an insufficiency case but the answer has no insufficiency wording (`marker_disagrees`).

## Cost and call volume

Restated whenever the plan changes.

| Run | Agent runs | Judge calls | Total `claude -p` calls | Live ARIA tool calls (est.) |
|---|---|---|---|---|
| Smoke test, N=1 | 10 cases x 2 arms = **20** | **20** | **40** | 16 live agent runs x ~4-8 = ~64-128 |
| Full, N=3 (only after smoke results are reviewed) | **60** | **60** | **120** | 48 live agent runs x ~4-8 = ~192-384 |

- Cases 5 and 6 (4 agent runs in the smoke test) hit the stub, not the live registries.
- Each PubMed tool call makes two upstream requests (search, then fetch); NCBI allows about 3 requests per second without a key, so runs go one at a time.
- Dollar cost is not estimated here. The harness records the cost reported for each run, enforces `--max-budget-usd` per run, and prints the running total.

## Authentication and billing

`run_evals.py` checks `claude auth status` before anything runs and prints the auth method and API key source. If it reports an API key (`ANTHROPIC_API_KEY`), runs are billed to that key; if it reports a subscription login, runs use the subscription. The harness refuses to proceed unless you pass `--execute`, and prints the auth source, call counts and model names first.

## Running

1. `python -m evals.run_evals` (no flags) is a **dry run**: validates `cases.json`, prints arms, N, models, call counts and the auth source, then exits without calling anything.
2. `python -m evals.run_evals --execute --runs 1` runs the smoke test.
3. Review `results/<run>/summary.json` and the judge rationales. Only then consider `--runs 3`.

## Results

- **Raw results** (transcripts, extracted tool calls, answers, judge verdicts, per-run scores) go under `evals/results/`, which is git-ignored.
- **`RESULTS.md`** is the short summary that is committed. After each `--execute` run the harness rewrites it with: date, run label (smoke N=1 or full N=3), agent and judge models, ARIA server version (read from `pyproject.toml` in the working tree; the harness verifies the live server imports from this tree), per-arm pass rates (overall, tool choice, identifiers, gap honesty), invented-ID run counts, mean tool calls, and notable failures (case, arm, which check failed). Earlier versions are in git history. The harness writes the file; committing it is up to you.

## Tests

`pytest evals/tests` covers the scoring functions, including the fail-closed paths (invented IDs, unruled criteria, unknown ARIA tool names).
