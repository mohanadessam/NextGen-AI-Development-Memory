#!/usr/bin/env python3
"""NextGen AI Development Memory: shared Markdown memory + local MCP 2025-11 stdio server.
Python 3.9+, standard library only. Run inside installed project.
"""
from __future__ import annotations
import argparse
import contextlib
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

DOCS = ("PROJECT", "STATUS", "DECISIONS", "IDEAS", "HANDOFF", "LOG")
MAX_TEXT = 6000
SERVER_NAME = "nextgen-development-memory"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def root_for(value: str | None = None) -> Path:
    return Path(value).expanduser().resolve() if value else Path(__file__).resolve().parent.parent


def memory_dir(project: Path) -> Path:
    dest = project / "ai-memory"
    if not dest.is_dir():
        raise ValueError(f"Memory not installed: {dest}. Run install.py first.")
    return dest


def validate_doc(name: str) -> str:
    n = name.upper().removesuffix(".MD")
    if n not in DOCS:
        raise ValueError(f"Unknown document: {name}; choose {', '.join(DOCS)}")
    return n


def read(project: Path, name: str) -> str:
    path = memory_dir(project) / (validate_doc(name) + ".md")
    return path.read_text(encoding="utf-8") if path.is_file() else "(empty)"


def snippet(project: Path) -> str:
    parts = []
    for name, maxchars in (("PROJECT", 1500), ("STATUS", 2200), ("HANDOFF", 2800), ("DECISIONS", 1600), ("IDEAS", 900)):
        val = read(project, name).strip()
        parts.append(f"## {name}\n{val[:maxchars]}" + ("\n…[truncated; use read_memory]" if len(val) > maxchars else ""))
    return "\n\n".join(parts)


def bounded(value: Any, field: str, limit: int = MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    x = value.strip().replace("\x00", "")
    if len(x) > limit:
        raise ValueError(f"{field} exceeds {limit} characters")
    # This is defense-in-depth only; users must still review content before committing.
    if re.search(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----|\b(?:sk-proj|sk-ant-api)\-[A-Za-z0-9_-]{12,}", x):
        raise ValueError("Potential secret detected; do not store credentials in shared memory")
    return x


@contextlib.contextmanager
def lock(project: Path):
    path = memory_dir(project) / ".memory-write-lock"
    deadline = time.monotonic() + 7
    while True:
        try:
            path.mkdir()
            break
        except FileExistsError:
            try:
                if time.time() - path.stat().st_mtime > 60:
                    path.rmdir()
                    continue
            except (FileNotFoundError, OSError):
                pass
            if time.monotonic() > deadline:
                raise TimeoutError("Shared memory is currently being written; retry shortly")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            path.rmdir()
        except OSError:
            pass


def append_locked(project: Path, name: str, entry: str) -> None:
    path = memory_dir(project) / f"{validate_doc(name)}.md"
    with path.open("a", encoding="utf-8", newline="\n") as fp:
        fp.write("\n" + entry.rstrip() + "\n")
        fp.flush()
        os.fsync(fp.fileno())


def atomically_replace(path: Path, content: str) -> None:
    # Avoid temp-file races across independent clients while holding shared lock.
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def record(project: Path, kind: str, title: str, details: str, agent: str = "unspecified", state: str = "proposed") -> str:
    title = bounded(title, "title", 150)
    details = bounded(details, "details")
    agent = bounded(agent, "agent", 80)
    if kind not in ("DECISIONS", "IDEAS", "LOG"):
        raise ValueError("Invalid entry type")
    if kind == "DECISIONS" and state not in ("proposed", "user-approved"):
        raise ValueError("Decision state must be proposed or user-approved")
    status_line = f"**State:** {state}  \n" if kind == "DECISIONS" else ""
    entry = f"### {now()} — {title}\n**Recorded by:** {agent}  \n{status_line}{details}\n"
    with lock(project):
        append_locked(project, kind, entry)
    return f"Saved {kind.lower()} entry: {title} ({state if kind == 'DECISIONS' else 'recorded'})"


def git_context(project: Path) -> str:
    def git(*args: str) -> str:
        try:
            p = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, timeout=4)
            return p.stdout.strip() if p.returncode == 0 else "not available"
        except (OSError, subprocess.TimeoutExpired):
            return "not available"
    branch = git("branch", "--show-current")
    commit = git("rev-parse", "--short", "HEAD")
    return f"Branch: {branch}; Commit: {commit}; Working tree: {git('status', '--porcelain') or 'clean'}"


def handoff(project: Path, agent: str, task: str, completed: str, next_steps: str, tests: str, risks: str = "none reported") -> str:
    fields = {"agent": agent, "task": task, "completed": completed, "next_steps": next_steps, "tests": tests, "risks": risks}
    for field, val in fields.items():
        fields[field] = bounded(val, field)
    entry = (f"# Current handoff\n\nUpdated: {now()}  \nAgent: {fields['agent']}  \n"
             f"Git: {git_context(project)}\n\n## Task\n{fields['task']}\n\n"
             f"## Completed (verified vs unverified clearly indicated)\n{fields['completed']}\n\n"
             f"## Next steps\n{fields['next_steps']}\n\n## Tests and evidence\n{fields['tests']}\n\n"
             f"## Risks / blockers\n{fields['risks']}\n")
    with lock(project):
        # Append old handoff into history before overwriting latest snapshot.
        old = read(project, "HANDOFF")
        if "## Task\n" in old:
            append_locked(project, "LOG", f"### Archived handoff — {now()}\n\n{old}\n")
        atomically_replace(memory_dir(project) / "HANDOFF.md", entry)
    return "Handoff saved. Next agent should read ai-memory/HANDOFF.md and verify repository state."


def search(project: Path, query: str, limit: int = 15) -> str:
    query = bounded(query, "query", 200).casefold()
    results = []
    for name in DOCS:
        lines = read(project, name).splitlines()
        for i, line in enumerate(lines):
            if query in line.casefold():
                s = " | ".join(t.strip()[:250] for t in lines[max(0, i-1):min(len(lines), i+2)])
                results.append(f"{name}.md:{i+1} — {s}")
                if len(results) >= limit:
                    return "\n".join(results)
    return "\n".join(results) if results else "No matching entries."


def schema_tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"name": name, "description": description, "inputSchema": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}


