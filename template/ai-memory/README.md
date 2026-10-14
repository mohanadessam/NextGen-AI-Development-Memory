# Memory conventions

- `PROJECT.md`: durable product, stack, architecture and business invariants.
- `STATUS.md`: current verified project-wide state; correct after each milestone.
- `DECISIONS.md`: approved/proposed decisions and their rationales (append-only).
- `IDEAS.md`: unapproved proposals (append-only).
- `HANDOFF.md`: current agent-to-agent handoff; newest only.
- `LOG.md`: progress notes and historical handoffs (append-only).

Git-track these Markdown files if they have no sensitive information. These files are **not** a verbatim conversation backup. Use `python tools/ngmem.py overview` or an MCP-enabled agent to read. Run `python tools/ngmem.py --help` for commands. Avoid parallel manual edits; CLI/MCP serializes its own writes.
