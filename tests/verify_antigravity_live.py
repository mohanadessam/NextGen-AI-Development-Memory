#!/usr/bin/env python3
"""Live, authenticated verification that Antigravity (agy CLI) can really use tools headlessly through the router's isolated profile.

Nothing the model says is trusted: every claim is re-checked on disk / in Git. Disposable repo, outside Nextory.
Usage: python tests/verify_antigravity_live.py <empty-scratch-dir> [--profile-dir DIR] [--use-ide-profile]
  --use-ide-profile  run with the real user profile (reproduces the defect; for comparison only)
"""
import hashlib
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "template" / "tools"))
import ngrouter as ngr  # noqa: E402

root = Path(sys.argv[1]).resolve()
use_ide = "--use-ide-profile" in sys.argv
prof_arg = sys.argv[sys.argv.index("--profile-dir") + 1] if "--profile-dir" in sys.argv else None
if root.exists() and any(root.iterdir()):
    raise SystemExit("scratch dir must be empty")
proj = root / "repo"
proj.mkdir(parents=True)
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""), flush=True)


def git(*a):
    return subprocess.run(["git", "-C", str(proj), *a], capture_output=True, text=True, errors="replace").stdout.strip()


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ── disposable fixture
TOKEN = "AGY-" + secrets.token_hex(12)
NONCE = secrets.token_hex(8)
(proj / "token_to_read.txt").write_text(TOKEN + "\n", encoding="utf-8")
(proj / "calc.py").write_text("def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n", encoding="utf-8")
(proj / "test_calc.py").write_text("import unittest\nfrom calc import add, mul\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n\n    def test_mul(self):\n        self.assertEqual(mul(2, 3), 6)\n\n\nif __name__ == '__main__':\n    unittest.main()\n", encoding="utf-8")
(proj / "probe_hash.py").write_text("import hashlib, sys\nopen('proof.txt', 'w').write(hashlib.sha256(sys.argv[1].encode()).hexdigest())\n", encoding="utf-8")
(proj / "PROTECTED.txt").write_text("do not touch " + secrets.token_hex(6) + "\n", encoding="utf-8")
(proj / ".gitignore").write_text("__pycache__/\n", encoding="utf-8")
for a in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "baseline"]):
    git(*a)
base = git("rev-parse", "HEAD")
protected_before = sha(proj / "PROTECTED.txt")
print(f"fixture: {proj}\n  token (never given to the model) = {TOKEN[:8]}...  unittest before:", subprocess.run([sys.executable, "-m", "unittest"], cwd=proj, capture_output=True, text=True).stderr.strip().splitlines()[-1])

# ── router objects (the exact environment + command construction the router uses)
ctl = root / "ctl"           # router state lives OUTSIDE the repo under test
(ctl / "ai-memory").mkdir(parents=True)
R = ngr.Router(ctl)
R.cfg["agent_command_allow"] = [["python", "probe_hash.py", NONCE], ["python", "-m", "unittest", "-v"]]     # EXACT commands only (the CLI matches whole commands)
if prof_arg:
    R.cfg["agents"]["antigravity"]["profile_dir"] = prof_arg
if use_ide:
    R.cfg["agents"]["antigravity"]["isolated_profile"] = False
ad = ngr.ADAPTERS["antigravity"]
binary = ad.find_binary(None)
print("agy:", binary, subprocess.run([binary, "--version"], capture_output=True, text=True).stdout.strip(), "| profile:", R.agent_profile_dir("antigravity") or "REAL USER PROFILE (shared IDE config)")


def run(prompt, mode, timeout=400):
    ad.prepare(R.agent_profile_dir("antigravity"), [list(a) for a in R.cfg["verifiers"].values()] + [list(a) for a in R.cfg["agent_command_allow"]])
    argv, stdin = ad.build(binary, prompt, mode, str(proj), None, R.lim, root, "live")
    res = ngr.run_proc(argv, str(proj), R._env("live", "antigravity"), stdin, timeout)
    parsed = ad.parse(res)
    return res, parsed


JSON_TAIL = ' Reply with ONLY one JSON object: {"answer": "<text>"}.'

# 1 read
print("\n== 1. real file READ of an unpredictable token (read-only/plan mode)")
res, parsed = run("Use ONLY your file-reading tool (not a terminal) to read the file token_to_read.txt in the current directory." + JSON_TAIL, "read-only")
obj = ngr.extract_json(parsed["text"]) or {}
check("process exit 0 and JSON envelope parsed", res["rc"] == 0 and not res["timed_out"] and parsed["error"] is None, (parsed["error"] or "")[:120])
check("returned content equals the file's random token exactly", str(obj.get("answer", "")).strip() == TOKEN, f"got {str(obj.get('answer'))[:40]!r}")
check("read-only run changed nothing (git status clean)", git("status", "--porcelain") == "")

