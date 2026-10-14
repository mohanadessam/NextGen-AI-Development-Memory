#!/usr/bin/env python3
"""Sandboxed Laravel verification (disposable fixture, SQLite only). Usage: python tests/verify_laravel_sandbox.py <laravel-skeleton-dir>
The skeleton comes from `composer create-project laravel/laravel <dir>` (outside Nextory). The router copies it into a throwaway AppContainer workspace:
no network (so no MariaDB over TCP), no host env, no .env, vendor/ copied read-only-by-copy. Hostile Laravel tests try to break out; they must all be denied."""
import base64
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KIT / "template" / "tools"))
import ngrouter as ngr  # noqa: E402

app = Path(sys.argv[1]).resolve()
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""), flush=True)


def git(*a):
    return subprocess.run(["git", "-C", str(app), *a], capture_output=True, text=True).stdout.strip()


assert (app / "vendor" / "phpunit").is_dir(), "run composer create-project first"
(app / "ai-memory").mkdir(exist_ok=True)
gi = app / ".gitignore"
if "ai-memory/" not in gi.read_text():
    gi.write_text(gi.read_text() + "\nai-memory/\n")
if not (app / ".git").exists():
    for a in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "t"], ["add", "-A"], ["commit", "-qm", "laravel skeleton"]):
        git(*a)
R = ngr.Router(app)
R.cfg["verifier_backend"] = "appcontainer"
R.cfg["sandbox_extra_dirs"] = ["vendor"]
R.cfg["sandbox_env"] = {"APP_KEY": "base64:" + base64.b64encode(secrets.token_bytes(32)).decode(), "APP_ENV": "testing", "DB_CONNECTION": "sqlite", "DB_DATABASE": ":memory:",
                        "CACHE_STORE": "array", "SESSION_DRIVER": "array", "QUEUE_CONNECTION": "sync", "MAIL_MAILER": "array"}
PHPUNIT = ["php", "vendor/phpunit/phpunit/phpunit", "--do-not-cache-result"]      # the admin-defined verifier command
canary_dir = Path(tempfile.mkdtemp(prefix="ngv-canary-", dir=str(Path.home() / "AppData" / "Local" / "Temp")))
(canary_dir / "prod.env").write_text("DB_PASSWORD=PRODUCTION-SECRET\n")
os.environ["DB_PASSWORD"] = "host-production-password"
os.environ["DB_HOST"] = "prod-db.internal"
print(f"fixture {app}; sandbox: {R._sandbox() and 'appcontainer'}; php runtime: {R._runtime_dir('php')}")

print("\n== 1. stock Laravel feature + unit tests, SQLite :memory:, inside the sandbox")
ev = R._test_evidence(PHPUNIT, app)
check("stock Laravel tests pass inside the sandbox", ev["score"] == 1.0 and ev.get("backend") == "appcontainer", ev["detail"][:170])
tests_run = (ev["detail"].split("OK (")[-1][:40] if "OK (" in ev["detail"] else ev["detail"][-60:])

print("\n== 2. hostile Laravel tests (all must be DENIED for the suite to pass)")
hostile = f"""<?php
namespace Tests\\Feature;
use Tests\\TestCase;

class HostileTest extends TestCase
{{
    public function test_no_env_file_in_the_sandbox_copy(): void
    {{
        $this->assertFileDoesNotExist(base_path('.env'));
    }}

    public function test_host_database_credentials_are_not_visible(): void
    {{
        $this->assertFalse(getenv('DB_PASSWORD'));
        $this->assertFalse(getenv('DB_HOST'));
        $this->assertSame('sqlite', config('database.default'));
        $this->assertSame(':memory:', config('database.connections.sqlite.database'));
    }}

    public function test_cannot_read_host_canary(): void
    {{
        $this->assertFalse(@file_get_contents({str(canary_dir / 'prod.env').replace(chr(92), '/')!r}));
    }}

    public function test_cannot_reach_a_mysql_server(): void
    {{
        $s = @stream_socket_client('tcp://127.0.0.1:3306', $en, $es, 2);
        $this->assertFalse($s);
        $this->expectException(\\PDOException::class);
        new \\PDO('mysql:host=127.0.0.1;port=3306;dbname=nextory', 'root', '');
    }}

    public function test_cannot_write_outside_the_workspace(): void
    {{
        $this->assertFalse(@file_put_contents({str(canary_dir / 'planted.txt').replace(chr(92), '/')!r}, 'PWNED'));
    }}
}}
"""
(app / "tests" / "Feature" / "HostileTest.php").write_text(hostile, encoding="utf-8")
ev = R._test_evidence(PHPUNIT, app)
check("hostile tests all denied (suite green means every attack failed)", ev["score"] == 1.0, ev["detail"][:200])
check("host canary untouched and nothing planted", (canary_dir / "prod.env").read_text() == "DB_PASSWORD=PRODUCTION-SECRET\n" and not (canary_dir / "planted.txt").exists())
check("no PRODUCTION-SECRET in the evidence", "PRODUCTION-SECRET" not in json.dumps(ev) and "host-production-password" not in json.dumps(ev))
check("fixture worktree untouched by the run (no database.sqlite, no storage writes)", git("status", "--porcelain").replace("?? tests/Feature/HostileTest.php", "").strip() == "")

print("\n== 3. a planted failing test is reported as a failure")
(app / "tests" / "Feature" / "HostileTest.php").unlink()
(app / "tests" / "Feature" / "PlantedFailTest.php").write_text("<?php\nnamespace Tests\\Feature;\nuse Tests\\TestCase;\nclass PlantedFailTest extends TestCase { public function test_bug(): void { $this->assertSame(5, 2 + 2); } }\n")
ev = R._test_evidence(PHPUNIT, app)
check("failing Laravel test -> score 0", ev["score"] == 0.0, ev["detail"][:140])
(app / "tests" / "Feature" / "PlantedFailTest.php").unlink()

print("\n== 4. MariaDB")
print("  [BLOCKED] MariaDB/MySQL tests cannot run in this sandbox: an AppContainer without the privateNetworkClientServer capability has no TCP at all (not even loopback), "
      "and granting it needs a system loopback exemption (CheckNetIsolation, an OS-wide setting). Docker/WSL/Windows Sandbox are not installed. SQLite-based suites are supported.")
shutil.rmtree(canary_dir, ignore_errors=True)
fails = [r for r in results if not r[1]]
print(f"\nRESULT: {len(results) - len(fails)}/{len(results)} checks passed" + ("" if not fails else "  FAILED: " + "; ".join(r[0] for r in fails)))
sys.exit(1 if fails else 0)
