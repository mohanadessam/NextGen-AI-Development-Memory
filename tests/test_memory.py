import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ngmem", KIT / "template" / "tools" / "ngmem.py")
ngmem = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ngmem)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "Nextory"
        self.project.mkdir()
        self.install()

    def install(self, *extra):
        return subprocess.run([sys.executable, str(KIT / "install.py"), "--project", str(self.project), *extra], text=True, capture_output=True, check=True)

    def test_scaffold_and_all_integrations(self):
        self.assertIn("NEXTGEN_SHARED_MEMORY_START", (self.project / "AGENTS.md").read_text())
        self.assertIn("@AGENTS.md", (self.project / "CLAUDE.md").read_text())
        self.assertTrue((self.project / "tools" / "ngmem.py").exists())
        for file in ngmem.DOCS:
            self.assertTrue((self.project / "ai-memory" / f"{file}.md").is_file())
        claude = json.loads((self.project / ".mcp.json").read_text())
        anti = json.loads((self.project / ".agents" / "mcp_config.json").read_text())
        self.assertEqual(claude["mcpServers"]["nextgen-memory"]["args"], anti["mcpServers"]["nextgen-memory"]["args"])
        self.assertIn('[mcp_servers."nextgen-memory"]', (self.project / ".codex/config.toml").read_text())
        self.assertIn(".codex/config.toml", (self.project / ".gitignore").read_text())

    def test_idempotent_preserve(self):
        p = self.project / "AGENTS.md"
        before = p.read_text()
        self.install()
        self.assertEqual(before, p.read_text())
        self.assertEqual(p.read_text().count("NEXTGEN_SHARED_MEMORY_START"), 1)
        self.assertEqual((self.project / ".codex/config.toml").read_text().count("[mcp_servers"), 1)

    def test_merge_existing_rules_and_mcp_servers(self):
        project2 = Path(self.tmp.name) / "Existing"
        project2.mkdir()
        (project2 / "AGENTS.md").write_text("# Existing important rules\n", encoding="utf8")
        (project2 / "CLAUDE.md").write_text("# Existing Claude instructions\n", encoding="utf8")
        (project2 / ".mcp.json").write_text(json.dumps({"mcpServers": {"existing": {"command": "node"}}}))
        (project2 / ".codex").mkdir()
        (project2 / ".codex/config.toml").write_text("model = 'gpt-6'\n", encoding="utf8")
        subprocess.run([sys.executable, str(KIT / "install.py"), "--project", str(project2)], check=True, capture_output=True, text=True)
        self.assertIn("Existing important rules", (project2 / "AGENTS.md").read_text())
        self.assertIn("Existing Claude instructions", (project2 / "CLAUDE.md").read_text())
        self.assertIn("@AGENTS.md", (project2 / "CLAUDE.md").read_text())
        self.assertIn("existing", json.loads((project2 / ".mcp.json").read_text())["mcpServers"])
        self.assertIn("model = 'gpt-6'", (project2 / ".codex/config.toml").read_text())
        self.assertTrue(list(project2.glob("AGENTS.md.ngmem-backup-*")))

    def test_three_agents_decision_and_handoff(self):
        self.assertIn("Saved", ngmem.record(self.project, "DECISIONS", "Tenant isolation", "User approved per-tenant db", "Claude", "user-approved"))
        self.assertIn("Saved", ngmem.record(self.project, "IDEAS", "Smart reports", "Consider later", "Codex"))
        out = ngmem.handoff(self.project, "Antigravity", "Improve UX", "Updated 2 components", "Run UI tests", "Not run")
        self.assertIn("saved", out)
        self.assertIn("Improve UX", ngmem.read(self.project, "HANDOFF"))
        self.assertIn("Tenant isolation", ngmem.snippet(self.project))
        self.assertIn("Smart reports", ngmem.search(self.project, "reports"))
        ngmem.handoff(self.project, "Codex", "Review", "Found one bug", "Fix bug", "phpunit 12 passed")
        self.assertIn("Improve UX", ngmem.read(self.project, "LOG"))
        self.assertIn("Review", ngmem.read(self.project, "HANDOFF"))

    def test_mcp_protocol_and_tool_calls(self):
        script = self.project / "tools/ngmem.py"
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "record_idea", "arguments": {"title": "Android app", "details": "Maybe later", "agent": "Claude"}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "search_memory", "arguments": {"query": "Android"}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "read_memory", "arguments": {"document": "../../../../etc/passwd"}}},
        ]
        p = subprocess.run([sys.executable, str(script), "mcp"], input="\n".join(json.dumps(x) for x in reqs) + "\n", text=True, capture_output=True, check=True)
        self.assertEqual(p.stderr, "")
        self.assertEqual(len(p.stdout.splitlines()), 5)
        reply = [json.loads(line) for line in p.stdout.splitlines()]
        self.assertEqual(reply[0]["result"]["serverInfo"]["name"], "nextgen-development-memory")
        self.assertEqual(len(reply[1]["result"]["tools"]), 7)
        self.assertIn("Saved", reply[2]["result"]["content"][0]["text"])
        self.assertIn("Android", reply[3]["result"]["content"][0]["text"])
        self.assertTrue(reply[4]["result"]["isError"])

    def test_older_mcp_client_negotiation(self):
        old = ngmem.mcp_reply({"jsonrpc": "2.0", "id": 42, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}, self.project)
        self.assertEqual(old["result"]["protocolVersion"], "2025-03-26")

    def test_secret_and_unapproved_decision_guards(self):
        with self.assertRaises(ValueError):
            ngmem.record(self.project, "DECISIONS", "Test", "bad", "Codex", "approved")
        with self.assertRaises(ValueError):
            ngmem.record(self.project, "IDEAS", "secret", "-----BEGIN OPENSSH PRIVATE KEY-----", "Codex")

    def test_nextory_preset_is_explicitly_unverified(self):
        project2 = Path(self.tmp.name) / "NextoryPreset"
        project2.mkdir()
        subprocess.run([sys.executable, str(KIT / "install.py"), "--project", str(project2), "--preset", "nextory"], check=True)
        self.assertIn("585eced", (project2 / "ai-memory" / "STATUS.md").read_text())
        self.assertIn("VERIFY", (project2 / "ai-memory" / "STATUS.md").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