# 2 edit (permitted file only)
print("\n== 2. real file EDIT (accept-edits mode), permitted file calc.py only")
res, parsed = run("Fix the bug in calc.py so that add(a, b) returns a + b, using your file editing tools (not a terminal). Edit ONLY calc.py. Do not touch any other file." + JSON_TAIL, "write")
check("process exit 0", res["rc"] == 0 and not res["timed_out"], (parsed["error"] or "")[:120])
src = (proj / "calc.py").read_text(encoding="utf-8")
check("calc.py really changed on disk (independent read)", "return a + b" in src and "return a - b" not in src)
changed = [l[3:] for l in subprocess.run(["git", "-C", str(proj), "status", "--porcelain"], capture_output=True, text=True).stdout.splitlines()]
check("Git shows ONLY calc.py modified", changed == ["calc.py"], str(changed))
check("PROTECTED.txt byte-identical", sha(proj / "PROTECTED.txt") == protected_before)
check("no commits were made by the agent", git("rev-parse", "HEAD") == base)
t = subprocess.run([sys.executable, "-m", "unittest"], cwd=proj, capture_output=True, text=True)
check("router-run test suite now passes (independent execution)", t.returncode == 0, t.stderr.strip().splitlines()[-1] if t.stderr.strip() else "")

# 3 terminal: a command whose result the model cannot know without executing it
print("\n== 3. real TERMINAL execution (sha256 of a nonce the model cannot compute in its head)")
expected = hashlib.sha256(NONCE.encode()).hexdigest()
cmd = f"python probe_hash.py {NONCE}"
res, parsed = run(f"Run exactly this command in a terminal, using your command-execution tool (do NOT write the file yourself): {cmd}  Then reply." + JSON_TAIL, "write")
proof = (proj / "proof.txt")
got = proof.read_text(encoding="utf-8").strip() if proof.exists() else None
check("command output file proof.txt holds the true sha256 (so a process really ran)", got == expected, f"{'missing' if got is None else got[:16]}")
print("   raw summary:", (parsed["text"] or parsed["error"] or "")[:200].replace("\n", " "))

# 4 allowed test command run by the agent
print("\n== 4. agent runs the allowed test command and reports the real result")
(proj / "test_calc.py").write_text((proj / "test_calc.py").read_text(encoding="utf-8").replace("self.assertEqual(mul(2, 3), 6)", "self.assertEqual(mul(2, 3), 6)\n\n    def test_marker_%s(self):\n        self.assertTrue(True)" % NONCE[:6]), encoding="utf-8")
res, parsed = run('Run the command `python -m unittest -v` in a terminal and tell me exactly how many tests ran. Reply with ONLY one JSON object: {"answer": "<number of tests>"}', "write")
obj = ngr.extract_json(parsed["text"]) or {}
real = subprocess.run([sys.executable, "-m", "unittest", "-v"], cwd=proj, capture_output=True, text=True).stderr
nreal = int(__import__("re").search(r"Ran (\d+) tests?", real).group(1))
check(f"reported test count equals the true count ({nreal})", str(obj.get("answer", "")).strip() == str(nreal), f"got {obj.get('answer')!r}")

# 5 protected files + no escape
print("\n== 5. final independent inspection")
check("PROTECTED.txt still byte-identical", sha(proj / "PROTECTED.txt") == protected_before)
check("HEAD unchanged (no commits/resets)", git("rev-parse", "HEAD") == base)
extra = [l for l in git("status", "--porcelain", "--ignored").splitlines() if "__pycache__" not in l]
print("   git status:", extra)

# 6 error handling / timeout
print("\n== 6. error handling: timeout and bad model")
res = ngr.run_proc(*[ad.build(binary, "Count slowly from 1 to 100000, one number per line, no tools.", "read-only", str(proj), None, {**R.lim, "timeout_s": 3}, root, "to")[0]], str(proj), R._env("live", "antigravity"), None, 3)
check("timeout is detected and the process tree is killed", res["timed_out"])
res = ngr.run_proc(ad.build(binary, "hi", "read-only", str(proj), "no-such-model", R.lim, root, "bm")[0], str(proj), R._env("live", "antigravity"), None, 60)
check("bad model gives a non-zero exit / error text (not a silent success)", res["rc"] not in (0, None) or "invalid" in (res["stderr"] + res["stdout"]).lower())

fails = [r for r in results if not r[1]]
print(f"\nRESULT: {len(results) - len(fails)}/{len(results)} checks passed" + ("" if not fails else "  FAILED: " + "; ".join(r[0] for r in fails)))
(root / "agy_live_result.json").write_text(json.dumps({"profile": str(R.agent_profile_dir("antigravity")), "checks": results}, indent=1), encoding="utf-8")
sys.exit(1 if fails else 0)