def prop(desc: str, enum: list[str] | None = None) -> dict:
    d: dict[str, Any] = {"type": "string", "description": desc}
    if enum is not None:
        d["enum"] = enum
    return d


TOOLS = [
    schema_tool("memory_overview", "Read the source-of-truth project context, latest handoff, current status and decisions at the start of tasks.", {}, []),
    schema_tool("read_memory", "Read a specific shared memory document (not raw chat history).", {"document": prop("Allowed shared memory file", list(DOCS))}, ["document"]),
    schema_tool("search_memory", "Search project ideas, rationale, past handoffs and decisions.", {"query": prop("Case-insensitive substring to find")}, ["query"]),
    schema_tool("record_decision", "Record a proposed or explicitly user-approved decision. Never claim approval unless user clearly approved it.", {"title": prop("Concise decision title"), "details": prop("Decision, motivation, tradeoffs and supporting context"), "agent": prop("Claude, Codex, Antigravity, or human"), "state": prop("Approval state", ["proposed", "user-approved"])}, ["title", "details", "agent", "state"]),
    schema_tool("record_idea", "Preserve an idea not yet approved or implemented.", {"title": prop("Idea"), "details": prop("Description and implications"), "agent": prop("Agent name")}, ["title", "details", "agent"]),
    schema_tool("record_progress", "Append dated work notes with evidence, without overwriting prior decisions.", {"title": prop("Work note title"), "details": prop("Changed files, verification, unresolved issues"), "agent": prop("Agent name")}, ["title", "details", "agent"]),
    schema_tool("create_handoff", "At task end, replace CURRENT handoff atomically and archive previous one. Always distinguish tested and untested work.", {"agent": prop("Agent name"), "task": prop("Task goal"), "completed": prop("Completed work, files touched and results"), "next_steps": prop("Explicit remaining work"), "tests": prop("Exact checks run, result and not-run checks"), "risks": prop("Risks or blockers, or 'none reported'")}, ["agent", "task", "completed", "next_steps", "tests"]),
]


