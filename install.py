#!/usr/bin/env python3
"""Install NextGen AI Development Memory (+ optional v1.2 Smart Agent Router add-on) into existing project roots.

Only adds files and appends bounded integration instructions. Never overwrites existing project memory, never replaces an
installed tools/ngmem.py (any version), never touches hooks. Local MCP configs hold machine-specific absolute paths and are
git-ignored.

  install.py --project P                 new project: memory kit v1.0 files (unchanged behaviour); add --with-router for the add-on too
  install.py --project P --router-only   EXISTING project (e.g. memory v1.1 with hooks): add/refresh ONLY the router add-on
  install.py --project P --rollback-router   exact rollback of what the router install changed (manifest based)
  install.py --project P --dry-run       (with --router-only) print what would change, change nothing
"""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "template"
SECTION = "<!-- NEXTGEN_SHARED_MEMORY_START -->"
END = "<!-- NEXTGEN_SHARED_MEMORY_END -->"
ROUTER_START, ROUTER_END = "<!-- NEXTGEN_ROUTER_START -->", "<!-- NEXTGEN_ROUTER_END -->"
IGN_START, IGN_END = "# >>> NextGen router v1.2 >>>", "# <<< NextGen router v1.2 <<<"
TOML_START, TOML_END = "# >>> nextgen-router (managed by install.py; rollback with --rollback-router) >>>", "# <<< nextgen-router <<<"
ROUTER_KEY = "nextgen-router"
MANIFEST = Path("ai-memory") / "router" / "install-manifest.json"
ROUTER_FILES = ("ngrouter.py", "ngcaps.py", "ngsandbox.py")


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def backup(path: Path) -> Path:
    suffix = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    out = path.with_name(path.name + ".ngmem-backup-" + suffix)
    shutil.copy2(path, out)
    return out


def append_once(path: Path, addition: str, guard: str) -> str:
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(addition.lstrip(), encoding="utf-8")
        return "created"
    prev = path.read_text(encoding="utf-8")
    if guard in prev:
        return "already configured"
    backup(path)
    path.write_text(prev.rstrip() + "\n\n" + addition.strip() + "\n", encoding="utf-8")
    return "appended (original backed up)"


def patch_json(path: Path, entry: dict, key: str = "nextgen-memory") -> str:
    was = path.is_file()
    if was:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            raise ValueError(f"Expected object in {path}")
    else:
        obj = {}
    data = obj.setdefault("mcpServers", {})
    if not isinstance(data, dict):
        raise ValueError(f"mcpServers must be an object: {path}")
    if key in data:
        if data[key] == entry:
            return "already configured"
        # This machine may have changed path from a former installation.
        # Keep manually modified entries intact instead of replacing them blindly.
        return f"existing {key} config preserved; verify paths manually"
    data[key] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    if was:
        backup(path)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "merged (original backed up)" if was else "created (local machine config)"


def patch_codex(path: Path, python: str, server: str, project: str) -> str:
    # TOML append preserves all existing tables, comments and unrelated configs.
    stanza = ('[mcp_servers."nextgen-memory"]\n'
              f'command = {json.dumps(python, ensure_ascii=False)}\n'
              f'args = {json.dumps([server, "--project", project, "mcp"], ensure_ascii=False)}\n'
              f'cwd = {json.dumps(project, ensure_ascii=False)}\n')
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if re.search(r'^\s*\[mcp_servers\.(?:"nextgen-memory"|nextgen-memory)\]\s*$', old, re.M):
            return "existing nextgen-memory config preserved; verify paths manually"
        backup(path)
        path.write_text(old.rstrip() + "\n\n" + stanza, encoding="utf-8")
        return "appended (original backed up)"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stanza, encoding="utf-8")
    return "created (local machine config)"


def patch_codex_router(path: Path, python: str, server: str, project: str) -> str:
    stanza = (f'{TOML_START}\n[mcp_servers."{ROUTER_KEY}"]\ncommand = {json.dumps(python, ensure_ascii=False)}\n'
              f'args = {json.dumps([server, "--project", project, "mcp"], ensure_ascii=False)}\ncwd = {json.dumps(project, ensure_ascii=False)}\n\n'
              f'[mcp_servers."{ROUTER_KEY}".env]\nPYTHONUTF8 = "1"\nPYTHONIOENCODING = "utf-8"\n{TOML_END}\n')
    if path.exists():
        old = path.read_text(encoding="utf-8")
        if TOML_START in old or re.search(rf'^\s*\[mcp_servers\.(?:"{ROUTER_KEY}"|{ROUTER_KEY})\]\s*$', old, re.M):
            return "already configured / existing config preserved"
        backup(path)
        path.write_text(old.rstrip() + "\n\n" + stanza, encoding="utf-8")
        return "appended (original backed up)"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stanza, encoding="utf-8")
    return "created (local machine config)"


