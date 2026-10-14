"""v1.3 isolated verifier: the router runs verifier commands against UNTRUSTED agent-written code inside an OS sandbox (Windows AppContainer + job object).
Every test here executes the sandbox for real; hostile test code tries to breach it. Skipped (not faked) when no sandbox is available on the machine."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_router import RouterBase, ngr  # noqa: E402

try:
    sys.path.insert(0, str(Path(ngr.__file__).resolve().parent))
    import ngsandbox
    SANDBOX_OK, SANDBOX_WHY = ngsandbox.available()
except Exception as exc:  # pragma: no cover
    ngsandbox, SANDBOX_OK, SANDBOX_WHY = None, False, str(exc)


@unittest.skipUnless(SANDBOX_OK, f"no OS sandbox on this machine: {SANDBOX_WHY}")
class SandboxedVerifier(RouterBase):
    def setUp(self):
        super().setUp()
        cfgp = self.project / "ai-memory/router/config.json"
        c = json.loads(cfgp.read_text())
        c["verifier_backend"] = "auto"
        c["verifiers"]["python-unittest"] = ["python", "-m", "unittest", "discover", "-s", "tests"]
        cfgp.write_text(json.dumps(c))
        self.r = ngr.Router(self.project)
        (self.project / "src").mkdir(exist_ok=True)
        (self.project / "tests").mkdir(exist_ok=True)
        (self.project / "src" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
        (self.project / "tests" / "__init__.py").write_text("")
        (self.project / ".gitignore").write_text("__pycache__/\nai-memory/\n")
        self.canary_dir = Path(tempfile.mkdtemp(prefix="ngv-canary-", dir=str(Path.home() / "AppData" / "Local" / "Temp")))
        self.addCleanup(shutil.rmtree, self.canary_dir, ignore_errors=True)
        self.canary = self.canary_dir / "secret.txt"
        self.canary.write_text("TOP-SECRET-CANARY-VALUE")

    def write_tests(self, body):
        (self.project / "tests" / "test_x.py").write_text(body)
        self.git_repo()

    def verify(self, **limits):
        for k, v in limits.items():
            self.r.lim[k] = v
        return self.r._test_evidence(["python", "-m", "unittest", "discover", "-s", "tests"], self.project)

    def test_passing_and_failing_suites_are_judged_correctly_inside_the_sandbox(self):
        self.write_tests("import sys, unittest\nsys.path.insert(0, 'src')\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
        ev = self.verify()
        self.assertEqual((ev["score"], ev["backend"]), (1.0, "appcontainer"), ev)
        self.assertIn("no network", ev["detail"])
        self.write_tests("import sys, unittest\nsys.path.insert(0, 'src')\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 6)\n")
        ev = self.verify()
        self.assertEqual(ev["score"], 0.0)

    def test_hostile_test_code_cannot_read_write_leak_or_connect(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
        srv.settimeout(0.2)
        port = srv.getsockname()[1]
        hits = []
        stop = threading.Event()

        def accept():
            while not stop.is_set():
                try:
                    c, _ = srv.accept()
                    hits.append(1)
                    c.close()
                except OSError:
                    pass
        threading.Thread(target=accept, daemon=True).start()
        os.environ["NGV_HOST_SECRET"] = "env-secret-should-not-be-visible"
        self.addCleanup(lambda: os.environ.pop("NGV_HOST_SECRET", None))
        planted = self.canary_dir / "planted_by_hostile_code.txt"
        # The suite PASSES only if every attack is blocked, so score 1.0 == all denials held.
        self.write_tests(f'''import os, socket, unittest


class Hostile(unittest.TestCase):
    def test_cannot_read_host_canary(self):
        with self.assertRaises(OSError):
            open({str(self.canary)!r}).read()

    def test_cannot_write_to_host(self):
        with self.assertRaises(OSError):
            open({str(planted)!r}, "w").write("PWNED")

    def test_cannot_list_host_dirs(self):
        with self.assertRaises(OSError):
            os.listdir({str(self.canary_dir)!r})

    def test_host_environment_is_not_visible(self):
        self.assertNotIn("NGV_HOST_SECRET", os.environ)
        self.assertNotIn("ANTHROPIC_API_KEY", os.environ)

    def test_no_network_even_to_loopback(self):
        s = socket.socket()
        s.settimeout(2)
        with self.assertRaises(OSError):
            s.connect(("127.0.0.1", {port}))
        s2 = socket.socket()
        s2.settimeout(2)
        with self.assertRaises(OSError):
            s2.connect(("1.1.1.1", 443))
''')
        ev = self.verify()
        stop.set()
        self.assertEqual(ev["score"], 1.0, "a hostile attempt SUCCEEDED inside the verifier: " + ev["detail"])
        self.assertEqual(hits, [], "the host listener saw a connection from the sandbox")
        self.assertFalse(planted.exists(), "the sandbox created a file on the host")
        self.assertEqual(self.canary.read_text(), "TOP-SECRET-CANARY-VALUE")
        self.assertNotIn("TOP-SECRET-CANARY-VALUE", ev["detail"])

    def test_verifier_runs_on_a_throwaway_copy_and_never_touches_the_worktree(self):
        self.write_tests("import unittest\n\n\nclass T(unittest.TestCase):\n    def test_writes(self):\n        open('created_by_tests.txt', 'w').write('x')\n        open('src/calc.py', 'w').write('BROKEN')\n")
        before = subprocess.run(["git", "-C", str(self.project), "status", "--porcelain"], capture_output=True, text=True).stdout
        ev = self.verify()
        self.assertEqual(ev["score"], 1.0)
        self.assertFalse((self.project / "created_by_tests.txt").exists())
        self.assertEqual((self.project / "src" / "calc.py").read_text(), "def add(a, b):\n    return a + b\n")
        self.assertEqual(subprocess.run(["git", "-C", str(self.project), "status", "--porcelain"], capture_output=True, text=True).stdout, before)

    def test_runaway_verifier_is_killed_at_the_timeout(self):
        self.write_tests("import unittest\n\n\nclass T(unittest.TestCase):\n    def test_spin(self):\n        while True:\n            pass\n")
        t0 = time.time()
        ev = self.verify(verify_timeout_s=4)
        self.assertEqual(ev["score"], 0.0)
        self.assertIn("TIMED OUT", ev["detail"])
        self.assertLess(time.time() - t0, 90)

    def test_backend_policy_when_no_sandbox_exists(self):
        self.write_tests("import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n")
        self.r._sandbox_mod, self.r._sandbox_reason = None, "simulated: no isolation backend"
        ev = self.verify()                                                     # auto -> NOT RUN, never silently on the host
        self.assertEqual(ev["kind"], "tests_skipped")
        self.assertIn("NOT RUN", ev["detail"])
        self.assertNotIn("backend", ev)
        self.r.cfg["verifier_backend"] = "appcontainer"                        # required but missing -> failing evidence
        ev = self.verify()
        self.assertEqual((ev["kind"], ev["score"]), ("tests", 0.0))
        self.r.cfg["verifier_backend"] = "host"                                # explicit opt-in, loudly flagged
        ev = self.verify()
        self.assertEqual(ev["backend"], "host-UNSAFE")
        self.assertIn("not isolated", ev["detail"])
        self.r.cfg["verifier_backend"] = "none"
        self.assertEqual(self.verify()["kind"], "tests_skipped")

    def test_skipped_verification_is_not_quality_evidence_and_is_reported(self):
        run = {"outcome": "completed"}
        self.assertIsNone(self.r.run_quality(run, [{"kind": "tests_skipped", "source": "router_process", "score": 1.0}]))

    def test_verifier_commands_remain_named_never_free_form(self):
        for bad in ("python -c \"import os\"", "curl http://x", "cmd /c calc"):
            with self.assertRaises(ngr.RouterError):
                self.r.plan("Explain", verify_cmd=bad, persist=False)

    def test_status_reports_the_isolation_backend(self):
        v = self.r.status()["verification"]
        self.assertEqual(v["verifier_backend"], "auto")
        self.assertIn("appcontainer", v["isolation"])
        self.assertIn("not available", v["container_runtime"])

    def test_end_to_end_write_plan_is_gated_by_the_sandboxed_verifier(self):
        self.write_tests("import sys, unittest\nsys.path.insert(0, 'src')\nfrom calc import add\n\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
        p = self.r.plan("Change the implementation in src", mode="write", scope=["src/"], agent="codex", verify_cmd="python-unittest")
        good = "import pathlib;pathlib.Path('src/calc.py').write_text('def add(a, b):\\n    return b + a\\n')"
        bad = "import pathlib;pathlib.Path('src/calc.py').write_text('def add(a, b):\\n    return 0\\n')"
        import json as _j
        mk = lambda code: (lambda binary, prompt, mode, cwd, model, limits, run_dir, tag: ([binary, "-c", code + ";import json;print(json.dumps({'text': json.dumps({'summary':'x','findings':[]})}))"], None))
        self.fakes["codex"].build = mk(bad)
        self.fakes["claude"].build = mk(good)
        res = self.r.execute(p["plan_id"])
        att = res["stages"][0]["slots"][0]["attempts"]
        self.assertEqual(att[0]["agent"], "codex")
        self.assertEqual(att[0]["outcome"], "failed", "bad change rejected by the sandboxed tests")
        self.assertIn("appcontainer sandbox", att[0]["detail"])
        self.assertEqual(res["stages"][0]["slots"][0]["status"], "completed", "the next agent's good change passed the same sandboxed verification")
        self.r.cleanup(p["plan_id"])


@unittest.skipUnless(SANDBOX_OK and shutil.which("php"), "needs the OS sandbox and a php binary")
class SandboxedPhpVerifier(RouterBase):
    def setUp(self):
        super().setUp()
        cfgp = self.project / "ai-memory/router/config.json"
        c = json.loads(cfgp.read_text())
        c["verifier_backend"] = "auto"
        c["verifiers"]["php-smoke"] = ["php", "tests/run.php"]
        cfgp.write_text(json.dumps(c))
        self.r = ngr.Router(self.project)
        (self.project / "tests").mkdir(exist_ok=True)
        self.canary_dir = Path(tempfile.mkdtemp(prefix="ngv-canary-", dir=str(Path.home() / "AppData" / "Local" / "Temp")))
        self.addCleanup(shutil.rmtree, self.canary_dir, ignore_errors=True)
        (self.canary_dir / "secret.txt").write_text("PHP-CANARY")

    def test_php_with_disposable_sqlite_database_and_hostile_attempts(self):
        canary = str(self.canary_dir / "secret.txt").replace("\\", "/")
        (self.project / "tests" / "run.php").write_text(f"""<?php
