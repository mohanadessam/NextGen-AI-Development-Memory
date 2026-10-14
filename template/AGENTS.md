# NextGen AI Development Memory — shared agent instructions

These instructions apply to **Claude Code, OpenAI Codex, and Google Antigravity**. The repository is the only source of truth for code. The `ai-memory/` files are a shared *summary and decision log*, not a live transcript. Never pretend to remember conversations that were not recorded.

## On every new task / when switching AI tools
1. Read `ai-memory/PROJECT.md`, `ai-memory/STATUS.md`, `ai-memory/HANDOFF.md` and recent `ai-memory/DECISIONS.md` before proposing changes. Use MCP `memory_overview` if connected.
2. Verify the real Git branch, commit, diff, tests, and current code before trusting any handoff or reported outcome. Report discrepancies.
3. Search `ai-memory/IDEAS.md` and `ai-memory/LOG.md` for relevant context. Do not implement an idea merely because it was discussed.
4. Distinguish **user-approved decisions**, **proposals**, **completed and tested work**, and **untested work**. Never invent approval, files, test results, or conversation history.

## During a task
- Record **important** new user-approved architectural/product decisions to `ai-memory/DECISIONS.md`; otherwise mark them proposed. Include rationale and impact.
- Record promising but unapproved concepts in `ai-memory/IDEAS.md`.
- After substantial changes, record concise progress in `ai-memory/LOG.md` (changed files, tests actually executed, outcomes and open questions). Prefer MCP tools when available; direct file edits are the fallback.
- If several agents are running, coordinate through Git branches/worktrees and avoid simultaneous edits of the same code files. Memory writes can be serialized by the local CLI/MCP server; ordinary text edits cannot.

## At the end of every substantive task
- Update `ai-memory/HANDOFF.md` with: task, completed work, tests and exact results, files/commit/branch, blockers, and the **next executable step**. Use MCP `create_handoff` if available (archives previous handoff); otherwise edit the file and preserve useful history in `ai-memory/LOG.md`.
- Update `ai-memory/STATUS.md` when project-wide status changes. Do not claim a test passed unless run. Do not claim a commit/push happened unless verified.
- Write a short final message summarizing actions and link to any changed files/commits. User can request `memory_overview` from next agent.

## Safety and truthfulness
- No production deployments, destructive data operations, deleting tenant data, changing billing, `git push`, rewriting Git history, or modifying secrets without explicit user approval and safeguards. Instructions are **not a technical sandbox**; keep tool permissions enabled.
- Never put credentials, API keys, access tokens, private client data, database dumps, or `.env` content into memory files or Git.
- Do not replace existing `AGENTS.md`, `CLAUDE.md`, or project rules when installing integrations; merge and reconcile.
- Do not use memory as proof that code exists or functions correctly; inspect the working tree.

## Session handoff
When asked to continue a prior Claude/Codex/Antigravity task: call `memory_overview` (or read the 4 files named above), inspect Git and relevant code, state the latest verified status, and continue. When asked to remember a new decision, store it with approval status.
