"""v1.3 collaboration tests: routing explanations, decomposition, bounded correction rounds, disputes, crash + resume, process-tree safety, handoffs."""
import json
import os
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_router import RouterBase, ngr  # noqa: E402

OK_JSON = "import json;print(json.dumps({'text': json.dumps(%s)}))"
W2 = "import pathlib;pathlib.Path('src/a.py').write_text('x = 2')"
W3 = "import pathlib;pathlib.Path('src/a.py').write_text('x = 3')"


def cmd(code, result):
    return lambda binary, prompt, mode, cwd, model, limits, run_dir, tag: ([binary, "-c", code + ";" + OK_JSON % json.dumps(result)], None)


def seq(tmp, steps):
    """Builder whose n-th call runs steps[n] = (code, result_dict); the last step repeats."""
    counter = Path(tmp) / f"seq_{id(steps)}"
    counter.write_text("0")

    def build(binary, prompt, mode, cwd, model, limits, run_dir, tag):
        n = int(counter.read_text())
        counter.write_text(str(n + 1))
        code, result = steps[min(n, len(steps) - 1)]
        return [binary, "-c", code + ";" + OK_JSON % json.dumps(result)], None
    return build


class Explanations(RouterBase):
    def test_plan_explains_choice_with_evidence_and_records_a_decision_event(self):
        self.seed("codex", "debug", [1.0], n=4)
        p = self.r.plan("Fix the crash bug in the parser", files=["target.txt"], task_class="debug")
        text = " ".join(p["explanation"])
        self.assertIn(p["decision"]["selected"], text)
        self.assertIn("No agent has a fixed role", text)
        self.assertIn("debug", text)
        self.assertTrue(any(e.get("type") == "decision" and e["task_id"] == p["plan_id"] and e["ranking"] for e in self.r.events()))

    def test_exclusions_are_explained(self):
        p = self.r.plan("Explain the lock", simulate_unavailable={"codex"}, persist=False)
        self.assertIn("codex", " ".join(p["explanation"]))
        self.assertIn("SIMULATED", p["decision"]["excluded"]["codex"])

    def test_outcome_assessment_marks_failover_as_not_justified_and_feeds_the_registry(self):
        p = self.r.plan("Explain the lock function")
        first = p["decision"]["selected"]
        self.fakes[first].beh = "authfail"
        res = self.r.execute(p["plan_id"])
        self.assertFalse(res["decision_outcome"]["justified"])
        self.assertIn("failover", res["decision_outcome"]["reason"])
        p2 = self.r.plan("Explain the lock function again")
        self.r.execute(p2["plan_id"])
        rep = self.r.registry_report()["agents"]
        self.assertTrue(any("routing_decisions" in v for v in rep.values() if isinstance(v, dict)))
        self.assertTrue(any(e.get("type") == "decision_outcome" for e in self.r.events()))


class Decomposition(RouterBase):
    def test_numbered_independent_items_run_in_parallel(self):
        t = "Review these three areas:\n1. Check the lock handling in target.txt\n2. Check the error paths in target.txt\n3. Check the naming conventions in target.txt"
        p = self.r.plan(t, persist=False)
        self.assertEqual(p["strategy"], "parallel")
        self.assertEqual(len(p["stages"][0]["slots"]), 3)
        self.assertIn("automatically", " ".join(p["rationale"]))

    def test_dependent_or_single_or_write_tasks_are_not_split(self):
        dep = "Do this:\n1. First read the module and summarise it\n2. Then fix the problems based on the result of step 1"
        self.assertEqual(self.r.plan(dep, persist=False)["strategy"], "single")
        self.assertEqual(self.r.plan("Explain the lock function", persist=False)["strategy"], "single")
        self.git_repo()
        w = "Implement:\n1. Add the feature flag to the config module\n2. Add the feature flag to the UI module"
        self.assertEqual(self.r.plan(w, mode="write", scope=["src/"], persist=False)["strategy"], "single")
        t = "Review:\n1. Check the lock handling in target.txt\n2. Check the error paths in target.txt"
        self.assertEqual(self.r.plan(t, auto_decompose=False, persist=False)["strategy"], "single")


