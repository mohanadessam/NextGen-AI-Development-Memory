"""Install-safety audit: router add-on on top of a REAL memory v1.1 project (hooks, custom instructions, existing MCP servers).

The fixture is a disposable copy of Nextory's v1.1 files (READ-ONLY source: NEXTORY_SRC, default the local Nextory checkout);
nothing in Nextory is ever written. Skipped automatically if the source is not available.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
SRC = Path(os.environ.get("NEXTORY_SRC", r"H:\NextGen_Projects\Next-gen\Next-apps\NextGen-Factory_erp"))
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
COPY = ["AGENTS.md", "CLAUDE.md", ".gitignore", ".mcp.json", ".claude/settings.local.json", ".codex/config.toml", ".agents/hooks.json", ".agents/mcp_config.json",
        "tools/ngmem.py", "tools/ngmem_hook.py", "tools/install_hooks.py", "tools/test_ngmem.py"]


def digest(root: Path, ignore_router=True) -> dict:
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if not p.is_file() or rel.startswith(".git/") or "__pycache__" in rel:
            continue
        if ignore_router and (rel.startswith("ai-memory/router") or ".ngmem-backup-" in rel):
            continue
        out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


@unittest.skipUnless((SRC / "tools" / "ngmem.py").is_file(), "Nextory v1.1 sample not available")
class UpgradeAudit(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Path(self.tmp.name) / "v11project"
        for rel in COPY:
            if (SRC / rel).is_file():
                (self.p / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(SRC / rel, self.p / rel)
        shutil.copytree(SRC / "ai-memory", self.p / "ai-memory", ignore=shutil.ignore_patterns("router", ".memory-write-lock"))
        # make the sample conspicuously "custom" so preservation is provable
        with (self.p / "AGENTS.md").open("a", encoding="utf-8") as f:
            f.write("\n\n## CUSTOM RULE (must survive)\nNever touch the billing tables.\n")
        with (self.p / "CLAUDE.md").open("a", encoding="utf-8") as f:
            f.write("\n# CUSTOM CLAUDE NOTE (must survive)\n")
        with (self.p / ".gitignore").open("a", encoding="utf-8") as f:
            f.write("\n# custom ignore\nsecret-dump.sql\n")
        mcp = json.loads((self.p / ".mcp.json").read_text(encoding="utf-8"))
        mcp["mcpServers"]["some-other-server"] = {"command": "node", "args": ["x.js"]}
        (self.p / ".mcp.json").write_text(json.dumps(mcp, indent=2), encoding="utf-8")
        for a in (["init", "-q"], ["config", "user.email", "a@b"], ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "v1.1 baseline"]):
            subprocess.run(["git", "-C", str(self.p), *a], check=True, capture_output=True)
        self.before = digest(self.p)
        self.orig = {rel: (self.p / rel).read_text(encoding="utf-8") for rel in (".mcp.json", ".codex/config.toml", "AGENTS.md")}

    def inst(self, *extra, check=True):
        r = subprocess.run([sys.executable, str(KIT / "install.py"), "--project", str(self.p), *extra], capture_output=True, text=True, env=ENV)
        if check and r.returncode:
            self.fail(r.stdout + r.stderr)
        return r

    def changed(self):
        now = digest(self.p)
        return {k for k in set(now) | set(self.before) if now.get(k) != self.before.get(k)}

    def test_dry_run_changes_nothing(self):
        self.inst("--router-only", "--dry-run")
        self.assertEqual(self.changed(), set())
        self.assertFalse((self.p / "tools" / "ngrouter.py").exists())

    def test_router_only_install_preserves_everything_it_must(self):
        self.inst("--router-only")
        ch = self.changed()
        # only these may change, and only by ADDING router content
        self.assertEqual(ch, {"AGENTS.md", ".mcp.json", ".codex/config.toml", ".agents/mcp_config.json", ".gitignore", "tools/ngrouter.py", "tools/ngcaps.py", "tools/ngsandbox.py"})
        for rel in ("tools/ngmem.py", "tools/ngmem_hook.py", "tools/install_hooks.py", ".claude/settings.local.json", ".agents/hooks.json", "CLAUDE.md"):
            self.assertEqual(digest(self.p)[rel], self.before[rel], f"{rel} must be byte-identical")
        for rel in [r for r in self.before if r.startswith("ai-memory/")]:
            self.assertEqual(digest(self.p)[rel], self.before[rel], f"memory record {rel} must be byte-identical")
        agents = (self.p / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("CUSTOM RULE (must survive)", agents)
        self.assertEqual(agents.count("NEXTGEN_ROUTER_START"), 1)
        self.assertTrue(agents.startswith(self.orig["AGENTS.md"].rstrip()), "original AGENTS.md (incl. v1.1 block + custom rule) is a verbatim prefix")
        mcp = json.loads((self.p / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        self.assertEqual(set(mcp), {"nextgen-memory", "some-other-server", "nextgen-router"})
        self.assertEqual(mcp["nextgen-memory"], json.loads(self.orig[".mcp.json"])["mcpServers"]["nextgen-memory"])
        toml = (self.p / ".codex/config.toml").read_text(encoding="utf-8")
        old = self.orig[".codex/config.toml"]
        self.assertTrue(toml.startswith(old.rstrip()), "existing codex config incl. hook blocks is a verbatim prefix")
        self.assertIn('[mcp_servers."nextgen-router"]', toml)
        gi = (self.p / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("secret-dump.sql", gi)
        self.assertIn(".ngworktrees/", gi)

    def test_upgrade_is_idempotent_and_never_replaces_memory_server(self):
        self.inst("--router-only")
        first = digest(self.p, ignore_router=False)
        for _ in range(2):
            self.inst("--router-only")
            self.inst("--router-only", "--upgrade")
        second = digest(self.p, ignore_router=False)
        self.assertEqual({k: v for k, v in first.items() if ".ngmem-backup-" not in k}, {k: v for k, v in second.items() if ".ngmem-backup-" not in k})
        self.assertEqual(digest(self.p)["tools/ngmem.py"], self.before["tools/ngmem.py"])

    def test_upgrade_refreshes_modified_router_with_backup(self):
        self.inst("--router-only")
        r = self.p / "tools" / "ngrouter.py"
        r.write_text(r.read_text(encoding="utf-8") + "\n# local tamper\n", encoding="utf-8")
        self.inst("--router-only")                                        # without --upgrade it is preserved
        self.assertIn("local tamper", r.read_text(encoding="utf-8"))
        self.inst("--router-only", "--upgrade")
        self.assertNotIn("local tamper", r.read_text(encoding="utf-8"))
        self.assertTrue(list((self.p / "tools").glob("ngrouter.py.ngmem-backup-*")))

    def test_rollback_restores_exact_original_bytes(self):
        self.inst("--router-only")
        self.inst("--rollback-router")
        self.assertEqual(self.changed(), set(), "every pre-existing file is byte-identical again and ngrouter.py is gone")
        self.inst("--rollback-router")                                   # idempotent
        self.assertEqual(self.changed(), set())
        self.inst("--router-only")                                        # and a re-install after rollback works
        self.assertTrue((self.p / "tools" / "ngrouter.py").exists())

    def test_rollback_after_user_edits_removes_only_router_blocks(self):
        self.inst("--router-only")
        with (self.p / "AGENTS.md").open("a", encoding="utf-8") as f:
            f.write("\nUSER ADDED AFTER INSTALL\n")
        mcp = json.loads((self.p / ".mcp.json").read_text(encoding="utf-8"))
        mcp["mcpServers"]["added-later"] = {"command": "x"}
        (self.p / ".mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
        self.inst("--rollback-router")
        agents = (self.p / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("USER ADDED AFTER INSTALL", agents)
        self.assertIn("CUSTOM RULE (must survive)", agents)
        self.assertNotIn("NEXTGEN_ROUTER", agents)
        m = json.loads((self.p / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
        self.assertEqual(set(m), {"nextgen-memory", "some-other-server", "added-later"})
        self.assertNotIn("nextgen-router", (self.p / ".codex/config.toml").read_text(encoding="utf-8"))

    def test_v11_suite_and_hooks_still_pass_after_router_install(self):
        self.inst("--router-only")
        env = {**ENV, "NGMEM_TOOLS_DIR": str(self.p / "tools")}
        r = subprocess.run([sys.executable, str(self.p / "tools" / "test_ngmem.py")], capture_output=True, text=True, env=env, timeout=600)
        self.assertIn("Ran 45 tests", r.stderr + r.stdout)
        self.assertIn("OK", (r.stderr + r.stdout).strip().splitlines()[-1])
        tl = subprocess.run([sys.executable, str(self.p / "tools" / "ngmem.py"), "--project", str(self.p), "mcp"], input='{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n', capture_output=True, text=True, env=ENV)
        self.assertTrue(len(json.loads(tl.stdout)["result"]["tools"]) >= 7)

    def test_router_mcp_server_and_bridge_work_on_top_of_v11(self):
        self.inst("--router-only")
        req = "\n".join(json.dumps(x) for x in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "router_status", "arguments": {}}})) + "\n"
        r = subprocess.run([sys.executable, str(self.p / "tools" / "ngrouter.py"), "--project", str(self.p), "mcp"], input=req, capture_output=True, text=True, env=ENV, timeout=180)
        rep = [json.loads(l) for l in r.stdout.splitlines()]
        self.assertEqual(rep[0]["result"]["serverInfo"]["name"], "nextgen-router")
        self.assertNotIn("router_approve", {t["name"] for t in rep[1]["result"]["tools"]})
        self.assertFalse(rep[2]["result"]["isError"])
        sys.path.insert(0, str(KIT / "template" / "tools"))
        import ngrouter
        b = ngrouter.MemoryBridge(self.p)
        self.assertIn("Saved", b.record(self.p, "LOG", "Router bridge test — اختبار", "unicode ✓ details"))
        self.assertIn("Duplicate skipped", b.record(self.p, "LOG", "Router bridge test — اختبار", "unicode ✓ details"), "v1.1 de-duplication still in charge")
        self.assertIn("Recorded by:** NextGen-Router", (self.p / "ai-memory" / "LOG.md").read_text(encoding="utf-8"))
        self.assertIn("Sessions".lower(), b.snippet(self.p).lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