# ───────────── router add-on (works on top of ANY installed ngmem.py) ─────────────

class Manifest:
    """Records, per touched file, its state before the first router install and after the latest one, enabling exact rollback."""

    def __init__(self, project: Path):
        self.project, self.path = project, project / MANIFEST
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.is_file() else {"version": 1, "entries": {}}

    def touch(self, path: Path, action) -> str:
        rel = path.relative_to(self.project).as_posix()
        pre_exists, pre = path.is_file(), sha(path)
        before = set(path.parent.glob(path.name + ".ngmem-backup-*")) if path.parent.is_dir() else set()
        msg = action()
        new_backups = sorted(set(path.parent.glob(path.name + ".ngmem-backup-*")) - before)
        ent = self.data["entries"].get(rel)
        if ent is None:
            ent = self.data["entries"][rel] = {"existed_before": pre_exists, "pre_sha": pre, "backup": None}
        if new_backups and ent["backup"] is None:
            ent["backup"] = new_backups[-1].relative_to(self.project).as_posix()
        ent["post_sha"] = sha(path)
        return msg

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=1) + "\n", encoding="utf-8")


def existing_python(project: Path) -> str:
    """Prefer the interpreter already registered for nextgen-memory (keeps one Python per project)."""
    with_ok = project / ".mcp.json"
    try:
        cmd = json.loads(with_ok.read_text(encoding="utf-8"))["mcpServers"]["nextgen-memory"]["command"]
        if Path(cmd).is_file():
            return cmd
    except (OSError, ValueError, KeyError):
        pass
    return str(Path(sys.executable).resolve())


def install_router(project: Path, skip_mcp: bool = False, upgrade: bool = False, dry_run: bool = False) -> None:
    tools = project / "tools"
    router_src = TEMPLATE / "tools" / "ngrouter.py"
    router = tools / "ngrouter.py"
    print(f"\n== Router add-on in {project}{'  [DRY RUN]' if dry_run else ''} ==")
    if (project / "tools" / "ngmem.py").is_file():
        print("  memory server: tools/ngmem.py present (left untouched, any version supported)")
    else:
        print("  WARNING: tools/ngmem.py not found - router works but cannot log to shared memory until memory is installed")
    if dry_run:
        print(f"  would {'refresh' if router.exists() else 'create'}: tools/ngrouter.py; add router section to AGENTS.md; add nextgen-router to .mcp.json, .agents/mcp_config.json, .codex/config.toml (merge only); ignore rules")
        return
    man = Manifest(project)
    tools.mkdir(exist_ok=True)
    (project / "ai-memory" / "router").mkdir(parents=True, exist_ok=True)

    def put_file(src: Path, dst: Path):
        def run():
            if not dst.exists():
                shutil.copy2(src, dst)
                return "created"
            if dst.read_bytes() == src.read_bytes():
                return "already at the kit version"
            if upgrade:
                backup(dst)
                shutil.copy2(src, dst)
                return "upgraded (original backed up)"
            return "preserved (differs from kit; use --upgrade to refresh)"
        return run
    for name in ROUTER_FILES:      # ngrouter.py imports ngcaps.py (capability registry) and ngsandbox.py (isolated verifier) from the same directory
        src = TEMPLATE / "tools" / name
        if src.is_file():
            print(f"  tools/{name}:", man.touch(tools / name, put_file(src, tools / name)))
    block = "\n".join([ROUTER_START, (TEMPLATE / "AGENTS_ROUTER.md").read_text(encoding="utf-8").rstrip(), ROUTER_END]) + "\n"
    print("  AGENTS.md router section:", man.touch(project / "AGENTS.md", lambda: append_once(project / "AGENTS.md", block, ROUTER_START)))
    if not skip_mcp:
        py = existing_python(project)
        entry = {"command": py, "args": [str(router), "--project", str(project), "mcp"], "env": {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}}
        print("  Claude .mcp.json:", man.touch(project / ".mcp.json", lambda: patch_json(project / ".mcp.json", {"type": "stdio", **entry}, ROUTER_KEY)))
        print("  Antigravity .agents/mcp_config.json:", man.touch(project / ".agents" / "mcp_config.json", lambda: patch_json(project / ".agents" / "mcp_config.json", entry, ROUTER_KEY)))
        print("  Codex .codex/config.toml:", man.touch(project / ".codex" / "config.toml", lambda: patch_codex_router(project / ".codex" / "config.toml", py, str(router), str(project))))
        rignore = (f"{IGN_START}\nai-memory/router/runs/\nai-memory/router/jobs/\nai-memory/router/plans/\nai-memory/router/state.json\n"
                   f"ai-memory/router/.router-lock/\nai-memory/router/*.tmp\n.ngworktrees/\n{IGN_END}")
        print("  .gitignore (router):", man.touch(project / ".gitignore", lambda: append_once(project / ".gitignore", rignore, IGN_START)))
    man.save()
    print("  Done. Hooks, memory records, ngmem.py and the nextgen-memory registrations were not touched. Restart the AI tools to load the new MCP server.")