$db = new PDO('sqlite:test.db');                       // disposable database file inside the sandbox workspace
$db->exec('CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)');
$db->exec("INSERT INTO t (v) VALUES ('ok')");
if ($db->query('SELECT COUNT(*) FROM t')->fetchColumn() != 1) {{ fwrite(STDERR, "db failed\\n"); exit(1); }}
if (@file_get_contents('{canary}') !== false) {{ fwrite(STDERR, "BREACH: read host canary\\n"); exit(2); }}
$s = @stream_socket_client('tcp://127.0.0.1:3306', $en, $es, 2);
if ($s) {{ fwrite(STDERR, "BREACH: network\\n"); exit(3); }}
if (getenv('ANTHROPIC_API_KEY') !== false || getenv('DB_PASSWORD') !== false) {{ fwrite(STDERR, "BREACH: env\\n"); exit(4); }}
echo "OK 1 passed\\n";
""")
        self.git_repo()
        os.environ["DB_PASSWORD"] = "prod-password-should-not-leak"
        self.addCleanup(lambda: os.environ.pop("DB_PASSWORD", None))
        ev = self.r._test_evidence(["php", "tests/run.php"], self.project)
        self.assertEqual(ev["score"], 1.0, ev["detail"])
        self.assertEqual(ev["backend"], "appcontainer")
        self.assertFalse((self.project / "test.db").exists(), "the disposable database never reaches the host worktree")


if __name__ == "__main__":
    unittest.main(verbosity=2)
