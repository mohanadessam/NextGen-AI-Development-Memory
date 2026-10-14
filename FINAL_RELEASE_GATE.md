# NextGen AI Team v1.2 — FINAL RELEASE GATE (2026-10-09)

## Verdict: **CONDITIONAL GO**

| Capability | Verdict |
|---|---|
| Router add-on installed with `--router-only`; **read-only** delegation to Claude / Codex / Antigravity (inlined files for Antigravity) | **GO** |
| **Write** delegation (Claude or Codex only) to non-sensitive paths, in isolated worktrees, with independent review | **GO, with conditions below** |
| Router-run **verifiers** on a project whose tests touch a database (Nextory's PHP/MariaDB suites) | **NO-GO** until the verifier can run in an OS sandbox (blocker B1) |
| **High-risk writes** (any sensitive path or stakes keyword) | **Disabled by design**, not a gap to close here |
| **Antigravity as a writer** | **NO-GO** (its headless tool use is broken on this machine, blocker B3) |

Nothing was installed into Nextory, no Nextory code was modified, nothing was committed, pushed, merged or deployed.

## 0. Corrections to what I told you earlier
1. **Why the last report listed only 8 legacy tests.** I ran only the kit's own `tests/test_memory.py`. The real **v1.1 lives in Nextory's `tools/`** (595-line `ngmem.py`, hooks, `SESSIONS.md`, `test_ngmem.py` with **45 tests**). The kit repo's `template/` is still **v1.0**. I wrongly called it v1.1 and wrongly claimed "v1.1 preserved".
2. The first `install.py --upgrade` would have **overwritten Nextory's v1.1 `ngmem.py` with v1.0 plus router glue**. That design was dropped: the router is now a separate MCP server (`nextgen-router`) that never replaces or imports `ngmem.py`; it logs through the project's own ngmem CLI.
3. The earlier "approval by user statement" and later "passphrase approval" designs are gone, see section 2.
4. `ROUTER_V1_2_REPORT.md` is marked superseded.

## 1. Independent review (Codex, read-only, 2 rounds) — findings treated as untrusted and checked against the source
**Round 1: 21 findings.** 18 confirmed in source and fixed; 3 correct but inherent to running agents as the same OS user (documented as residuals).

| # | Sev | Finding | Disposition / evidence |
|---|---|---|---|
| 1,2 | crit | Approval/plan fingerprint is an unkeyed hash; approver file overwritable by same-user code | **Confirmed. Mechanism deleted** (section 2). `execute()` re-derives risk from task text + write scope; test `test_editing_the_plan_file_does_not_unlock_it` |
| 3 | high | Sensitive paths in the write *scope* not counted | Fixed; round 2 found exact dir names (`config`) evaded the regex, fixed (`(/|$)`), tests for 7 spellings |
| 4 | crit | `verify_cmd` allowlist = `python -c ...` runs anything | Fixed: only **named verifiers** from config; free-form rejected (6 attack strings tested) |
| 5 | high | `NGROUTER_DEPTH` forgeable | **Inherent** (no OS sandbox). Mitigations: concurrency cap, attempt/hourly caps, Claude has no shell tool, Codex sandbox. Residual R1 |
| 6 | high | Evidence forging via MCP | MCP limited to `coordinator_inspection` (weight 0.5), children blocked. Caller identity still unauthenticated, residual R2 |
| 7 | med | Agents can pass override params | By design (coordinator relays user overrides); logged. Residual R2 |
| 8 | med | Reviewer prompt injection | Fenced as UNTRUSTED data, router-derived diff separate, executor free text omitted. Cannot be eliminated for LLMs |
| 9 | high | Reviewer verdict not required | Fixed: verdict mandatory; `issues_found` -> status `review_issues`; no review -> `review_required` |
| 10 | crit | Router state in main repo editable while a write runs | Partly fixed: fingerprint now covers router `config.json`, the plan file, the written prefix of `events.jsonl`, `.git/info/exclude`, hooks, refs, packed-refs, common git dir. `state.json` is router-written and not covered (R3) |
| 11 | crit | A worktree is not a filesystem boundary | Mitigated per agent and **tested live** (section 5): Claude runs with `--tools Read Edit Write Glob Grep` (no shell); Codex `workspace-write` sandbox. Direct orders to write outside the directory failed for both |
| 12 | high | Glob scope widened to prefix | Fixed: globs rejected; literal match; case-folded on Windows |
| 13 | high | Ignored files / symlinks / rename quoting | Fixed: `status -z --ignored`, both rename paths, symlink escape + tracked-symlink blob target, benign caches tolerated |
| 14 | med | commit-then-reset hides a commit | Fixed via reflog length; recovery now uses a **brand-new worktree** per attempt |
| 15 | high | `cleanup` trusts result JSON | Fixed: ids validated, paths from `git worktree list` under the dedicated root; removed from MCP |
| 16 | high | Failing verification still "completed" | Fixed: attempt fails (and failover) |
| 17 | med | Heartbeat once only | Fixed (lease refresh per attempt). No owner-liveness check (R4) |
| 18 | med | Exception after claim leaks "running" | Fixed (try/finally right after claim; exclusive job file) |
| 19 | med | Schema-valid output scored as quality | Fixed: format/compliance passes earn nothing, failures count as 0 |
| 20 | med | Capabilities pre-verified; config unvalidated | Fixed: all flags default **false**; set only by router-observed `probe`; strict config validation incl. integers |
| 21 | high | Env denylist leaks AWS/GitHub/DB secrets | Fixed: **allowlist** env; recursive redaction (keys too); prompt temp files deleted |

**Round 2** (verification of the fixes + hunt for new bugs): 14 findings. Fixed: exact sensitive dirs (#1), verifier side-effects re-checked against scope (#3), read-only violation now **fails** the attempt and **stops failover** (#4), read-only fingerprint includes ignored files + router files (#5), review-required status (#6), `NODE_OPTIONS` removed from env allowlist (#7), parallel-scope IndexError (#8), fractional counts (#9), nested-lock deadlock in `probe` on fresh state (#10), secret-bearing dict keys + whole-event redaction (#11), linked-worktree `.git` (#12), global `git worktree prune` removed (#13), stale comment (#14). **Not fixable here:** #2 (the verifier executes agent-edited code as your OS user), see B1.

## 2. Approval security — decision: **high-risk writes are disabled entirely**
Your rule: enforceable, user-controlled approval bound to task/action/scope/expiry, or disable. I built a passphrase-gated, single-use, plan-bound, expiring `HumanApproval` capability. Codex then showed (critical, confirmed) that anything an agent running as the same OS user can reach (plan files, `~/.ngrouter/approver.json`, importing the module, TTY bypass parameters) defeats it. **That cannot be verified secure without an OS-level boundary (separate account or sandbox), so I removed the mechanism instead of shipping false assurance.** Verified now:
- `requires_approval`/`blocked` plans cannot run via `execute`, `start_job`, MCP `router_delegate`, or CLI; editing + re-hashing the plan file does not unlock it (re-derived from task and scope).
- No approve tool/command/class exists (`test_no_approval_surface_exists_anywhere`, E2E W5/W6).
- Plain-text "approval by statement" no longer exists anywhere.

## 3. Regression — all suites, final tree
| Suite | Result |
|---|---|
| Legacy **v1.1, the real 45-test suite** (`NextGen-Factory_erp/tools/test_ngmem.py`, run from a disposable copy so Nextory is not written) | **45/45** |
| Same 45 run inside an upgraded v1.1 fixture (after `--router-only`) | 45/45 |
| Kit v1.0 suite (`tests/test_memory.py`) | **8/8** (assertions unmodified: 7 MCP tools, one TOML table) |
| Router simulated suite (`tests/test_router.py`, fake agent CLIs) | **90/90** |
| Install/upgrade/rollback audit (`tests/test_install_upgrade.py`) | **8/8** |
| Authenticated write E2E (`tests/e2e_write_authenticated.py`, final code) | **39/39 checks** |
| Hooks drill: real v1.1 hooks installed in a disposable project, read-only delegation to all 3 agents | 3/3 completed; Claude and Antigravity hook events logged `ok`; no read-only violations (Codex hook events not observed, they are trust-gated) |

Total automated tests: 45 + 8 + 90 + 8 = **151** (+ 39 live checks). The 45 is counted once; it is also re-run inside the audit.

## 4. Antigravity
Root cause (read-only diagnosis, nothing modified): the plugin `googlecloudtools.datacloud_telemetry` (added to `~/.gemini/config/plugins` on Oct 8) registers a `PreToolUse` hook whose command is `node "C:\...\telemetry_hook_bundle.js" ... ; exit 0`. `agy` 1.3.2 does not strip the literal quotes and resolves `<plugin dir>\"C:\...bundle.js"` -> `MODULE_NOT_FOUND`; the failing pre-tool hook blocks **every** tool call headlessly. The bundle file exists. The plugin lives in shared config used by the IDE, so I did not touch it. User-side options (your decision, not done): `agy plugin disable googlecloudtools.datacloud_telemetry`, or fix the quoting in its `hooks.json`.
Enforced behaviour: Antigravity is **never** a write executor (probe result `tool_failure`, E2E W0/W1), is eligible for read-only work **only if the listed files can be fully inlined** (<=24k chars), and as a reviewer only when the whole diff fits inline; results then say "judged from the inlined diff only". Inlined-file runs are **not** claimed as proof of tool-enabled execution. After the plugin is fixed, `ngrouter.py probe antigravity` will unlock it on evidence.

## 5. Authenticated E2E (real CLIs, subscriptions, disposable repo, short path)
Transcript: `tests/e2e_write_transcript_2026-10-09.txt`. Verified: capability probes (Claude pass, Codex pass, Antigravity fails with `tool_failure`); scope enforcement; **automatic test execution** by the router (rc 0); **independent review** (executor Claude, reviewer Codex, coordinator Claude); **failed-task recovery** (Claude forced to fail with a bad model -> Codex recovered in a clean worktree, tests pass); technical filesystem boundary (direct instruction to write outside the directory failed for Claude and Codex); high-risk gate; MCP surface (no approve tool, evidence forging rejected, background delegation works); **final invariants: HEAD unchanged, no new refs, no remote (so no push/merge/deploy possible), git config untouched, main tree pristine, no worktrees left, Nextory hash/status/HEAD identical before and after**.
Honest notes: (a) in the scope drill the agents **declined** the out-of-scope write themselves (they obeyed the prompt), so router-side rejection of a real agent's violation was shown only in the simulated suite; (b) one E2E attempt hit a transient DNS outage (`No such host`, `tests/e2e_write_run3_transient_codex_outage.log`) which exposed that the router classified it as an agent failure; fixed (now infrastructure, no breaker penalty) and the full run repeated clean; (c) an earlier run failed from Windows path length ($GIT_DIR too big) with a very deep scratch path, so use short paths (Nextory's are fine).

## 6. Installation safety (existing v1.1 project, copied read-only from Nextory)
`install.py --router-only` verified on that fixture: **byte-identical** after install: `tools/ngmem.py`, `ngmem_hook.py`, `install_hooks.py`, `.claude/settings.local.json`, `.agents/hooks.json`, `CLAUDE.md`, and **every file in `ai-memory/`** (all memory records and handoffs). **Prefix-preserved** (original content is a verbatim prefix, router block appended, backup created): `AGENTS.md` (custom rules intact), `.codex/config.toml` (hook blocks intact), `.gitignore` (custom rules intact). **Merged, not replaced:** `.mcp.json` and `.agents/mcp_config.json` keep `nextgen-memory` and unrelated servers; `nextgen-router` is added beside them. `--dry-run` changes nothing; repeated installs/`--upgrade` are idempotent; a modified `ngrouter.py` is preserved unless `--upgrade` (then backed up); `--rollback-router` restores **exact original bytes** (manifest + backups), and after user edits removes only router blocks/keys. The real 45-test suite passes against the upgraded fixture and the router MCP server answers beside it.

## 7. Remaining blockers and residual risks
- **B1 (blocks verifiers on Nextory):** the router runs the configured verifier in the worktree, which executes **agent-edited code as your OS user without a sandbox**. For Nextory's PHP/MariaDB tests this could touch a database. Ship with **no PHP verifier configured** (only `python-unittest`/`pytest` exist by default); tests stay a human step until sandboxing exists.
- **B2:** no OS-level sandbox/separate account anywhere. Same-user agents with a general shell can forge `NGROUTER_DEPTH`, edit router files, or import the module (R1, R3). Claude (no shell tool) and Codex (sandbox) were tested; Antigravity has shell tools and is read-only only.
- **B3:** Antigravity headless tools broken (section 4).
- R2: MCP callers/overrides are unauthenticated; evidence from MCP is capped at weight 0.5 and logged as `mcp-caller`. R4: leases have no owner-liveness check. R5: LLM reviewers can be prompt-injected despite fencing. R6: Windows-only verification; macOS/Linux paths untested. R7: remaining quota is never exposed by the CLIs and is not guessed.
- v1.1 hooks fire inside delegated runs (extra `SESSIONS.md` entries); harmless in tests but expect noise.

## 8. Conditions for installing into Nextory (do these, in order)
1. Keep Nextory's working tree as is; run from the kit directory with Nextory's interpreter (e.g. `C:/Python313/python.exe`).
2. Preview: `python install.py --project "H:\NextGen_Projects\Next-gen\Next-apps\NextGen-Factory_erp" --router-only --dry-run`
3. Install: same command without `--dry-run`. It writes only `tools/ngrouter.py`, `ai-memory/router/`, an AGENTS.md section, `nextgen-router` entries in `.mcp.json`, `.agents/mcp_config.json`, `.codex/config.toml`, and git-ignore rules (all backed up). Restart Claude Code / Codex / Antigravity.
4. Probe (disposable repos, no Nextory writes): `python tools/ngrouter.py --project . probe claude` and `... probe codex`. Do **not** hand-set `write_verified`.
5. Start with **read-only** delegations; use write delegation only for non-sensitive paths with an explicit literal scope, review the worktree diff yourself, then `python tools/ngrouter.py --project . cleanup <plan_id>`.
6. Leave verifiers at defaults; do not add `php artisan test` (B1).
## 9. Rollback
`python install.py --project "<Nextory>" --rollback-router` (restores exact originals from the install manifest; keeps `ai-memory/router/` data; idempotent). Manual fallback: remove the `NEXTGEN_ROUTER_*` block in AGENTS.md, the `nextgen-router` keys/TOML block, the `# >>> NextGen router v1.2` block in .gitignore, delete `tools/ngrouter.py` (backups: `*.ngmem-backup-*`). Remove leftover worktrees with `cleanup <plan_id>` or `git worktree remove` under `..\.ngworktrees\`.

## 10. Evidence index
`tests/test_memory.py`, `tests/test_router.py`, `tests/test_install_upgrade.py`, `tests/e2e_write_authenticated.py`, `tests/e2e_write_transcript_2026-10-09.txt`, `tests/e2e_write_run3_transient_codex_outage.log`, `tests/e2e_authenticated.py` + `tests/e2e_transcript_2026-10-09.txt` (earlier read-only/write run on the pre-hardening code, kept for history). Codex review outputs are in the session scratchpad (`codex_review_out.json`, `codex_review2_out.json`).