class Collaboration(RouterBase):
    def setUp(self):
        super().setUp()
        (self.project / "src").mkdir(exist_ok=True)
        (self.project / "src" / "a.py").write_text("x = 1\n")
        (self.project / ".gitignore").write_text("__pycache__/\nai-memory/\n")
        self.git_repo()
        self.prompts = []

    def reviewer_sequence(self, verdicts):
        counter = Path(self.tmp.name) / "rev_counter"
        counter.write_text("0")
        prompts = self.prompts

        def build(binary, prompt, mode, cwd, model, limits, run_dir, tag):
            prompts.append(prompt)
            n = int(counter.read_text())
            counter.write_text(str(n + 1))
            v = verdicts[min(n, len(verdicts) - 1)]
            res = {"summary": "review", "verdict": v, "findings": ([{"title": "off by one", "severity": "high", "file": "src/a.py", "line": 1, "quote": "x =", "detail": "d"}] if v == "issues_found" else [])}
            return [binary, "-c", OK_JSON % json.dumps(res)], None
        return build

    def plan_write(self, task="Implement the change in src", agent="codex"):
        return self.r.plan(task, mode="write", scope=["src/"], agent=agent, coordinator="claude")

    def test_reviewer_finds_issue_another_agent_corrects_and_rereview_approves(self):
        p = self.plan_write()
        # codex is the executor and (as the best-ranked non-reviewer) also the corrector: its builder is stateful
        self.fakes["codex"].build = seq(self.tmp.name, [(W2, {"summary": "v1", "findings": []}), (W3, {"summary": "v2 fixed", "findings": []})])
        self.fakes["antigravity"].build = self.reviewer_sequence(["issues_found", "confirmed"])
        res = self.r.execute(p["plan_id"])
        self.assertEqual(res["status"], "completed", res["summary"])
        self.assertEqual(len(res["rounds"]), 1)
        rnd = res["rounds"][0]
        self.assertNotEqual(rnd["corrector"], rnd["reviewer"])
        self.assertEqual(rnd["outcome"], "confirmed")
        self.assertIn("approved after 1 correction round", res["independent_review"])
        self.assertEqual((Path(res["worktrees"]["s1"]) / "src" / "a.py").read_text().strip(), "x = 3", "the correction landed in the SAME worktree")
        fenced = [pr for pr in self.prompts if "UNTRUSTED_EXECUTOR_CLAIMS" in pr]
        self.assertEqual(len(fenced), 2, "the reviewer ran twice, each time with fenced untrusted claims")
        self.assertIn("src/a.py", res["proposal"]["slots"]["s1"]["files"])
        self.r.cleanup(p["plan_id"])

    def test_rounds_are_bounded_and_unresolved_issues_stay_flagged(self):
        p = self.plan_write()
        self.fakes["codex"].build = seq(self.tmp.name, [(W2, {"summary": "v1", "findings": []}), (W3, {"summary": "v2", "findings": []})])
        self.fakes["antigravity"].build = self.reviewer_sequence(["issues_found"])          # never satisfied
        res = self.r.execute(p["plan_id"])
        self.assertEqual(res["status"], "review_issues")
        self.assertEqual(len(res["rounds"]), self.r.lim["max_correction_rounds"])
        self.assertIn("still open", res["independent_review"])
        reviewer_runs = [e for e in self.r.events() if e.get("type") == "run" and e["task_id"] == p["plan_id"] and e["role"] == "reviewer"]
        self.assertEqual(len(reviewer_runs), 1 + self.r.lim["max_correction_rounds"], "no infinite discussion")
        self.r.cleanup(p["plan_id"])

    def test_corrector_can_dispute_a_finding_with_evidence_and_the_dispute_reaches_the_rereview(self):
        p = self.plan_write()
        disp = {"summary": "no change needed", "findings": [], "disputed": [{"title": "off by one", "reason": "the loop bound is inclusive by design, see line 1"}]}
        self.fakes["codex"].build = seq(self.tmp.name, [(W2, {"summary": "v1", "findings": []}), ("pass", disp)])
        self.fakes["antigravity"].build = self.reviewer_sequence(["issues_found", "confirmed"])
        res = self.r.execute(p["plan_id"])
        self.assertTrue(res["rounds"][0]["disputed"])
        self.assertIn("inclusive by design", self.prompts[-1])
        self.assertIn("disputed_findings", self.prompts[-1])
        self.r.cleanup(p["plan_id"])

    def test_a_correction_that_leaves_scope_fails_without_retry_and_keeps_the_tree_for_humans(self):
        p = self.plan_write()
        self.fakes["codex"].build = seq(self.tmp.name, [(W2, {"summary": "v1", "findings": []}),
                                                        ("import pathlib;pathlib.Path('outside.txt').write_text('x')", {"summary": "oops", "findings": []})])
        self.fakes["antigravity"].build = self.reviewer_sequence(["issues_found", "confirmed"])
        res = self.r.execute(p["plan_id"])
        self.assertEqual(res["rounds"][0]["outcome"], "correction_failed")
        self.assertEqual(res["status"], "review_issues")
        cs = [sg for sg in res["stages"] if sg["role"] == "corrector"][0]["slots"][0]
        self.assertEqual(len(cs["attempts"]), 1, "no automatic retry on a modified tree")
        self.assertIn("kept as-is", cs["escalation"])
        self.r.cleanup(p["plan_id"])

    def test_read_only_and_non_review_plans_have_no_rounds(self):
        res = self.r.execute(self.r.plan("Explain the lock function")["plan_id"])
        self.assertEqual(res["rounds"], [])