def _strip_between(text: str, start: str, end: str) -> str:
    return re.sub(r"\n*" + re.escape(start) + r".*?" + re.escape(end) + r"\n?", "\n", text, flags=re.S)


def rollback_router(project: Path) -> None:
    mp = project / MANIFEST
    if not mp.is_file():
        print("  nothing to roll back (no install manifest)")
        return
    man = json.loads(mp.read_text(encoding="utf-8"))
    print(f"\n== Rolling back router add-on in {project} ==")
    for rel, ent in reversed(list(man["entries"].items())):
        path = project / rel
        cur = sha(path)
        if cur is None:
            print(f"  {rel}: already gone")
            continue
        if cur == ent.get("post_sha") and ent["existed_before"] and ent.get("backup") and (project / ent["backup"]).is_file():
            shutil.copy2(project / ent["backup"], path)
            print(f"  {rel}: restored exact original from {ent['backup']}")
        elif cur == ent.get("post_sha") and not ent["existed_before"]:
            path.unlink()
            print(f"  {rel}: removed (created by the router install)")
        else:   # edited since install: remove only our delimited blocks / key, keep everything else
            txt = path.read_text(encoding="utf-8")
            if path.suffix == ".json":
                obj = json.loads(txt)
                obj.get("mcpServers", {}).pop(ROUTER_KEY, None)
                path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            elif rel in ("tools/ngrouter.py", "tools/ngcaps.py", "tools/ngsandbox.py"):
                print(f"  {rel}: modified since install - left in place")
                continue
            else:
                for s, e in ((ROUTER_START, ROUTER_END), (IGN_START, IGN_END), (TOML_START, TOML_END)):
                    txt = _strip_between(txt, s, e)
                path.write_text(txt.rstrip() + "\n", encoding="utf-8")
            print(f"  {rel}: file changed since install -> removed only the router blocks")
    mp.rename(mp.with_name("install-manifest.rolled-back.json"))
    print("  ai-memory/router/ (registry, plans, results) kept on purpose; delete it manually if unwanted.")


# ───────────── full kit install (new projects) ─────────────

