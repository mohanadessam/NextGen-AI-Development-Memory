#!/usr/bin/env python3
"""Authenticated end-to-end run of the v1.2 router against the REAL installed CLIs (subscription auth, no API keys).

Builds a throw-away project (NOT Nextory) with three planted bugs, then:
  P0 detect agents  P1 calibrate: same read-only task on all three agents  P2 changing availability/failure history: same task re-routed
  P3 write task in an isolated worktree + independent review  P4 approval gate  P5 router over the real MCP stdio protocol
Usage: python tests/e2e_authenticated.py <empty-scratch-dir> [--skip-p2]
It consumes subscription quota (roughly 12-15 agent runs) and takes ~15 minutes.
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=True)
proj = root / "proj"
PY = sys.executable
T = "Review inventory.py for correctness bugs against the behaviour described by test_inventory.py. Do NOT run or modify anything. Report every real bug as a finding with the exact file, line number and a short verbatim quote of the faulty line."
BUG_LINES = {18, 24, 27}          # ground truth: lines of the three planted bugs (each reproduced by a failing project test)
out = []


def say(*a):
    line = " ".join(str(x) for x in a)
    print(line, flush=True)
    out.append(line)


def sh(*args, cwd=None, check=True):
    r = subprocess.run(args, cwd=cwd or proj, capture_output=True, text=True, errors="replace")
    if check and r.returncode:
        raise SystemExit(f"FAILED {args}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def router(sub, args=None):
    r = subprocess.run([PY, "tools/ngmem.py", "router", sub, json.dumps(args or {})], cwd=proj, capture_output=True, text=True, errors="replace")
    return r.stdout.strip() if r.returncode == 0 else "ERROR: " + (r.stderr.strip() or r.stdout.strip())


def jrouter(sub, args=None):
    return json.loads(router(sub, args))


def edit_cfg(fn):
    p = proj / "ai-memory/router/config.json"
    c = json.loads(p.read_text())
    fn(c)
    p.write_text(json.dumps(c, indent=2))


def table(plan):
    for r in plan["ranking"]:
        say(f"    {r['agent']:<12} utility={r['utility']:<7} quality={r['quality']} conf={r['confidence']} completion={r['completion']} "
            f"lat_s={r['latency_s']} fail_pen={r['failure_penalty']} explore={r['exploration_bonus']}")
    unavail = {a: v["reason"] for a, v in plan["availability"].items() if not v["available"]}
    if unavail:
        say("    UNAVAILABLE:", json.dumps(unavail))


# ───────── setup
if (proj / ".git").exists():
    raise SystemExit("scratch dir must be empty")
proj.mkdir(parents=True, exist_ok=True)
(proj / "inventory.py").write_text('''"""Tiny stock ledger used for router end-to-end tests."""


class StockLedger:
    def __init__(self):
        self.entries = []

    def add(self, sku, qty_in=0, qty_out=0):
        # Ledger rule: a movement must move something.
        if qty_in == 0 and qty_out == 0:
            raise ValueError("empty movement")
        self.entries.append({"sku": sku, "in": qty_in, "out": qty_out})

    def balance(self, sku):
        total = 0
        for e in self.entries:
            if e["sku"] == sku:
                total += e["in"] + e["out"]
        return total

    def average_cost(self, costs):
        if not costs:
            return 0
        return sum(costs) / (len(costs) - 1)

    def can_ship(self, sku, qty):
        return self.balance(sku) > qty
''', encoding="utf-8")
(proj / "test_inventory.py").write_text('''import unittest
from inventory import StockLedger


class T(unittest.TestCase):
    def test_balance_subtracts_outgoing(self):
        l = StockLedger(); l.add("A", qty_in=10); l.add("A", qty_out=4)
        self.assertEqual(l.balance("A"), 6)

    def test_average_cost(self):
        self.assertEqual(StockLedger().average_cost([2, 4, 6]), 4)

    def test_can_ship_exact_balance(self):
        l = StockLedger(); l.add("A", qty_in=5)
        self.assertTrue(l.can_ship("A", 5))

    def test_rejects_empty_movement(self):
        with self.assertRaises(ValueError):
            StockLedger().add("A")
''', encoding="utf-8")
sh("git", "init", "-q")
sh("git", "config", "user.email", "e2e@example.invalid")
sh("git", "config", "user.name", "e2e")
sh(PY, str(KIT / "install.py"), "--project", str(proj), cwd=KIT)
(proj / ".gitignore").write_text((proj / ".gitignore").read_text() + "__pycache__/\n")
sh("git", "add", "-A")
sh("git", "commit", "-qm", "fixture")
router("registry")      # first router call creates ai-memory/router/config.json
edit_cfg(lambda c: c["limits"].update(same_task_hourly_cap=12, timeout_s=600))
say("fixture tests before any agent work:", sh(PY, "-m", "unittest", check=False).strip().splitlines()[-1] if False else subprocess.run([PY, "-m", "unittest"], cwd=proj, capture_output=True, text=True).stderr.strip().splitlines()[-1])

# ───────── P0
say("\n== P0 detection")
for a, v in jrouter("status", {"refresh": True})["agents"].items():
    say(f"  {a}: available={v['available']} ({v['reason']})")

# ───────── P1 calibration (manual override only to collect baseline evidence; parallel read-only, same task)
say("\n== P1 calibration: identical read-only task on every agent (manual override = evidence gathering)")
results = {}


def cal(agent):
    results[agent] = router("delegate", {"task": T, "files": ["inventory.py", "test_inventory.py"], "agent": agent, "task_class": "review", "wait": True, "verify_cmd": "python -m unittest"})


th = [threading.Thread(target=cal, args=(a,)) for a in ("claude", "codex", "antigravity")]
[t.start() for t in th]
[t.join() for t in th]
events = [json.loads(l) for l in (proj / "ai-memory/router/events.jsonl").read_text().splitlines()]
for run in [e for e in events if e["type"] == "run"]:
    rr = json.loads((proj / f"ai-memory/router/runs/{run['task_id']}.result.json").read_text())
    findings = rr["stages"][0]["slots"][0]["run"]["result"]["findings"] if rr["stages"][0]["slots"][0].get("run") else []
    good = sum(1 for f in findings if f.get("line") in BUG_LINES)
    score = good / len(findings) if findings else 0.0
    say(f"  {run['agent']}: outcome={run['outcome']} latency={run['latency_s']}s tokens={run.get('tokens')} findings={len(findings)} true_bugs={good}/3 "
        f"evidence={[ (e['kind'], e['score']) for e in run['evidence']]}")
    if run["outcome"] == "completed":
        say("   ->", router("record_evidence", {"task_id": run["task_id"], "agent": run["agent"], "kind": "finding_validated", "source": "reproduction",
                                                 "score": score, "detail": f"{good}/{len(findings)} reported lines are the planted bugs, each reproduced by a failing unit test", "recorded_by": "e2e-driver"}))

# ───────── P2 changing availability / failure history for the SAME task
if "--skip-p2" not in sys.argv:
    say("\n== P2 same task, changing conditions (auto routing, no override)")
    plan_a = jrouter("plan", {"task": T, "files": ["inventory.py", "test_inventory.py"], "task_class": "review"})
    first = plan_a["stages"][0]["slots"][0]["candidates"][0]
    say(f"  [state A] all agents healthy, evidence from P1 -> executor = {first}   candidates={plan_a['stages'][0]['slots'][0]['candidates']}")
    table(plan_a)

    say(f"  [state B] REAL failure of {first}: its CLI is pointed at a non-existent model; router executes the plan")
    edit_cfg(lambda c: c["agents"][first].update(model="no-such-model-e2e"))
    res = router("delegate", {"plan_id": plan_a["plan_id"], "wait": True})
    say("   " + res.replace("\n", "\n   ")[:1400])
    att = json.loads((proj / f"ai-memory/router/runs/{plan_a['plan_id']}.result.json").read_text())["stages"][0]["slots"][0]["attempts"]
    say("  attempts:", json.dumps([(a["agent"], a["outcome"]) for a in att]))
    plan_b = jrouter("plan", {"task": T, "files": ["inventory.py", "test_inventory.py"], "task_class": "review"})
    second = plan_b["stages"][0]["slots"][0]["candidates"][0]
    say(f"  [state B'] same task re-planned after {first}'s failure history -> executor = {second}   candidates={plan_b['stages'][0]['slots'][0]['candidates']}")
    table(plan_b)

    say(f"  [state C] {first} fails again (still misconfigured) -> circuit breaker")
    router("delegate", {"task": T, "files": ["inventory.py", "test_inventory.py"], "task_class": "review", "agent": first, "wait": True})
    plan_c = jrouter("plan", {"task": T, "files": ["inventory.py", "test_inventory.py"], "task_class": "review"})
    third = plan_c["stages"][0]["slots"][0]["candidates"][0]
    say(f"  [state C'] same task -> executor = {third}   candidates={plan_c['stages'][0]['slots'][0]['candidates']}")
    table(plan_c)
    edit_cfg(lambda c: c["agents"][first].update(model=None))

# ───────── P3 write + independent review
say("\n== P3 write task, auto routing, isolated worktree, independent review")
# let a healthy agent be chosen: clear any breaker left over from P2 (simulating the operator fixing the misconfiguration)
st_path = proj / "ai-memory/router/state.json"
st = json.loads(st_path.read_text())
st["breakers"], st["cooldowns"] = {}, {}
st_path.write_text(json.dumps(st))
fix = {"task": "Fix the bugs in inventory.py so that every test in test_inventory.py passes. Edit only inventory.py; do not change the tests.",
       "mode": "write", "files": ["inventory.py", "test_inventory.py"], "scope": ["inventory.py"], "verify_cmd": "python -m unittest"}
plan = jrouter("plan", fix)
say(f"  class={plan['analysis']['task_class']} risk={plan['analysis']['risk']} strategy={plan['strategy']} approval={plan['requires_approval']}")
say("  stages:", json.dumps([(s["role"], s["slots"][0]["candidates"]) for s in plan["stages"]]))
table(plan)
say(router("delegate", {"plan_id": plan["plan_id"], "wait": True}))
wt = next(iter(json.loads((proj / f"ai-memory/router/runs/{plan['plan_id']}.result.json").read_text())["worktrees"].values()))
say("  main tree inventory.py still buggy:", "len(costs) - 1" in (proj / "inventory.py").read_text())
say("  worktree diff:\n" + "\n".join("    " + l for l in subprocess.run(["git", "-C", wt, "diff"], capture_output=True, text=True, errors="replace").stdout.splitlines() if l[:1] in "+-" and l[:3] not in ("+++", "---")))
say("  cleanup:", router("cleanup", {"plan_id": plan["plan_id"]}))

# ───────── P4 approval gate
say("\n== P4 approval gate (high-risk write must not run without the user's explicit approval)")
hp = jrouter("plan", {"task": "Drop the legacy tenant billing table in the production migration", "mode": "write", "scope": ["database/"]})
say(f"  risk={hp['analysis']['risk']} requires_approval={hp['requires_approval']} reasons={hp['approval_reasons']}")
say("  execute without approval ->", router("delegate", {"plan_id": hp["plan_id"], "wait": True}).replace("\n", " ")[:260])

# ───────── P5 real MCP stdio protocol
say("\n== P5 router over the real MCP stdio server")
p = subprocess.Popen([PY, "tools/ngmem.py", "mcp"], cwd=proj, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")


def rpc(i, method, params=None):
    p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}}) + "\n")
    p.stdin.flush()
    return json.loads(p.stdout.readline())


rpc(1, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "e2e", "version": "1"}})
names = [t["name"] for t in rpc(2, "tools/list")["result"]["tools"]]
say("  tools:", names)
call = lambda i, n, a: rpc(i, "tools/call", {"name": n, "arguments": a})["result"]["content"][0]["text"]
started = json.loads(call(3, "router_delegate", {"task": T, "files": ["inventory.py", "test_inventory.py"], "task_class": "review"}))
say("  router_delegate (background) ->", started)
for _ in range(120):
    info = json.loads(call(4, "router_result", {"plan_id": started["plan_id"]}))
    if info["status"] != "running":
        break
    time.sleep(5)
say("  router_result ->", info["status"], "\n   " + info.get("summary", info.get("error", "")).replace("\n", "\n   ")[:900])
p.stdin.close()
p.wait(timeout=60)

say("\n== Final registry")
say(router("registry"))
(root / "e2e_transcript.txt").write_text("\n".join(out), encoding="utf-8")
