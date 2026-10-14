# NextGen AI Team v1.3 — progress (WORK IN PROGRESS, honest status)

Published from the local kit by NextGen Git AutoSync. v1.2.2 (`FINAL_RELEASE_GATE.md`) is the last release-gated version; v1.3 is **not** released.

| # | Phase | Status | Evidence |
|---|---|---|---|
| 1 | Discovery + baseline hashes | done | `tests/baseline_v13_start.json` |
| 2 | Antigravity recovery | **done** (isolated CLI profile; IDE untouched) | `tests/verify_antigravity_live.py` 15/15 live |
| 3 | Capability registry + live probes | done | `template/tools/ngcaps.py`; live matrix for claude/codex/antigravity |
| 4 | Routing explanations, decision outcomes, auto-decomposition | done | `tests/test_v13_collab.py` |
| 5 | Protected paths, test-harness protection, integration proposals | done | `tests/test_v13.py` |
| 6 | Isolated verifier (AppContainer + Job Object; no Docker/WSL here) | done for Python/PHP+SQLite; **MariaDB blocked** | `tests/test_sandbox.py` 17/17, `tests/test_v13_verifier.py`, `tests/verify_laravel_sandbox.py` 6/6 |
| 7 | Trusted approval | **not implemented — high-risk writes stay disabled** (needs a separate OS identity; none can be created without changing the system) | `FINAL_RELEASE_GATE.md` §2 |
| 8 | Collaboration rounds, durable state, resume | done | `tests/test_v13_collab.py` |
| 9 | Memory handoffs through the project's own ngmem | done | `tests/test_v13_collab.py` |
| 10 | Live team exercise (11 scenarios) | **not run yet** | — |
| 11 | Full regression + Codex review of v1.3 changes | **not run yet** | — |
| 12 | Router-only install into Nextory (read-only use) | **not done** — Nextory untouched | — |
| 13 | `NEXTGEN_AI_TEAM_V1_3_FINAL_REPORT.md` | **not written** | — |

## Key findings so far
- Antigravity's headless tools fail because of the IDE plugin hook (`googlecloudtools.datacloud_telemetry`, literal quotes in the hook path). Fixed **without touching the IDE** by running the CLI with an isolated profile (`USERPROFILE/HOME`), plus exact-command `permissions.allow` rules. Matching is exact (no prefix/wildcard); chaining/pipes/redirects were denied 4/4. `--add-dir` and `--sandbox` break headless permissions and are not used.
- Capability matrix (live): Claude — all file/review/worktree OK, terminal/test/git off **by router policy** (no shell tool); Codex — file/review/worktree OK, terminal/test/git fail inside its sandbox (git cannot read the linked worktree; no Python); Antigravity — all OK.
- Sandboxed verification: hostile test code cannot read host canaries, write outside the workspace, see host env, or reach any network (including loopback). Laravel 12 skeleton passes under SQLite; Nextory's `Unit` suite passes in the sandbox from a throwaway copy (Nextory byte-identical afterwards).
- Nextory `Feature` suite in the sandbox **timed out at 600 s** (my script set `verify_timeout_s` in the wrong place, so the 1500 s intended was not applied). Not yet re-run — **no pass/fail claim for that suite**.
- MariaDB over TCP cannot run in an AppContainer without a system-wide loopback exemption; Docker, WSL and Windows Sandbox are not installed.

## Known gaps before v1.3 can be called done
Phases 10–13 above; a re-run of the Nextory Feature suite with the correct timeout; install tests for the new `ngcaps.py` / `ngsandbox.py` files (the installer still copies only `ngrouter.py`).