def call_tool(project: Path, name: str, args: dict[str, Any]) -> str:
    if name == "memory_overview": return snippet(project) + "\n\n## Git\n" + git_context(project)
    if name == "read_memory": return read(project, args["document"])
    if name == "search_memory": return search(project, args["query"])
    if name == "record_decision": return record(project, "DECISIONS", args["title"], args["details"], args["agent"], args["state"])
    if name == "record_idea": return record(project, "IDEAS", args["title"], args["details"], args["agent"])
    if name == "record_progress": return record(project, "LOG", args["title"], args["details"], args["agent"])
    if name == "create_handoff": return handoff(project, args["agent"], args["task"], args["completed"], args["next_steps"], args["tests"], args.get("risks", "none reported"))
    raise ValueError(f"Unknown tool: {name}")


def mcp_reply(req: dict, project: Path) -> dict | None:
    if not isinstance(req, dict):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    if "id" not in req:  # notifications don't receive a response
        return None
    req_id = req["id"]
    method = req.get("method")
    try:
        if method == "initialize":
            requested = req.get("params", {}).get("protocolVersion")
            negotiated = requested if requested in ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05") else "2025-11-25"
            result = {"protocolVersion": negotiated, "capabilities": {"tools": {"listChanged": False}},
                      "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
                      "instructions": "Read memory_overview at session start and before handoff. Record only user-approved decisions as user-approved. Write a create_handoff at task end. Never store credentials or private customer data; review memory before Git commits."}
        elif method == "ping": result = {}
        elif method == "tools/list": result = {"tools": TOOLS}
        elif method == "tools/call":
            p = req.get("params", {})
            value = call_tool(project, p.get("name", ""), p.get("arguments", {}) or {})
            result = {"content": [{"type": "text", "text": value}], "isError": False}
        else:
            return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}
        return {"jsonrpc": "2.0", "id": req_id, "result": result}
    except (ValueError, KeyError, TimeoutError, OSError) as exc:
        if method == "tools/call":
            return {"jsonrpc": "2.0", "id": req_id, "result": {"content": [{"type": "text", "text": f"Error: {exc}"}], "isError": True}}
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": str(exc)}}


def serve(project: Path) -> None:
    # MCP stdio is newline-delimited JSON-RPC. stdout MUST contain only protocol messages.
    for raw in sys.stdin.buffer:
        try:
            msg = json.loads(raw)
            reply = mcp_reply(msg, project)
        except Exception as exc:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="NextGen shared developer memory (no API key needed)")
    parser.add_argument("--project", default=None, help="Project root (defaults to parent of tools folder)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("overview")
    r = sub.add_parser("read"); r.add_argument("document", choices=DOCS)
    s = sub.add_parser("search"); s.add_argument("query")
    d = sub.add_parser("decision"); d.add_argument("title"); d.add_argument("details"); d.add_argument("--agent", default="human"); d.add_argument("--state", choices=["proposed", "user-approved"], default="proposed")
    i = sub.add_parser("idea"); i.add_argument("title"); i.add_argument("details"); i.add_argument("--agent", default="human")
    l = sub.add_parser("log"); l.add_argument("title"); l.add_argument("details"); l.add_argument("--agent", default="human")
    h = sub.add_parser("handoff")
    for arg in ("agent", "task", "completed", "next_steps", "tests"):
        h.add_argument("--" + arg.replace("_", "-"), required=True)
    h.add_argument("--risks", default="none reported")
    sub.add_parser("mcp")
    args = parser.parse_args()
    project = root_for(args.project)
    if args.command == "mcp": serve(project)
    elif args.command == "overview": print(snippet(project) + "\n\nGit: " + git_context(project))
    elif args.command == "read": print(read(project, args.document))
    elif args.command == "search": print(search(project, args.query))
    elif args.command == "decision": print(record(project, "DECISIONS", args.title, args.details, args.agent, args.state))
    elif args.command == "idea": print(record(project, "IDEAS", args.title, args.details, args.agent))
    elif args.command == "log": print(record(project, "LOG", args.title, args.details, args.agent))
    elif args.command == "handoff": print(handoff(project, args.agent, args.task, args.completed, args.next_steps, args.tests, args.risks))


if __name__ == "__main__":
    try: main()
    except (ValueError, TimeoutError) as e:
        print("Error:", e, file=sys.stderr)
        sys.exit(2)
