> **SUPERSEDED by FINAL_RELEASE_GATE.md (2026-10-09).** Parts of this earlier report are wrong or obsolete: it called the kit's template 'v1.1' (it is v1.0; the real v1.1 is in Nextory's tools/), it described an approval mechanism that has since been removed (high-risk writes are now disabled), and its test counts omit the real 45-test v1.1 suite. Do not use it for the release decision.

# NextGen AI Team v1.2 — Implementation Report (2026-10-09)

## What was built
| File | Change |
|---|---|
| `template/tools/ngrouter.py` | New. Smart Agent Router (stdlib only): analysis, ranking, planning, delegation, failover, registry, worktrees, async jobs. |
| `template/tools/ngmem.py` | Additive: loads the router if present, adds 8 `router_*` MCP tools and a `router` CLI subcommand, server version 1.2.0. The 7 v1.1 tools are untouched. Without `ngrouter.py` it behaves exactly as v1.1. |
| `install.py` | Copies `ngrouter.py`; new `--upgrade` (backs up replaced files); appends a separate router section to AGENTS.md; router git-ignores. |
| `template/AGENTS_ROUTER.md` | Router rules for all three agents. |
| `tests/test_router.py`, `tests/e2e_authenticated.py`, `tests/e2e_transcript_2026-10-09.txt` | Simulated suite (43 tests), authenticated E2E driver, and its transcript. |

## Installed tools (inspected)
| Agent | CLI | Non-interactive interface used | Auth |
|---|---|---|---|
| Claude Code 2.1.278 | `claude` | `-p --output-format json --permission-mode plan` (read-only) / `acceptEdits` (worktree); prompt on stdin | `claude auth status`: claude.ai subscription |
| Codex 0.160.1 | not on PATH; found under `%LOCALAPPDATA%\OpenAI\Codex\bin\*\codex.exe` | `exec --json --ephemeral --sandbox read-only / workspace-write -`; prompt on stdin | `codex login status`: ChatGPT login |
| Antigravity `agy` 1.3.2 | `~/.gemini/bin/agy.exe` | `-p=<prompt> --output-format json --mode plan / accept-edits` (prompt is argument-only) | `agy models` succeeds |

No API keys are used; `ANTHROPIC/OPENAI/CODEX/GEMINI/GOOGLE_API_KEY` are stripped from child environments.

## How the requirements are met
1. **Claude coordinates, may delegate completely**: `coordinator` is a config/parameter; Claude can also be chosen as executor.
2. **Analysis, decomposition only when useful**: class/complexity/risk heuristics; parallel only for 2 or more independent subtasks.
3. **Selection inputs**: utility = quality, completion, latency, cost, failure penalty, plus a small exploration bonus (only for agents with almost no evidence, low risk). Weights shift with risk. Near ties go to the least recently used agent, so no agent becomes a permanent default.
4. **Registry**: `ai-memory/router/events.jsonl` holds runs (outcome, latency, tokens, attempt/retries) and evidence (tests, citation grounding, read-only integrity, scope, validated/rejected findings, missed defects).
5. **No self-evaluation as evidence**: accepted sources are router-observed facts, test runs, reproductions, user, and coordinator inspection (half weight). `self_confidence` is stored but never scored; `router_record_evidence` rejects self-report sources.
6. **Unavailability and limits**: installed + authenticated detection, cooldowns from limit messages (a reset time is used only if the CLI printed one; otherwise a flagged conservative backoff), circuit breaker, tool-failure detection. Remaining quota is never guessed (none of the CLIs expose it).
7. **Single / sequential / parallel**: all implemented.
8. **Proportional review**: read-only low/medium: none; write medium/high or read-only high: one independent reviewer.
9. **Shared memory**: delegated prompts embed a memory excerpt and search hits; the router logs dispatch/result/approval to LOG.md; full results in `ai-memory/router/runs/`.
10. **Worktrees and approval**: every write task runs in its own `git worktree` branch (`ngr/...`) with declared, disjoint, locked scopes; high-risk writes need `router_approve` with the user's own words (process control, not a sandbox).
11. **Loop control**: `NGROUTER_DEPTH` blocks recursive delegation; a plan runs once; at most 3 attempts per slot and 6 per task, never the same agent twice per slot; same-task hourly cap.
12. **Coordinator is not the final reviewer**: reviewer is never the executor or the coordinator. If none is eligible, the plan says so, requires approval for writes, and reports "independent review REQUIRED BUT NOT PERFORMED".
13. **Manual override**: `agent` / `reviewer` parameters, ignored with a warning if the agent is unavailable.
14. **v1.1 preserved**: the 8 original tests pass; the only edit is the tool-count assertion, relaxed to "all 7 original tools still present". Memory files, MCP configs, secret guard, locking and doc whitelist are untouched; upgrade replaces only tool scripts (backed up). The kit contains no hooks and `~/.claude/settings.json` has none; nothing there was changed.