def install_one(project: Path, preset: str | None, skip_mcp: bool, upgrade: bool = False, with_router: bool = False) -> None:
    project = project.expanduser().resolve()
    if not project.is_dir():
        raise ValueError(f"Project directory does not exist: {project}")
    if project == HERE:
        raise ValueError("Specify a target project repository, not the starter-kit itself")
    print(f"\n== Installing in {project} ==")
    ai = project / "ai-memory"
    ai.mkdir(exist_ok=True)
    for source in (TEMPLATE / "ai-memory").glob("*.md"):
        target = ai / source.name
        if target.exists():
            print(f"  preserve: {target.relative_to(project)}")
        else:
            shutil.copy2(source, target)
            print(f"  create: {target.relative_to(project)}")
    tools = project / "tools"
    tools.mkdir(exist_ok=True)
    runner = tools / "ngmem.py"
    if not runner.exists():
        shutil.copy2(TEMPLATE / "tools" / "ngmem.py", runner)
        print("  create: tools/ngmem.py")
    else:
        print("  preserve: tools/ngmem.py (never replaced by this installer)")

    instructions = (TEMPLATE / "AGENTS.md").read_text(encoding="utf-8")
    block = f"{SECTION}\n{instructions.rstrip()}\n{END}\n"
    print("  AGENTS.md:", append_once(project / "AGENTS.md", block, SECTION))
    claude = project / "CLAUDE.md"
    if not claude.exists():
        shutil.copy2(TEMPLATE / "CLAUDE.md", claude)
        print("  CLAUDE.md: created")
    elif "@AGENTS.md" in claude.read_text(encoding="utf-8"):
        print("  CLAUDE.md: already imports AGENTS.md")
    else:
        print("  CLAUDE.md:", append_once(claude, "@AGENTS.md\n", "@AGENTS.md"))

    if preset == "nextory":
        obj = json.loads((HERE / "nextory-preset.json").read_text(encoding="utf-8"))
        # Only replace known untouched scaffold; never overwrite user-edited content.
        for name, key in (("PROJECT", "project"), ("STATUS", "status")):
            path = ai / f"{name}.md"
            scaffold = TEMPLATE / "ai-memory" / f"{name}.md"
            if path.read_text(encoding="utf-8") == scaffold.read_text(encoding="utf-8"):
                path.write_text(obj[key], encoding="utf-8")
                print(f"  preset: ai-memory/{name}.md (historical, UNVERIFIED context)")
            else:
                print(f"  preserve: customized ai-memory/{name}.md")

    if not skip_mcp:
        executable = str(Path(sys.executable).resolve())
        cmd_args = [str(runner), "--project", str(project), "mcp"]
        raw = {"command": executable, "args": cmd_args}
        print("  Claude .mcp.json:", patch_json(project / ".mcp.json", {"type": "stdio", **raw}))
        print("  Antigravity .agents/mcp_config.json:", patch_json(project / ".agents" / "mcp_config.json", raw))
        print("  Codex .codex/config.toml:", patch_codex(project / ".codex" / "config.toml", executable, str(runner), str(project)))
        # Generated configuration is workstation-specific; future clones regenerate via install.py.
        ignore = ("# NextGen AI memory: local MCP paths (re-run installer on each computer)\n"
                  ".mcp.json\n.agents/mcp_config.json\n.codex/config.toml\n"
                  "# Local backups, temp and locks\n*.ngmem-backup-*\nai-memory/.memory-write-lock/\nai-memory/*.tmp\n")
        print("  .gitignore:", append_once(project / ".gitignore", ignore, "# NextGen AI memory: local MCP paths"))
        print("  MCP registrations are machine-local; restart AI tools / approve project MCP server as needed.")
    if with_router:
        install_router(project, skip_mcp, upgrade)
    print("  Verify: " + json.dumps([sys.executable, str(runner), "overview"], ensure_ascii=False))
    print("  Done. No app code, Git commits, pushes, or deployments performed.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Install shared AI memory (and the v1.2 router add-on) into existing projects")
    ap.add_argument("--project", action="append", required=True, help="Project root; repeat option for multiple repositories")
    ap.add_argument("--preset", choices=["nextory"], help="Optional user-reported, unverified starting project context")
    ap.add_argument("--with-router", action="store_true", help="New project: install the memory kit AND the router add-on (default install is router-free, exactly as before)")
    ap.add_argument("--router-only", action="store_true", help="Existing project: add/refresh ONLY the router add-on (never touches ngmem.py, hooks, memory, nextgen-memory registrations)")
    ap.add_argument("--upgrade", action="store_true", help="Refresh tools/ngrouter.py to the kit version (original backed up). Never replaces ngmem.py")
    ap.add_argument("--rollback-router", action="store_true", help="Undo the router install using ai-memory/router/install-manifest.json")
    ap.add_argument("--dry-run", action="store_true", help="With --router-only: show intended changes without writing")
    ap.add_argument("--skip-mcp", action="store_true", help="Do not register MCP servers")
    args = ap.parse_args()
    if args.preset and len(args.project) != 1:
        ap.error("--preset applies to one project at a time")
    for p in args.project:
        proj = Path(p).expanduser().resolve()
        if not proj.is_dir():
            raise ValueError(f"Project directory does not exist: {proj}")
        if args.rollback_router:
            rollback_router(proj)
        elif args.router_only:
            install_router(proj, args.skip_mcp, args.upgrade, args.dry_run)
        else:
            install_one(proj, args.preset, args.skip_mcp, args.upgrade, args.with_router)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Install failed: {e}", file=sys.stderr)
        sys.exit(2)