class CrashAndResume(RouterBase):
    def setUp(self):
        super().setUp()
        (self.project / "src").mkdir(exist_ok=True)
        (self.project / "src" / "a.py").write_text("x = 1\n")
        (self.project / ".gitignore").write_text("__pycache__/\nai-memory/\n")
        self.git_repo()

    DRIVER = textwrap.dedent('''
        import os, sys, json
        from pathlib import Path
        sys.path.insert(0, __TESTS__)
        from test_router import FakeAdapter, ngr
        for n in ngr.AGENTS:
            ngr.ADAPTERS[n] = FakeAdapter(n)
        proj = Path(__PROJ__)
        r = ngr.Router(proj)
        p = r.plan("Implement the change in src", mode="write", scope=["src/"], agent="codex", coordinator="claude")

        def die_when_reviewing(orig):
            def build(binary, prompt, mode, cwd, model, limits, run_dir, tag):
                if mode == "read-only":
                    os._exit(9)                   # the router process vanishes mid-plan (no finally blocks, no cleanup)
                return orig(binary, prompt, mode, cwd, model, limits, run_dir, tag)
            return build
        for n in ("antigravity", "claude"):
            ngr.ADAPTERS[n].build = die_when_reviewing(ngr.ADAPTERS[n].build)
        code = "import pathlib,json;pathlib.Path('src/a.py').write_text('x = 2');print(json.dumps({'text': json.dumps({'summary':'done','findings':[]})}))"
        ngr.ADAPTERS["codex"].build = lambda binary, prompt, mode, cwd, model, limits, run_dir, tag: ([binary, "-c", code], None)
        print(p["plan_id"], flush=True)
        r.execute(p["plan_id"])
    ''')

    def test_router_killed_mid_plan_resumes_from_the_last_completed_stage(self):
        drv = self.DRIVER.replace("__TESTS__", repr(str(Path(__file__).resolve().parent))).replace("__PROJ__", repr(str(self.project)))
        out = subprocess.run([sys.executable, "-c", drv], capture_output=True, text=True, timeout=300)
        self.assertEqual(out.returncode, 9, f"stdout={out.stdout[-300:]!r} stderr={out.stderr[-900:]!r}")
        plan_id = out.stdout.split()[0]
        self.assertEqual(self.r.load_plan(plan_id)["status"], "running", "the abrupt death left the plan 'running'")
        prog = json.loads((self.project / f"ai-memory/router/runs/{plan_id}.progress.json").read_text())
        self.assertEqual(prog["next_stage"], 1, "the executor stage was durably recorded before the crash")
        self.assertTrue(Path(next(iter(prog["worktrees"].values()))).is_dir())
        exec_runs_before = [e for e in self.r.events() if e.get("type") == "run" and e["task_id"] == plan_id]
        # resume in this (new) process: the dead pid is detected, reviewers now work
        r2 = ngr.Router(self.project)
        res = r2.resume(plan_id)
        self.assertEqual(res["status"], "completed", res["summary"])
        runs = [e for e in r2.events() if e.get("type") == "run" and e["task_id"] == plan_id]
        self.assertEqual(sum(1 for e in runs if e["role"] == "executor"), sum(1 for e in exec_runs_before if e["role"] == "executor"), "the executor was NOT run again")
        self.assertTrue(any(e["role"] == "reviewer" and e["run_id"].endswith(".r1") for e in runs), "the new run ids carry the resume suffix")
        self.assertEqual(r2._state()["tasks"][plan_id]["resumes"], 1)
        self.assertIn("RESUME", (self.project / "ai-memory" / "LOG.md").read_text())
        r2.cleanup(plan_id)

    def test_resume_refuses_live_runs_finished_plans_and_excess_resumes(self):
        p = self.r.plan("Explain the lock function")
        with self.assertRaises(ngr.RouterError):
            self.r.resume(p["plan_id"])                      # planned, never ran
        self.r.execute(p["plan_id"])
        with self.assertRaises(ngr.RouterError):
            self.r.resume(p["plan_id"])                      # finished
        p2 = self.r.plan("Explain the lock function second")
        pl = self.r.load_plan(p2["plan_id"])
        pl["status"] = "running"
        self.r._save_plan(pl)
        pf = self.project / f"ai-memory/router/runs/{p2['plan_id']}.progress.json"
        pf.write_text(json.dumps({"pid": os.getpid(), "next_stage": 0, "results": {"plan_id": p2["plan_id"], "stages": []}}))
        self.r._update_state(lambda s: s["tasks"].__setitem__(p2["plan_id"], {"heartbeat": time.time(), "running": True}))
        with self.assertRaises(ngr.RouterError) as cm:
            self.r.resume(p2["plan_id"])
        self.assertIn("alive", str(cm.exception))
        pf.write_text(json.dumps({"pid": 99999999, "next_stage": 0, "results": {"plan_id": p2["plan_id"], "stages": []}}))
        self.r._update_state(lambda s: s["tasks"][p2["plan_id"]].update(heartbeat=0, resumes=self.r.lim["max_resumes"]))
        with self.assertRaises(ngr.RouterError) as cm:
            self.r.resume(p2["plan_id"])
        self.assertIn("already resumed", str(cm.exception))

    def test_pid_alive(self):
        self.assertTrue(ngr.pid_alive(os.getpid()))
        self.assertFalse(ngr.pid_alive(99999999))
        self.assertFalse(ngr.pid_alive(0))

    @unittest.skipUnless(os.name == "nt", "Windows job object")
    def test_agent_processes_die_with_the_router(self):
        pidfile = Path(self.tmp.name) / "child.pid"
        code = textwrap.dedent('''
            import os, sys, threading, time
            from pathlib import Path
            sys.path.insert(0, __TOOLS__)
            import ngrouter as ngr
            pidfile = Path(__PIDFILE__)
            child = "import os,time;open(r'%s','w').write(str(os.getpid()));time.sleep(120)" % pidfile
            threading.Thread(target=lambda: ngr.run_proc([sys.executable, "-c", child], os.getcwd(), dict(os.environ), None, 200), daemon=True).start()
            for _ in range(100):
                if pidfile.exists() and pidfile.read_text():
                    break
                time.sleep(0.1)
            os._exit(0)                       # abrupt death of the "router"
        ''').replace("__TOOLS__", repr(str(Path(ngr.__file__).resolve().parent))).replace("__PIDFILE__", repr(pidfile.as_posix()))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        pids = [int(pidfile.read_text())] if pidfile.exists() and pidfile.read_text().isdigit() else []
        self.assertTrue(pids, "the agent child never started: " + out.stderr[-300:])
        time.sleep(1.5)
        self.assertFalse(any(ngr.pid_alive(p) for p in pids), f"agent child survived the router: {pids}")


