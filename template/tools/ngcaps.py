#!/usr/bin/env python3
"""NextGen AI Team v1.3 — live capability registry and probe suite.

A capability is never taken from an agent's self-description. Each one is verified by a real, authenticated, headless run in a
disposable Git repository and the result is re-checked independently by the router (file contents, hashes, Git state).
Registry: <project>/ai-memory/router/capabilities.json. Entries expire (TTL), go stale when the tool version changes, and are marked
stale after relevant failures so the next use re-probes them. Quotas are never inferred.
"""
from __future__ import annotations
import contextlib
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import sys
from typing import Any

CAPS = ("auth", "headless", "structured_json", "file_read", "file_write", "terminal", "test_exec", "git", "mcp", "review", "worktree")
TTL_DAYS = 14
# which capabilities the router needs for which kind of work
NEEDS = {"read-only": ("auth", "headless", "structured_json"), "write": ("auth", "headless", "structured_json", "file_read", "file_write", "worktree"),
         "review": ("auth", "headless", "structured_json", "review")}


def _iso(t: float | None = None) -> str:
    return dt.datetime.fromtimestamp(t or time.time(), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(s: str) -> float:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc).timestamp()


class CapabilityEngine:
    def __init__(self, router: Any):
        self.r = router
        self.path = Path(router.dir) / "capabilities.json"

    # ── registry ──
    def load(self) -> dict:
        if self.path.is_file():
            with contextlib.suppress(ValueError):
                return json.loads(self.path.read_text(encoding="utf-8"))
        return {"version": 1, "agents": {}}

    @property
    def ngr(self):
        """The module that defines the router (works whether it was imported as ngrouter or run as __main__)."""
        return sys.modules[type(self.r).__module__]

    def _save(self, data: dict) -> None:
        self.ngr.atomic_write(self.path, json.dumps(data, indent=1))     # callers hold _txn(): load-modify-save must be one locked unit

    def _entry(self, agent: str) -> dict:
        return self.load()["agents"].get(agent) or {}

    def current_version(self, agent: str) -> str | None:
        ad = self.r.adapters[agent]
        binary = (self.r._state()["detect"].get(agent) or {}).get("binary") or ad.find_binary(self.r.cfg["agents"][agent].get("binary"))
        return ad.version(binary, self.r._env("version", agent)) if binary else None

    def status(self, agent: str, cap: str, check_version: bool = False) -> dict:
        """-> {ok, reason}; ok only if verified, not expired, not stale and (optionally) the tool version still matches."""
        e = self._entry(agent)
        c = (e.get("capabilities") or {}).get(cap)
        if not c:
            return {"ok": False, "reason": "never verified"}
        if not c.get("ok"):
            return {"ok": False, "reason": c.get("failure") or "failed verification"}
        if c.get("stale"):
            return {"ok": False, "reason": f"stale: {c['stale']}"}
        if time.time() > _epoch(c["expires_at"]):
            return {"ok": False, "reason": "expired; re-probe required"}
        if check_version:
            cur = self.current_version(agent)
            if cur and e.get("tool_version") and cur != e["tool_version"]:
                return {"ok": False, "reason": f"tool version changed {e['tool_version']} -> {cur}; re-probe required"}
        return {"ok": True, "reason": "verified " + c["verified_at"]}

    def is_ok(self, agent: str, cap: str) -> bool:
        return self.status(agent, cap)["ok"]

    def _txn(self):
        return self.ngr.file_lock(Path(self.r.dir) / ".caps-lock")

    def mark_stale(self, agent: str, caps: tuple[str, ...] | list[str], reason: str) -> None:
        with self._txn():
            self._mark_stale(agent, caps, reason)

    def _mark_stale(self, agent: str, caps, reason: str) -> None:
        data = self.load()
        ent = data["agents"].get(agent)
        if not ent:
            return
        for cap in caps:
            c = ent["capabilities"].get(cap)
            if c and c.get("ok"):
                c["stale"] = reason
        self._save(data)

    def record(self, agent: str, cap: str, ok: bool, evidence: str, failure: str | None, tool_version: str | None, model: str | None, env: dict) -> None:
        with self._txn():
            self._record(agent, cap, ok, evidence, failure, tool_version, model, env)

    def _record(self, agent: str, cap: str, ok: bool, evidence: str, failure: str | None, tool_version: str | None, model: str | None, env: dict) -> None:
        data = self.load()
        ent = data["agents"].setdefault(agent, {"capabilities": {}})
        ent.update(tool_version=tool_version or ent.get("tool_version"), model=model or ent.get("model") or "not reported by the CLI", environment=env)
        now = time.time()
        ent["capabilities"][cap] = {"ok": bool(ok), "verified_at": _iso(now), "expires_at": _iso(now + TTL_DAYS * 86400), "evidence": evidence[:300],
                                    "failure": None if ok else (failure or "failed")[:300], "stale": None}
        self._save(data)

    def matrix(self) -> dict:
        out = {}
        for agent in self.r.adapters:
            e = self._entry(agent)
            out[agent] = {"tool_version": e.get("tool_version"), "model": e.get("model"), "environment": e.get("environment"),
                          "capabilities": {cap: {**self.status(agent, cap), **{k: (e.get("capabilities", {}).get(cap) or {}).get(k) for k in ("verified_at", "expires_at", "evidence")}} for cap in CAPS}}
        return out

    # ── probes ──
    def probe(self, agent: str, caps: tuple[str, ...] | list[str] | None = None) -> dict:
        """Run the live probe suite for one agent (about 4 model runs). Every outcome is verified independently by the router."""
        ngr = self.ngr
        Router, run_proc, extract_json, RouterError = ngr.Router, ngr.run_proc, ngr.extract_json, ngr.RouterError
        r = self.r
        if agent not in r.adapters:
            raise RouterError(f"Unknown agent: {agent}")
        want = tuple(caps) if caps else CAPS
        ad = r.adapters[agent]
        acfg = r.cfg["agents"][agent]
        binary = ad.find_binary(acfg.get("binary"))
        env_rec = {"os": os.name, "binary": binary, "profile": str(r.agent_profile_dir(agent) or "user profile"), "cwd_isolated": True}
        ver = self.current_version(agent)
        model = None
        results: dict[str, tuple[bool, str, str | None]] = {}

        def put(cap, ok, ev, fail=None):
            if cap in want:
                results[cap] = (ok, ev if ok else "", fail)       # a failed capability never carries a success description

        if not binary:
            for cap in want:
                put(cap, False, "", "CLI not installed")
            return self._finish(agent, results, ver, model, env_rec)
        ok, detail = ad.auth_check(binary, r._env("probe", agent))
        put("auth", ok, detail, None if ok else detail)
        if not ok:
            for cap in want:
                if cap != "auth":
                    put(cap, False, "", "not authenticated")
            return self._finish(agent, results, ver, model, env_rec)

        with tempfile.TemporaryDirectory(prefix="ngp-") as td:
            proj = Path(td) / "p"
            (proj / "ai-memory").mkdir(parents=True)
            token, nonce, n_extra = "TOK-" + secrets.token_hex(10), secrets.token_hex(6), secrets.randbelow(4) + 2
            hashnonce = "HN-" + secrets.token_hex(6)
            (proj / "token.txt").write_text(token + "\n", encoding="utf-8")
            (proj / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            marks = "".join(f"\n    def test_m{i}(self):\n        self.assertTrue(True)\n" for i in range(n_extra))
            (proj / "test_calc.py").write_text("import unittest\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n" + marks, encoding="utf-8")
            (proj / "proof_input.txt").write_text(hashnonce + "\n", encoding="utf-8")
            (proj / "PROTECTED.txt").write_text("protected " + secrets.token_hex(4) + "\n", encoding="utf-8")
            (proj / ".gitignore").write_text("__pycache__/\nai-memory/\n", encoding="utf-8")
            for a in (["init", "-q"], ["config", "user.email", "probe@example.invalid"], ["config", "user.name", "probe"], ["add", "-A"], ["commit", "-qm", "base"]):
                subprocess.run(["git", "-C", str(proj), *a], check=True, capture_output=True)
            head = subprocess.run(["git", "-C", str(proj), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
            pr = Router(proj)
            pr.cfg["agents"][agent] = {**r.cfg["agents"][agent]}
            pr.cfg["agent_command_allow"] = []
            tail = ' Reply with ONLY one JSON object: {"answer": "<value>"} (no prose).'

            def run(prompt, mode, cwd, allow, timeout=420):
                ad.prepare(pr.agent_profile_dir(agent), [list(a) for a in allow])
                argv, stdin = ad.build(binary, prompt, mode, str(cwd), acfg.get("model"), pr.lim, Path(td), "cap")
                res = run_proc(argv, str(cwd), pr._env("probe", agent), stdin, timeout)
                return res, (ad.parse(res) if not res["spawn_error"] else {"text": "", "error": res["stderr"], "tokens": {}})

            # P1 headless + structured JSON + file read (unpredictable token the model cannot know)
            res, parsed = run("Use your file-reading tool (not a terminal) to read token.txt in the current directory." + tail, "read-only", proj, [])
            obj = extract_json(parsed.get("text", "")) or {}
            model = parsed.get("model") or (ad.configured_model() if hasattr(ad, "configured_model") else None) or model
            put("headless", res["rc"] == 0 and not res["timed_out"] and not parsed.get("error"), f"rc={res['rc']} in {res['latency_s']}s", parsed.get("error") or ("timeout" if res["timed_out"] else f"rc={res['rc']}"))
            put("structured_json", isinstance(obj, dict) and "answer" in obj, "JSON object with 'answer' parsed", "no parseable JSON object")
            put("file_read", str(obj.get("answer", "")).strip() == token, "returned the exact random token from token.txt", f"returned {str(obj.get('answer'))[:30]!r}, expected the file's content")
            clean = subprocess.run(["git", "-C", str(proj), "status", "--porcelain"], capture_output=True, text=True).stdout.strip() == ""
            if not clean:
                put("file_read", False, "", "READ-ONLY run modified the repository")

            # P2 write in a REAL router worktree: file edit, isolation (main untouched), scope
            if any(c in want for c in ("file_write", "worktree", "terminal", "test_exec", "git")):
                try:
                    wt = pr.make_worktree("ngr-00000000-000000-0000", "s1", "")
                except RouterError as exc:
                    wt = None
                    put("worktree", False, "", str(exc))
                    put("file_write", False, "", "no worktree")
                if wt is not None:
                    main_fp = pr.repo_fingerprint("", 0)
                    res, parsed = run("Fix the bug in calc.py so add(a, b) returns a + b, using your file editing tools (not a terminal). Edit ONLY calc.py." + tail, "write", wt, [])
                    changed = [l[3:] for l in subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True).stdout.splitlines()]
                    fixed = (wt / "calc.py").read_text(encoding="utf-8") if (wt / "calc.py").exists() else ""
                    prot = (wt / "PROTECTED.txt").read_text(encoding="utf-8") == (proj / "PROTECTED.txt").read_text(encoding="utf-8")
                    put("file_write", "return a + b" in fixed and changed == ["calc.py"] and prot, "calc.py edited in the worktree; only that file changed; protected file intact", f"changed={changed} fixed={'return a + b' in fixed} err={str(parsed.get('error'))[:100]}")
                    put("worktree", main_fp == pr.repo_fingerprint("", 0) and (proj / "calc.py").read_text(encoding="utf-8").count("a - b") == 1,
                        "agent edits stayed in the isolated worktree; main repository fingerprint unchanged", "main repository changed")
                    # P3 terminal / test execution / git through EXACT allow-listed commands
                    if any(c in want for c in ("terminal", "test_exec", "git")):
                        if not ad.grants_shell("write"):
                            for c in ("terminal", "test_exec", "git"):
                                put(c, False, "", "disabled by router policy: this agent runs without a shell tool (deliberate isolation); not a CLI limitation")
                        else:
                            cmds = [["git", "hash-object", "proof_input.txt"], ["python", "-m", "unittest"], ["git", "rev-parse", "HEAD"]]
                            expect_hash = subprocess.run(["git", "hash-object", "proof_input.txt"], cwd=wt, capture_output=True, text=True).stdout.strip()
                            expect_n = n_extra + 1
                            expect_head = subprocess.run(["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
                            res, parsed = run("Using your terminal / command tool, run these three commands one at a time, exactly as written, and report their results: "
                                              "(1) git hash-object proof_input.txt   (2) python -m unittest   (3) git rev-parse HEAD . "
                                              'Reply with ONLY one JSON object: {"hash": "<output of 1>", "tests_ran": <number of tests from 2>, "head": "<output of 3>"}.', "write", wt, cmds)
                            o = extract_json(parsed.get("text", "")) or {}
                            put("terminal", str(o.get("hash", "")).strip() == expect_hash, "git hash-object output matches the true value (a process really ran)", f"got {str(o.get('hash'))[:20]!r}; {str(parsed.get('error'))[:80]}")
                            put("test_exec", str(o.get("tests_ran", "")).strip() == str(expect_n), f"reported the true test count ({expect_n})", f"got {o.get('tests_ran')!r} expected {expect_n}")
                            put("git", str(o.get("head", "")).strip() == expect_head, "git rev-parse HEAD matches the real commit", f"got {str(o.get('head'))[:12]!r}")
                    pr._drop_worktree(wt, "ngr-00000000-000000-0000", "s1", "")

            # P4 review: a seeded diff with a planted defect must be found, grounded on the exact line, with an explicit verdict
            if "review" in want:
                (proj / "ledger.py").write_text("def balance(entries):\n    total = 0\n    for e in entries:\n        total += e['in'] + e['out']\n    return total\n", encoding="utf-8")
                diff = ("--- a/ledger.py\n+++ b/ledger.py\n@@\n-        total += e['in'] - e['out']\n+        total += e['in'] + e['out']\n")
                prompt = ("You are an INDEPENDENT reviewer. A change to ledger.py is claimed to be a harmless refactor. Router-derived diff:\n" + diff +
                          "\nCurrent content of ledger.py (line-numbered snapshot):\n" + "\n".join(f"{i}: {l}" for i, l in enumerate((proj / "ledger.py").read_text(encoding="utf-8").splitlines(), 1)) +
                          "\n\nCheck whether the change is really harmless. No tools are needed. Return ONLY JSON: {\"verdict\": \"confirmed\"|\"issues_found\", \"summary\": \"...\", \"findings\": "
                          "[{\"title\": \"...\", \"file\": \"ledger.py\", \"line\": <int>, \"quote\": \"<verbatim line>\"}]}. Report only defects you can quote verbatim.")
                res, parsed = run(prompt, "read-only", proj, [])
                o = extract_json(parsed.get("text", "")) or {}
                grounded = False
                for f in o.get("findings") or []:
                    with contextlib.suppress(Exception):
                        line = (proj / "ledger.py").read_text(encoding="utf-8").splitlines()[int(f["line"]) - 1]
                        grounded = grounded or ("e['in'] + e['out']" in re.sub(r"\s+", " ", str(f.get("quote", ""))) and "e['in'] + e['out']" in line)
                put("review", o.get("verdict") == "issues_found" and grounded, "found the planted defect, verdict issues_found, quote grounded at the right line", f"verdict={o.get('verdict')!r} grounded={grounded} err={str(parsed.get('error'))[:80]}")

        # MCP: the CLI must be able to list configured MCP servers (the router itself is an MCP server; this checks the agent side)
        if "mcp" in want:
            put("mcp", *self._mcp(agent, binary))
        return self._finish(agent, results, ver, model, env_rec)

    def _mcp(self, agent: str, binary: str) -> tuple[bool, str, str | None]:
        r = self.ngr.run_proc([binary, "mcp", "list"], str(Path.cwd()), self.r._env("probe", None), None, 60)     # real user config, not the isolated profile
        txt = (r["stdout"] + r["stderr"]).strip()
        ok = r["rc"] == 0 and "unknown" not in txt.lower()[:80]
        return ok, "CLI MCP subsystem responds to `mcp list`; a live MCP tool call is NOT exercised by this probe: " + re.sub(r"\s+", " ", txt)[:80], None if ok else txt[:160]

    def _finish(self, agent, results, ver, model, env_rec):
        for cap, (ok, ev, fail) in results.items():
            self.record(agent, cap, ok, ev, fail, ver, model, env_rec)
        return {cap: {"ok": ok, "evidence": ev, "failure": None if ok else fail} for cap, (ok, ev, fail) in results.items()}
