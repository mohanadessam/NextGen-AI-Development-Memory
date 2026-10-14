## NextGen AI Team v1.2 — dynamic multi-agent routing

Claude Code, OpenAI Codex and Google Antigravity are **general-purpose** agents here. No agent owns a role (implementation, review, frontend, backend, testing).
- For non-trivial work use the MCP server `nextgen-router`: `router_plan` / `router_delegate`. It picks executor(s) and an independent reviewer from task complexity/risk, current availability, objective performance history and cost. Manual agent choice is an optional USER override only.
- **A delegated agent must never delegate again** (no launching other agent CLIs, no `router_*` tools except read-only status). Recursion is refused.
- Write tasks need an explicit literal scope (no globs) and run only in isolated Git worktrees; the router runs the project's configured verifier itself. An agent without a router-observed write probe (`ngrouter.py probe <agent>`) is never given write work. The router never commits, pushes, merges or deploys.
- **High-risk writes are disabled** (sensitive paths such as config/, database/, migrations/, .github/, .env, or stakes keywords like tenant/billing/ledger/migration/production). There is no approval mechanism: tell the user the change must be made by them.
- Performance evidence is objective only (router-observed process/file/git facts, router-run tests, validated defects). A model's opinion of its own work is never evidence. Format compliance is not quality.
- Independent review is proportional to risk, and the final technical reviewer is never the coordinator or the executor. If the result says `review_required` / "independent review NOT performed", do not call the change verified.
- Findings, progress and handoffs flow through shared memory (`ai-memory/`); the router logs dispatches and results to LOG.md as `NextGen-Router`. The registry lives in `ai-memory/router/`.