class Handoffs(RouterBase):
    def setUp(self):
        super().setUp()
        (self.project / "src").mkdir(exist_ok=True)
        (self.project / "src" / "a.py").write_text("x = 1\n")
        (self.project / ".gitignore").write_text("__pycache__/\nai-memory/\n")
        self.git_repo()

    def test_write_plan_produces_a_unified_handoff_without_promoting_decisions(self):
        before_dec = (self.project / "ai-memory" / "DECISIONS.md").read_text(encoding="utf-8")
        p = self.r.plan("Implement the change in src — إضافة ميزة جديدة ✓", mode="write", scope=["src/"], agent="codex", coordinator="claude", verify_cmd="fake-ok")
        self.fakes["codex"].build = cmd(W2, {"summary": "done", "findings": []})
        self.r.execute(p["plan_id"])
        h = (self.project / "ai-memory" / "HANDOFF.md").read_text(encoding="utf-8")
        for needle in (p["plan_id"], "Executed by: codex", "Reviewed by:", "NOT modified", "src/a.py", "1 passed", "إضافة ميزة جديدة ✓", "git apply"):
            self.assertIn(needle, h, needle)
        self.assertEqual((self.project / "ai-memory" / "DECISIONS.md").read_text(encoding="utf-8"), before_dec, "no decision was written or promoted")
        self.assertIn("nothing is user-approved", h)
        self.r.cleanup(p["plan_id"])

    def test_previous_handoff_is_archived_not_lost(self):
        old = "OLD-HANDOFF-MARKER-12345"
        subprocess.run([sys.executable, str(self.project / "tools" / "ngmem.py"), "--project", str(self.project), "handoff", "--agent", "human", "--task", old, "--completed", "c",
                        "--next-steps", "n", "--tests", "t"], check=True, capture_output=True)
        p = self.r.plan("Implement the change in src", mode="write", scope=["src/"], agent="codex")
        self.fakes["codex"].beh = "write"
        self.r.execute(p["plan_id"])
        self.assertNotIn(old, (self.project / "ai-memory" / "HANDOFF.md").read_text(encoding="utf-8"))
        self.assertIn(old, (self.project / "ai-memory" / "LOG.md").read_text(encoding="utf-8"), "archived into LOG by the memory server")
        self.r.cleanup(p["plan_id"])

    def test_read_only_plans_do_not_touch_the_handoff_unless_configured(self):
        before = (self.project / "ai-memory" / "HANDOFF.md").read_text(encoding="utf-8")
        self.r.execute(self.r.plan("Explain the lock function")["plan_id"])
        self.assertEqual((self.project / "ai-memory" / "HANDOFF.md").read_text(encoding="utf-8"), before)
        cfgp = self.project / "ai-memory/router/config.json"
        c = json.loads(cfgp.read_text())
        c["auto_handoff"] = "all"
        cfgp.write_text(json.dumps(c))
        r2 = ngr.Router(self.project)
        r2.execute(r2.plan("Explain the lock function a second time")["plan_id"])
        self.assertNotEqual((self.project / "ai-memory" / "HANDOFF.md").read_text(encoding="utf-8"), before)
        c["auto_handoff"] = "off"
        cfgp.write_text(json.dumps(c))
        h2 = (self.project / "ai-memory" / "HANDOFF.md").read_text(encoding="utf-8")
        r3 = ngr.Router(self.project)
        r3.execute(r3.plan("Explain the lock function a third time")["plan_id"])
        self.assertEqual((self.project / "ai-memory" / "HANDOFF.md").read_text(encoding="utf-8"), h2)

    def test_handoff_failure_never_masks_the_result(self):
        (self.project / "tools" / "ngmem.py").unlink()
        res = self.r.execute(self.r.plan("Implement the change in src", mode="write", scope=["src/"], agent="codex")["plan_id"])
        self.assertIn(res["status"], ("completed", "review_required", "failed"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
