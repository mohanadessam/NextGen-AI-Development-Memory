# Changelog

## 1.2.2 - 2026-10-09 - Final release gate (supersedes the 1.2 entry's claims)
- The router is a **stand-alone add-on** (`tools/ngrouter.py`, MCP server `nextgen-router`). It no longer touches or replaces `tools/ngmem.py`; it logs through the project's own ngmem CLI (v1.0 and v1.1 verified). `install.py --router-only` / `--rollback-router` / `--dry-run`; default `install.py` behaviour is unchanged.
- Correction: the 1.2 report called the kit's template "v1.1". The real v1.1 (hooks, 45 tests) lives in Nextory's `tools/`; the kit template is v1.0.
- Security hardening after two independent Codex reviews: high-risk writes disabled (no approval mechanism); literal scopes only; named verifiers only; env allowlist; fresh worktree per attempt; read-only violations fail and stop failover; commit/reset/ignored-file/symlink/rename checks; main-repo + router-file fingerprints; reviewer verdict required; capabilities default to unverified until `probe`.
- Tests: 8 kit v1.0 + 90 router + 8 install audit + 45 real v1.1 (disposable copy) + authenticated E2E 39/39. See FINAL_RELEASE_GATE.md.

## 1.0 — 2026-10-08
- Portable Markdown state per repo; shared AGENTS.md, Claude import.
- Standard-library, local stdio MCP server with seven tools.
- Safe merge-based Python installer for three tool configurations.
- Multiple project option and optional historical Nextory bootstrapping.
- Local machine MCP configs Git-ignored; verify with test suite.
