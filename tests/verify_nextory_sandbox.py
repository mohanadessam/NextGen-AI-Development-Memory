#!/usr/bin/env python3
"""Run Nextory's PHPUnit suite INSIDE the OS sandbox, from a throwaway COPY (read-only use of Nextory; no router is installed into it).
No .env (gitignored, never copied), no host env, no network (so SQLite :memory: only, as in Nextory's phpunit.xml), disposable APP_KEY.
Usage: python tests/verify_nextory_sandbox.py <nextory-dir> <control-dir> [phpunit args...]   e.g. --testsuite Unit"""
import base64
import hashlib
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "template" / "tools"))
import ngrouter as ngr  # noqa: E402

nex, ctl = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
extra = sys.argv[3:] or ["--testsuite", "Unit"]
(ctl / "ai-memory").mkdir(parents=True, exist_ok=True)


def fingerprint():
    h = hashlib.sha256()
    files = subprocess.run(["git", "-C", str(nex), "ls-files", "-co", "--exclude-standard", "-z"], capture_output=True, text=True, errors="replace").stdout.split("\0")
    for rel in sorted(f for f in files if f):
        p = nex / rel
        if p.is_file() and "ai-memory/" not in rel:
            h.update(rel.encode() + p.read_bytes())
    h.update(subprocess.run(["git", "-C", str(nex), "status", "--short"], capture_output=True, text=True).stdout.encode())
    h.update(subprocess.run(["git", "-C", str(nex), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.encode())
    return h.hexdigest()


before = fingerprint()
R = ngr.Router(ctl)
R.cfg["verifier_backend"] = "appcontainer"
R.cfg["sandbox_extra_dirs"] = ["vendor"]
R.lim["verify_timeout_s"] = 3300      # NOTE: limits live in R.lim (cfg["limits"]); the earlier cfg[...] assignment was silently ignored
R.lim["verify_memory_mb"] = 3072
R.cfg["sandbox_env"] = {"APP_KEY": "base64:" + base64.b64encode(secrets.token_bytes(32)).decode(), "APP_ENV": "testing", "DB_CONNECTION": "sqlite", "DB_DATABASE": ":memory:",
                        "CACHE_STORE": "array", "SESSION_DRIVER": "array", "QUEUE_CONNECTION": "sync", "MAIL_MAILER": "array", "TELESCOPE_ENABLED": "false", "PULSE_ENABLED": "false"}
argv = ["php", "vendor/phpunit/phpunit/phpunit", "--do-not-cache-result", *extra]
print("running in the sandbox:", " ".join(argv), flush=True)
t0 = time.time()
sb = R._sandbox()
assert sb is not None, "no sandbox"
ev = R._sandbox_verify(sb, argv, nex)
print(f"\nscore={ev['score']} rc={ev['rc']} in {time.time() - t0:.0f}s\n{ev['detail']}")
after = fingerprint()
print("\nNextory working tree byte-identical before/after:", before == after)
listed = subprocess.run(["git", "-C", str(nex), "ls-files", "-co", "--exclude-standard"], capture_output=True, text=True).stdout.split()
print("the sandbox copy is built from the git-visible file list; .env* real files in it:", [f for f in listed if Path(f).name == ".env"])
sys.exit(0 if before == after else 1)