## Test results
- v1.1 suite: 8/8. Router simulated suite: 43/43 (rotation, evidence- and availability-driven reassignment, self-report immunity, reviewer independence, rate-limit/auth/timeout/garbage failover, breaker, recursion block, retry caps, read-only tamper detection, worktree isolation, scope locks, approval gate, MCP surface, background jobs, secret redaction).
- Authenticated E2E with real CLIs on subscriptions: `tests/e2e_transcript_2026-10-09.txt`.

## Demonstration: the same task, different agents
Task: review `inventory.py` (3 planted bugs, each reproduced by a failing project test).

| State | Condition | Router picked | Why (from the transcript) |
|---|---|---|---|
| A | all healthy; calibration: all three found 3/3 real bugs, 3/3 citations grounded | **codex** | equal verified quality, lowest measured latency (22.6s vs 30.5s vs 51.2s) |
| B | codex made to fail for real (CLI pointed at a nonexistent model) | codex failed, **claude** completed | automatic failover (2 attempts) |
| B' | same task re-planned | **claude** | codex failure history: completion 0.70, penalty 0.10, utility 0.21 |
| C' | codex fails again | **claude**, codex excluded | circuit breaker open after 2 consecutive failures |
| Synthetic | seeded registry in unit tests | codex, then antigravity, then claude | codex best on evidence; codex unavailable; claude later proves itself while codex gets validated defects |

Write path: the router chose claude to implement (class debug, risk medium). The diff was produced in an isolated worktree (main tree stayed unchanged), the router ran the tests itself (rc 0), and **antigravity** reviewed it (neither the executor nor the coordinator). Approval gate: a high-risk write was refused pending `router_approve`. MCP: plan, background delegation and result polling worked over the real stdio protocol.

## Caveats
- On this easy task all three agents scored equally on quality, so the real-agent reassignments came from latency and an **injected** failure. A purely quality-driven switch is demonstrated only with the synthetic registry. The codex failures in the registry transcript were deliberate.
- Defects found by the E2E phase and fixed with regression tests: the router's own memory log tripped the read-only check; Antigravity could not use tools; the classifier misread "Fix the bugs"; a reviewer was wrongly charged with a missed defect; scope parsing clipped filenames; grounding was applied to write tasks. The last two appear in the saved transcript (`grounding 0/3`, `OUT OF SCOPE: nventory.py`) and were fixed afterwards; the write phase was not re-run against real agents.
- **Antigravity install problem (not modified by me):** its pre-tool hook fails with `MODULE_NOT_FOUND` (googlecloudtools.datacloud_telemetry), so `agy` cannot use any tool headlessly. The router works around it by inlining small listed files into read-only prompts and records the fault as `tool_failure` (1-hour cooldown). Antigravity as a write executor will not work until that hook is repaired.
- Keyword-based classification and risk are crude; the coordinator can pass `task_class` and `risk_hint`. Approval is attested, not authenticated.
- Nextory was not touched. The E2E used a throw-away repository in the scratchpad (commits exist only there). The kit itself is not a Git repo; nothing was committed, pushed or deployed.

## Using it
Upgrade a project: `python install.py --project <repo> --upgrade`, then restart the AI tools. Use `router_plan` / `router_delegate` over MCP, or `python tools/ngmem.py router plan '{"task":"..."}'`. After tests or user review, call `router_record_evidence` so the registry learns from facts.
