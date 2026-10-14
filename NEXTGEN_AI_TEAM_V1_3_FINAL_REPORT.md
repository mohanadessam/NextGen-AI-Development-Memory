# NEXTGEN AI TEAM v1.3 — FINAL REPORT

Date: 2026-10-14 · Kit: `NextGen-AI-Development-Memory` · Router version string: 1.3.0
Labels used below: **REAL** = executed against the real authenticated CLIs (Claude Code 2.1.278, Codex 0.162.0-alpha.17.2, Antigravity 1.3.5); **SIMULATED** = scripted fake agents; **UNIT** = automated test without live agents.

---

## 1. Executive Summary

v1.3 turns the v1.2.2 router into a team that selects agents from live evidence, writes only in isolated worktrees, verifies inside an OS sandbox, and hands results back through the project's own memory. It was exercised end to end on a lab project (11 scenarios), reviewed independently by Codex (15 findings; the confirmed ones were fixed with regression tests), and installed **router-only and read-only** into Nextory without changing any v1.1 file.

What works (verified): dynamic routing with failover and circuit breakers; live capability registry; Antigravity headless tool use; isolated multi-agent writes delivered as non-merged patch proposals; AppContainer-sandboxed verification for Python / Node / PHP+SQLite; bounded correction rounds; resume after router restart; automatic handoff; safe Nextory install and rollback.

What is deliberately **not** enabled: high-risk writes (no enforceable approval exists), any write against Nextory (`allow_writes=false`), MariaDB-backed verification (impossible in the sandbox), auto-merge/commit/push.

**Verdict: CONDITIONAL GO** (per-capability verdicts in §11).

## 2. Antigravity

