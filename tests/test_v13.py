"""v1.3 tests: capability registry, protected paths, test-harness protection, integration proposals, Antigravity adapter safety.
Simulated (fake agent CLIs); live Antigravity/capability checks are in verify_antigravity_live.py and the live exercise."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_router import RouterBase, ngr  # noqa: E402  (RouterBase has no tests of its own)


class CapabilityRegistry(RouterBase):
    def rec(self, agent, cap, ok=True, version="1.0"):
        self.r.caps.record(agent, cap, ok, "ev", None if ok else "boom", version, "model-x", {"os": os.name})

    def test_verified_stale_expired_and_failed_states(self):
        self.assertFalse(self.r.caps.status("codex", "file_write")["ok"])           # never verified
        self.rec("codex", "file_write")
        self.assertTrue(self.r.caps.is_ok("codex", "file_write"))
        self.r.caps.mark_stale("codex", ["file_write"], "unit test")
        st = self.r.caps.status("codex", "file_write")
        self.assertFalse(st["ok"])
        self.assertIn("stale", st["reason"])
        self.rec("codex", "file_write")
        data = self.r.caps.load()
        data["agents"]["codex"]["capabilities"]["file_write"]["expires_at"] = "2000-01-01T00:00:00Z"
        self.r.caps._save(data)
        self.assertIn("expired", self.r.caps.status("codex", "file_write")["reason"])
        self.rec("codex", "terminal", ok=False)
        self.assertEqual(self.r.caps.status("codex", "terminal")["reason"], "boom")

    def test_record_keeps_version_model_environment_and_evidence(self):
        self.rec("claude", "file_read", version="2.1.278")
        e = self.r.caps.load()["agents"]["claude"]
        self.assertEqual((e["tool_version"], e["model"]), ("2.1.278", "model-x"))
        c = e["capabilities"]["file_read"]
        self.assertTrue(c["verified_at"] and c["expires_at"] and c["evidence"] == "ev")

    def test_tool_version_change_marks_everything_stale(self):
        self.rec("codex", "file_write", version="1.0")
        self.rec("codex", "worktree", version="1.0")
        self.r.caps.current_version = lambda agent: "2.0"
        self.assertIn("version changed", self.r.caps.status("codex", "file_write", check_version=True)["reason"])
        self.r.status()
        self.assertFalse(self.r.caps.is_ok("codex", "file_write"))
        self.assertFalse(self.r.caps.is_ok("codex", "worktree"))

    def test_registry_governs_write_eligibility_over_legacy_flags(self):
        self.git_repo()
        cfgp = self.project / "ai-memory/router/config.json"
        c = json.loads(cfgp.read_text())
        for a in ("claude", "codex"):
            c["agents"][a].update(write_verified=False, tools_verified=False)
        cfgp.write_text(json.dumps(c))
        r = ngr.Router(self.project)
        r._update_state(lambda st: st["verified"].__setitem__("codex", {"write": True, "tools": True, "ts": ngr.iso(), "binary": None}))
        self.assertTrue(r.capable("codex", "write"), "legacy probe state is used only while the registry has no data for the agent")
        r.caps.record("codex", "file_write", True, "e", None, "1", "m", {})
        r.caps.record("codex", "worktree", False, "", "no worktree support", "1", "m", {})
        self.assertFalse(r.capable("codex", "write"), "once the registry knows the agent, a failed capability blocks write")
        r.caps.record("codex", "worktree", True, "e", None, "1", "m", {})
        self.assertTrue(r.capable("codex", "write"))
        p = r.plan("Implement x", mode="write", scope=["src/"], persist=False)
        self.assertEqual(p["stages"][0]["slots"][0]["candidates"], ["codex"])

    def test_stale_capability_is_reprobed_once_before_use_and_failover_if_still_bad(self):
        self.git_repo()
        for cap in ("file_write", "worktree", "review"):
            self.rec("codex", cap)
        self.r.caps.mark_stale("codex", ["file_write", "worktree"], "tool version changed")
        calls = []
        def fake_probe(agent, caps=None):
            calls.append((agent, tuple(caps)))
            for c in caps:
                self.rec(agent, c)                        # the re-probe succeeds
            return {}
        self.r.caps.probe = fake_probe
        p = self.r.plan("Implement feature.txt support", mode="write", scope=["feature.txt"], agent="codex")
        self.fakes["codex"].beh = "write"
        res = self.r.execute(p["plan_id"])
        self.assertEqual(calls, [("codex", ("file_write", "worktree"))], "re-probed exactly once, only the stale capabilities")
        self.assertEqual(res["stages"][0]["slots"][0]["attempts"][0]["outcome"], "completed")
        self.r.cleanup(p["plan_id"])
        # re-probe that does NOT fix it -> the agent is skipped (recorded as unavailable) and the next candidate runs
        self.r.caps.mark_stale("codex", ["file_write"], "again")
        self.r._reprobed.clear()
        self.r.caps.probe = lambda agent, caps=None: {}
        p = self.r.plan("Implement feature.txt support v2", mode="write", scope=["feature.txt"])
        self.assertIn("codex", p["stages"][0]["slots"][0]["candidates"])
        for n in ("claude",):
            self.fakes[n].beh = "write"
        res = self.r.execute(p["plan_id"])
        att = res["stages"][0]["slots"][0]["attempts"]
        self.assertTrue(any(a["agent"] == "codex" and a["outcome"] == "unavailable" and "capability not verified" in a["detail"] for a in att) or att[0]["agent"] != "codex", att)
        self.r.cleanup(p["plan_id"])

    def test_observed_tool_failure_makes_tool_capabilities_stale(self):
        for cap in ("file_read", "file_write", "terminal"):
            self.rec("antigravity", cap)
        def broken(binary, prompt, mode, cwd, model, limits, run_dir, tag):
            return [binary, "-c", "import json;print(json.dumps({'text': json.dumps({'summary':'Cannot read the file: tool execution is blocked by a hook error (MODULE_NOT_FOUND)','findings':[]})}))"], None
        self.fakes["antigravity"].build = broken
        p = self.r.plan("Explain the lock", files=["target.txt"], agent="antigravity")
        res = self.r.execute(p["plan_id"])
        self.assertEqual(res["stages"][0]["slots"][0]["attempts"][0]["outcome"], "tool_failure")
        self.assertFalse(self.r.caps.is_ok("antigravity", "file_read"))
        self.assertIn("tool_failure", self.r.caps.status("antigravity", "file_read")["reason"])

    def test_matrix_and_mcp_tools(self):
        self.rec("claude", "file_read")
        m = self.r.caps.matrix()
        self.assertEqual(set(m), set(ngr.AGENTS))
        self.assertTrue(m["claude"]["capabilities"]["file_read"]["ok"])
        self.assertFalse(m["claude"]["capabilities"]["terminal"]["ok"])
        rep = ngr.mcp_reply({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "router_capabilities", "arguments": {}}}, self.project)
        self.assertFalse(rep["result"]["isError"])
        os.environ["NGROUTER_DEPTH"] = "1"
        rep = ngr.mcp_reply({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "router_probe_capabilities", "arguments": {"agent": "claude"}}}, self.project)
        self.assertTrue(rep["result"]["isError"], "a delegated agent may not trigger probes")
        rep = ngr.mcp_reply({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "router_capabilities", "arguments": {}}}, self.project)
        self.assertFalse(rep["result"]["isError"], "but it may read the matrix")


class ProtectedPathsAndProposals(RouterBase):
    def setUp(self):
        super().setUp()
        for d in ("src", "tools", "tests"):
            (self.project / d).mkdir(exist_ok=True)
        (self.project / "src" / "a.py").write_text("x = 1\n")
        (self.project / "tools" / "ngrouter.py").write_text("# policy\n")
        (self.project / "tests" / "test_a.py").write_text("assert True\n")
        (self.project / ".gitignore").write_text("__pycache__/\nai-memory/\n")
        self.git_repo()

    def writer(self, code):
        def build(binary, prompt, mode, cwd, model, limits, run_dir, tag):
            return [binary, "-c", code + ";import json;print(json.dumps({'text': json.dumps({'summary':'done','findings':[]})}))"], None
        return build

    def test_scopes_inside_protected_or_harness_paths_are_rejected(self):
        for sc in (["tools/ngrouter.py"], ["ai-memory/"], ["AGENTS.md"], [".claude/settings.json"], [".git/hooks"], [".mcp.json"], ["tools/ngmem_hook.py"]):
            with self.assertRaises(ngr.RouterError, msg=str(sc)):
                self.r.plan("Implement x", mode="write", scope=sc, persist=False)
        with self.assertRaises(ngr.RouterError):
            self.r.plan("Make the tests pass", mode="write", scope=["tests/"], persist=False)
        self.r.plan("Write tests for a", mode="write", scope=["tests/"], allow_test_changes=True, persist=False)

    def test_agent_changing_a_protected_file_through_a_broad_scope_is_rejected(self):
        p = self.r.plan("Implement x in tools", mode="write", scope=["tools/"], agent="codex")
        self.fakes["codex"].build = self.writer("import pathlib;pathlib.Path('tools/ngrouter.py').write_text('# weakened policy\\n')")
        res = self.r.execute(p["plan_id"])
        att = res["stages"][0]["slots"][0]["attempts"][0]
        self.assertEqual(att["outcome"], "failed")
        self.assertIn("PROTECTED PATH", att["detail"])
        self.r.cleanup(p["plan_id"])

    def test_agent_editing_tests_to_make_them_pass_is_rejected_unless_allowed(self):
        code = "import pathlib;pathlib.Path('src/test_b.py').write_text('assert True\\n')"
        p = self.r.plan("Fix src", mode="write", scope=["src/"], agent="codex")
        self.fakes["codex"].build = self.writer(code)
        att = self.r.execute(p["plan_id"])["stages"][0]["slots"][0]["attempts"][0]
        self.assertEqual(att["outcome"], "failed")
        self.assertIn("TEST/HARNESS FILE", att["detail"])
        self.r.cleanup(p["plan_id"])
        p = self.r.plan("Write the tests for src", mode="write", scope=["src/"], agent="codex", allow_test_changes=True)
        res = self.r.execute(p["plan_id"])
        self.assertEqual(res["stages"][0]["slots"][0]["attempts"][0]["outcome"], "completed")
        self.r.cleanup(p["plan_id"])

    def test_integration_proposal_is_a_clean_binary_safe_patch_and_nothing_is_merged(self):
        code = ("import pathlib;pathlib.Path('src/a.py').write_bytes(b'x = 2\\r\\ny = 3\\r\\n');pathlib.Path('src/new.py').write_text('print(1)\\n')")
        p = self.r.plan("Implement y in src", mode="write", scope=["src/"], agent="codex")
        self.fakes["codex"].build = self.writer(code)
        head = subprocess.run(["git", "-C", str(self.project), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
        res = self.r.execute(p["plan_id"])
        prop = res["proposal"]["slots"]["s1"]
        self.assertTrue(prop["applies_cleanly_to_main"], prop)
        self.assertEqual(sorted(prop["files"]), ["src/a.py", "src/new.py"])
        raw = Path(prop["patch"]).read_bytes()
        self.assertIn(b"x = 2", raw)       # git may normalise CRLF->LF in the patch on Windows (core.autocrlf); the patch is generated as BYTES so nothing else is altered
        self.assertEqual(hashlib.sha256(raw).hexdigest(), prop["sha256"])
        self.assertEqual((self.project / "src" / "a.py").read_text(), "x = 1\n", "main tree untouched")
        self.assertFalse((self.project / "src" / "new.py").exists())
        self.assertEqual(subprocess.run(["git", "-C", str(self.project), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(), head)
        self.assertIn("NOT applied", res["summary"])
        # applying the proposal by hand reproduces the agent's change exactly
        subprocess.run(["git", "-C", str(self.project), "apply", prop["patch"]], check=True, capture_output=True)
        self.assertEqual((self.project / "src" / "a.py").read_bytes().replace(b"\r\n", b"\n"), b"x = 2\ny = 3\n")
        self.r.cleanup(p["plan_id"])

    def test_proposal_reports_when_the_main_tree_moved_on(self):
        p = self.r.plan("Implement y in src", mode="write", scope=["src/"], agent="codex")
        self.fakes["codex"].build = self.writer("import pathlib;pathlib.Path('src/a.py').write_text('x = 2\\n')")
        orig_exec = self.r._run_slot
        def meddle(*a, **k):
            out = orig_exec(*a, **k)
            (self.project / "src" / "a.py").write_text("x = 99\n")        # a human edits the same line meanwhile
            return out
        self.r._run_slot = meddle
        res = self.r.execute(p["plan_id"])
        prop = res["proposal"]["slots"]["s1"]
        self.assertFalse(prop["applies_cleanly_to_main"])
        self.assertTrue(prop["apply_check"])
        self.r.cleanup(p["plan_id"])

    def test_reviewer_sees_new_untracked_files_in_the_diff(self):
        seen = {}
        def spy(binary, prompt, mode, cwd, model, limits, run_dir, tag):
            seen[mode] = prompt
            return [binary, "-c", "import json;print(json.dumps({'text': json.dumps({'summary':'ok','verdict':'confirmed','findings':[]})}))"], None
        p = self.r.plan("Implement a brand new module in src", mode="write", scope=["src/"], agent="codex", coordinator="claude")
        self.fakes["codex"].build = self.writer("import pathlib;pathlib.Path('src/brand_new_module.py').write_text('print(42)\\n')")
        for n in ("antigravity",):
            self.fakes[n].build = spy
        res = self.r.execute(p["plan_id"])
        self.assertEqual(res["status"], "completed", res["summary"])
        self.assertIn("brand_new_module.py", seen["read-only"], "the reviewer prompt contains the diff of the NEW file")
        self.r.cleanup(p["plan_id"])


class AntigravityAdapterSafety(RouterBase):
    def setUp(self):
        super().setUp()
        self.prof = Path(self.tmp.name) / "agyprofile"
        self.ad = self.saved["antigravity"]            # the real adapter object (the base class replaced it by a fake)

    def argv(self, ad, mode, prompt="hi"):
        return ad.build("agy", prompt, mode, str(self.project), None, self.r.lim, Path(self.tmp.name), "t")[0]

    def test_permission_bypass_and_workspace_widening_flags_are_never_used_by_any_adapter(self):
        banned = ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--dangerously-bypass-approvals-and-sandbox", "--add-dir", "--sandbox=danger-full-access", "bypassPermissions")
        for name, ad in self.saved.items():
            for mode in ("read-only", "write"):
                av = " ".join(str(x) for x in ad.build(name, "hi", mode, str(self.project), None, self.r.lim, Path(self.tmp.name), "t")[0])
                for b in banned:
                    self.assertNotIn(b, av, f"{name}/{mode}")
        self.assertNotIn("danger-full-access", " ".join(self.argv(self.saved["codex"], "write")))

    def test_allow_rules_are_exact_commands_only_never_wildcards(self):
        self.ad.prepare(self.prof, [["python", "-m", "unittest"], ["git", "rev-parse", "HEAD"], ["python", "-m", "unittest"]])
        cfg = json.loads((self.prof / ".gemini" / "antigravity-cli" / "settings.json").read_text())
        self.assertEqual(cfg["permissions"]["allow"], ["command(git rev-parse HEAD)", "command(python -m unittest)"])
        self.assertFalse(any("*" in x for x in cfg["permissions"]["allow"]))
        self.ad.prepare(self.prof, [])
        self.assertEqual(json.loads((self.prof / ".gemini" / "antigravity-cli" / "settings.json").read_text())["permissions"]["allow"], [])

    def test_isolated_profile_never_points_at_the_real_user_profile(self):
        cfgp = self.project / "ai-memory/router/config.json"
        c = json.loads(cfgp.read_text())
        c["agents"]["antigravity"]["profile_dir"] = str(self.prof)
        cfgp.write_text(json.dumps(c))
        r = ngr.Router(self.project)
        env = r._env("t", "antigravity")
        self.assertEqual(env["USERPROFILE"], str(self.prof))
        self.assertEqual(env["HOME"], str(self.prof))
        self.assertNotEqual(env["USERPROFILE"], str(Path.home()))
        self.assertEqual(r._env("t", "claude").get("USERPROFILE"), os.environ.get("USERPROFILE"), "other agents keep the normal profile")
        self.assertIsNone(r.agent_profile_dir("claude"))

    def test_oversize_prompt_fails_cleanly_and_fails_over(self):
        self.r.lim["prompt_arg_max_chars"] = 200
        with self.assertRaises(ValueError):
            self.ad.build("agy", "x" * 500, "read-only", str(self.project), None, self.r.lim, Path(self.tmp.name), "t")
        # inside a real attempt the ValueError becomes a recorded failure
        orig = self.fakes["codex"].build
        def too_big(*a, **k):
            raise ValueError("prompt is 99999 chars")
        self.fakes["codex"].build = too_big
        p = self.r.plan("Explain the lock function", agent="codex")
        res = self.r.execute(p["plan_id"])
        att = res["stages"][0]["slots"][0]["attempts"]
        self.assertEqual(att[0]["outcome"], "failed")
        self.assertIn("prompt is", att[0]["detail"])
        self.assertEqual(res["status"], "completed", "the next agent took over")

    def test_denied_actions_are_reported_as_explicit_error_not_empty_success(self):
        out = self.ad.parse({"stdout": json.dumps({"conversation_id": "c", "status": "SUCCESS", "response": "", "usage": {}, "denied_actions": [{"action": "command", "display_name": "RunCommand"}]}), "stderr": "", "rc": 0})
        self.assertIn("permission denied", out["error"])
        ok = self.ad.parse({"stdout": json.dumps({"conversation_id": "c", "status": "SUCCESS", "response": "{}", "usage": {}}), "stderr": "", "rc": 0})
        self.assertIsNone(ok["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