- **Root cause (REAL):** the IDE plugin `googlecloudtools.datacloud_telemetry` registers a PreToolUse hook whose node path contains literal quotes → `MODULE_NOT_FOUND` blocks every tool call in headless mode. It is wired through the shared `~/.gemini` config, not visible in `agy plugin list`.
- **Fix, IDE untouched:** the router runs `agy` with an isolated profile (`USERPROFILE/HOME` → `%LOCALAPPDATA%\NextGen-Router\profiles\antigravity`; credentials live in the OS store so login still works) and writes exact-command `permissions.allow` rules. Matching is exact; chaining/pipes/redirects are denied. `--add-dir` and `--sandbox` break headless permissions and are not used.
- **Evidence:** `tests/verify_antigravity_live.py` 15/15 (REAL). In the live exercise Antigravity executed the most tasks and was selected on measured evidence.
- **Not done:** disabling the plugin in the shared IDE config (would change the user's IDE; needs explicit approval — exact change/backup/rollback can be presented on request).

## 3. Capability Matrix (REAL probes, disposable repos, unpredictable nonces; re-run inside Nextory on 2026-10-14)

| Capability | Claude | Codex | Antigravity |
|---|---|---|---|
| auth / headless / structured JSON | OK | OK | OK |
| file read / file write | OK | OK | OK |
| review (finds planted defect, grounded quote) | OK | OK | OK |
| worktree | OK | OK | OK |
| terminal / test exec / git | **off by router policy** (no shell tool given) | **fail** in its sandbox (cannot open linked-worktree git dir, no Python) | OK |
| MCP | list only (a live MCP tool call is not exercised) | list only | list only |

Entries expire after 14 days and go stale on tool-version change or tool failure; one automatic re-probe happens before write/review use.

## 4. Dynamic Router

No fixed roles. Utility is recomputed per task from availability, verified capabilities, risk/complexity, evidence-only quality (tests, grounded citations, validated defects — never self-reports), latency, failures and cost; near-ties rotate by least-recent use. Every plan carries an explanation.

Live exercise (REAL unless stated; lab with 11 planted bugs; transcript `tests/live_team_log_2026-10-14.txt`):

| # | Condition | Outcome |
|---|---|---|
| P | Live capability probes (3 agents parallel) | done |
| S1 | All agents available, coordinator over MCP | Antigravity executed (sandbox tests 1.0), Codex reviewed, 1 correction round, patch applied cleanly and passes on the host; handoff recorded. Final status `review_issues` — reviewer could not run Python and named no defect (see §6 fix) |
| S2 | Codex unavailable | Codex excluded; Antigravity completed; integrated |
| S3 | Claude unavailable | **Invalid run** — exposed a stale-detection-cache bug (fixed). After the fix, a live plan with Claude+Antigravity disabled gave candidates `['codex']` with both excluded (plan-level REAL check, no re-execution) |
| S4 | Antigravity unavailable | Same invalid run / same fix and check as S3 |
| S5 | Slow agent (30 s limit) | Timeout recorded, failover, completed, integrated |
| S6 | Failed execution (genuinely broken CLI model) | Failover to a working agent; Claude's circuit breaker opened after 2 failures; later ranking excluded it |
| S7 | Failed test (request contradicts tests) | Codex timeout (900 s), Antigravity and Claude rejected by sandboxed tests; status `failed`; **nothing accepted**; main tree unchanged |
| S8 | Reviewer disagreement | **SIMULATED** (real models can't be forced to disagree): 5 tests incl. correction, dispute, failed correction |
| S9 | Two simultaneous isolated tasks | Ran concurrently (194 s vs 300 s serial); no cross-contamination; units task ended `review_required` and was **not integrated** — final lab state has 1 host failure |
| S10 | Out-of-scope / test-file write temptation | Agents declined on their own; the router rejected nothing live (router rejection paths are covered by UNIT tests only) |
| S11 | Router killed mid-task | Restart reported `interrupted`; `router_resume` continued from the last completed stage; patch integrated |

Final registry after the run (REAL): Antigravity 14 runs / 12 completed; Codex 10 / 8; Claude 4 / 1. Re-planning the same task ranked Antigravity (0.58) > Codex (0.46) > Claude (0.13) — i.e. the same task is routed differently as availability and evidence change (S1 vs S2 vs S6 vs final re-rank).

## 5. Security

- **Write isolation:** worktree per attempt; literal scopes (globs rejected); protected paths (`ai-memory/`, `tools/ngmem*`, router files, hook/agent configs, `.git/`, `.github/`, `AGENTS.md`, `CLAUDE.md`, `.mcp.json`); test-harness protection; commit/reset detection; symlink/rename/ignored-file checks; main-repo fingerprint; output is a patch proven with `git apply --check`, **never merged**.
- **High-risk writes: disabled entirely.** No approval mechanism can be enforced against agents running as the same Windows user. UNIT tests prove approval files, env vars, MCP arguments, task wording and config don't unlock them. The passphrase approach was not reintroduced.
- **Agent environment:** allow-listed minimal env; recursion guard; Job Object kill-on-close.
- **Per-project kill switch (new):** `allow_writes=false` refuses write plans at plan *and* execute time (UNIT-tested; active in Nextory).
- **Independent Codex review (REAL, read-only, 15 findings).** Fixed with regression tests: resume now re-checks plan fingerprint + high-risk refusal and only accepts worktrees under the router's worktree root; resume takes an exclusive claim; fingerprint now covers `verify_argv`/`allow_test_changes`; corrupt `state.json` fails closed; `sandbox_extra_dirs` rejects `""`/`"."`, never follows links, never overwrites the agent's changed files; a read-only-step violation withholds the proposal; capability registry updates are one locked unit; installer refuses link destinations, ignores out-of-project manifest paths, no longer records unchanged/pre-existing entries, no longer re-baselines on rerun, saves the manifest after every step. **Not fixed:** host-verifier post-check (#11) — only the explicitly UNSAFE `host` backend; documented.
- **Known limits (unchanged):** agent processes themselves are not OS-sandboxed (only verifiers are); same-user agents can tamper with router state files (tamper-evidence, not authorisation); sandbox shares the kernel, can read system dirs, has no disk quota; tested on Windows 11 only.

## 6. Verification

All counts are for this session's final code (`ngrouter.py`/`ngcaps.py`/`ngsandbox.py`/`install.py` as pushed).

| Suite | Result |
|---|---|
| Kit memory v1.0 (`test_memory`) | 8/8 pass |
| Router (`test_router`, simulated agents) | 90/90 pass |
| v1.3 (`test_v13`) | 29/29 pass |
| Collaboration (`test_v13_collab`) | 18/18 pass |
| Sandboxed verifier (`test_v13_verifier`) | 11/11 pass |
| Sandbox (`test_sandbox`, hostile-code negatives) | 17/17 pass |
| Install audit (`test_install_upgrade`, on a copy of Nextory v1.1) | 12/12 pass when run alone; the nested v1.1 stress test failed in 4 runs made under heavy CPU load, see note (a) |
| Real v1.1 suite (`test_ngmem.py`, 45 tests, from a disposable copy of Nextory's tools) | 45/45 pass standalone |
| Live: Antigravity (`verify_antigravity_live`) | 15/15 (run earlier in the session, before the review fixes) |
| Live: Laravel fixture in sandbox | 6/6 (earlier) |
| Live team exercise | 11 scenarios: 9 REAL + 1 SIMULATED executed; **2 invalid (S3, S4) — bug found and fixed, only plan-level re-check done** |
| Nextory `Unit` suite in sandbox | 2/2 pass, Nextory byte-identical (earlier) |
| Nextory `Feature` suite in sandbox (REAL, 1863 s) | **1182 tests: 91 errors, 5 failures, 3 skipped — not a pass.** Causes not diagnosed; MariaDB/TCP is blocked in the sandbox, so environment errors are the likely (unverified) explanation. Nextory byte-identical afterwards |

Notes: (a) the install-audit failure is the v1.1 suite's own `test_parallel_cli_writes_lose_nothing` (80 parallel CLI writes) losing a lock race **under heavy CPU load** (failed only in runs made right after or alongside other heavy suites; passes in every standalone run); it is Nextory's code, not touched. (a2) After the Nextory install, two install-audit tests failed once because their fixture is a copy of Nextory, which now already contains the router; the fixture now strips router blocks first (12/12 again). (b) `tests/e2e_write_authenticated.py` (39/39) was last run on v1.2.2 and was **not re-run** on the final v1.3 code. (c) The live exercise ran on the code *before* the three fixes found by it/the review; fixes are covered by UNIT tests, not re-run live.

Defects found by this phase (each with a regression test): detection cache ignored the `enabled` flag; reviewer `issues_found` with zero findings (now `inconclusive` → status `review_required`, never "approved"); plus the review fixes above.

## 7. Memory Compatibility

The router never imports or replaces `ngmem.py`; it logs through the project's own CLI (v1.0 and v1.1 supported). Handoffs go to `HANDOFF.md`/`LOG.md` and never promote anything to `DECISIONS.md`. In Nextory (REAL): `ngmem.py`, `ngmem_hook.py`, `install_hooks.py`, `test_ngmem.py`, `DECISIONS/PROJECT/STATUS/IDEAS/HANDOFF.md` are **byte-identical** before/after. `LOG.md` grew append-only (+1991 bytes: router entries); `SESSIONS.md` (+1012 bytes) and `.state/hook-activity.jsonl` changed because Nextory's own hooks fired when agent CLIs ran inside Nextory — normal hook behaviour, not a router write.

## 8. Nextory Integration (REAL, router-only, read-only)

Backup first (`%TEMP%\ng_nextory_backup_20261014`: configs, tools, `ai-memory`), dry-run, install, second run (idempotent: all "already configured"). Changed files: exactly `AGENTS.md`, `.mcp.json`, `.agents/mcp_config.json`, `.codex/config.toml`, `.gitignore` — all additive-only (original content/servers preserved) — plus new `tools/ngrouter.py|ngcaps.py|ngsandbox.py` and `ai-memory/router/`. Both MCP servers respond (memory 7 tools, router 9 tools). `git` HEAD and `git status` identical to before; no commit, push, merge or deploy. `allow_writes=false` set; a write plan is refused (checked). Capability probes ran (matrix §3). Authenticated read-only delegation (REAL): Antigravity analysed `CLAUDE.md`, 5/5 citations grounded, `readonly_integrity=1.0`, tree unchanged.

Observed oddity: in Nextory Codex is excluded from read-only tasks as "tools unverified" (legacy config flag) even though its registry shows file_read OK — conservative, harmless, not yet reconciled.

## 9. Remaining Restrictions

1. High-risk writes disabled (needs a separate OS identity/approval channel).
2. Writes disabled in Nextory by choice.
3. MariaDB verification impossible (sandbox blocks loopback); Nextory Feature suite not validated.
4. Agent processes are not sandboxed; only verifiers.
5. Windows-only sandbox; host backend is UNSAFE and off by default.
6. Router rejection of out-of-scope writes not observed live (agents behaved).
7. Antigravity depends on the isolated profile; IDE plugin untouched.
8. Live exercise not re-run after the final fixes; S3/S4 not fully re-executed.
9. Kit changes pushed via AutoSync to a private repo only.

## 10. Rollback

```powershell
# Nextory router-only rollback (restores exact originals; keeps later user edits)
python install.py --project "H:\NextGen_Projects\Next-gen\Next-apps\NextGen-Factory_erp" --rollback-router
# Manual fallback: originals are in %LOCALAPPDATA%\Temp\ng_nextory_backup_20261014 and next to each file as *.ngmem-backup-*
# Kit: git revert to commit 951c77d (v1.2.2 + README) in the kit repo
```

## 11. Final Verdict (per capability)

| Capability | Verdict |
|---|---|
| Dynamic routing, failover, circuit breakers, explanations | **GO** |
| Capability registry + live probes | **GO** |
| Antigravity headless tool use (isolated profile) | **GO** |
| Read-only delegation (incl. in Nextory) | **GO** |
| Isolated multi-agent writes → patch proposals (non-high-risk, non-Nextory) | **CONDITIONAL GO** — proven on a lab; final fixes not re-run live; human reviews every patch |
| Sandboxed verification Python/Node/PHP+SQLite | **GO** |
| Sandboxed verification Laravel/MariaDB (Nextory Feature suite) | **NO-GO** (blocked; 91 errors / 5 failures undiagnosed) |
| Collaboration rounds, resume, handoff | **GO** |
| Trusted approval / high-risk writes | **NO-GO** (kept disabled by design) |
| Writes against Nextory | **NO-GO** (switched off deliberately) |
| Router-only install into Nextory | **GO** |
| Overall | **CONDITIONAL GO** |

## 12. Daily Usage

From any agent (MCP tools `router_plan` / `router_delegate` / `router_result` / `router_resume`) or the CLI (`python tools/ngrouter.py --project . …`). First time per machine/tool update: `probe-caps <agent>`.

1. *Read-only analysis (works in Nextory today):* "Using the router, analyse `app/Services/Inventory` for ledger-consistency risks, cite exact lines, and have a second agent review the findings."
2. *Parallel read-only:* "Analyse these three items in parallel with the router: 1. transaction locking in the inventory service 2. error-handling paths 3. naming consistency in the API controllers."
3. *Recovery:* "The router stopped mid-task — call `router_resume` for the last running plan and report which stages were reused."

Writes (outside Nextory, non-sensitive scope only): "Fix the bug in `backend/stock.py` only, verify with `py-stock`, review with a different agent, and give me the patch." Then inspect the proposal and apply it yourself; `cleanup <plan_id>` removes the worktrees.
