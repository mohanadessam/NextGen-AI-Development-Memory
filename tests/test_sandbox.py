"""Security tests for template/tools/ngsandbox.py. Every test runs the sandbox for real.

Run:  python tests/test_sandbox.py     (needs Windows; elevated host is fine)
Negative tests are the point: each one first proves a positive control (host can do the thing),
then asserts the sandboxed process is denied.
"""
from __future__ import annotations

import ctypes
import glob
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
import winreg
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "template" / "tools"))
import ngsandbox as sb  # noqa: E402

TMP = Path(os.environ["TEMP"])
RUNTIME_ROOT = TMP / "ngsb-runtime"
PHP_SRC = Path(r"C:\xampp\php")
BASE_ENV = {"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
_runtimes: dict[str, Path] = {}
_baseline_maps = 0

# Helper injected at the top of probe scripts: prints "label|Status|detail" lines.
PRE = '''
import os, sys
def attempt(label, fn):
    try:
        r = fn(); print(f"{label}|OK|{r!r:.60}", flush=True)
    except BaseException as e:
        print(f"{label}|{type(e).__name__}|errno={getattr(e,'errno',None)} winerror={getattr(e,'winerror',None)}", flush=True)
'''


def parse(out: str) -> dict:
    d = {}
    for line in out.splitlines():
        p = line.strip().split("|", 2)
        if len(p) == 3:
            d[p[0]] = (p[1], p[2])
    return d


def count_mappings() -> int:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sb._MAPPINGS) as k:
            return winreg.QueryInfoKey(k)[0]
    except OSError:
        return 0


def stage_cached(name: str, src: Path) -> Path:
    """Stage a runtime once under %TEMP%\\ngsb-runtime and reuse it across tests/runs."""
    if name in _runtimes:
        return _runtimes[name]
    dst, marker = RUNTIME_ROOT / name, RUNTIME_ROOT / (name + ".ok")
    if not marker.exists():
        RUNTIME_ROOT.mkdir(exist_ok=True)
        sb.stage_runtime(src, dst)
        marker.write_text("ok")
    _runtimes[name] = dst
    return dst


def setUpModule():
    global _baseline_maps
    ok, why = sb.available()
    if not ok:
        raise unittest.SkipTest("AppContainer unavailable: " + why)
    stage_cached("py312", Path(sys.base_prefix))
    _baseline_maps = count_mappings()


def tearDownModule():
    # No per-run profile / staging dir may survive the whole suite (only the cached runtime).
    left = [p for p in glob.glob(str(TMP / "ngsb-*")) if Path(p).name not in ("ngsb-runtime",)]
    assert not left, f"leftover staging dirs: {left}"
    assert count_mappings() == _baseline_maps, "AppContainer profile mappings leaked"


class Base(unittest.TestCase):
    def setUp(self):
        self.stage = Path(tempfile.mkdtemp(prefix="ngsb-run-", dir=TMP))
        self.ws = self.stage / "ws"
        self.ws.mkdir()
        self.links, self.canaries = [], []
        self.py = stage_cached("py312", Path(sys.base_prefix))

    def tearDown(self):
        for l in self.links:  # remove junction/symlink itself, never its target
            try:
                os.rmdir(l)
            except OSError:
                try:
                    os.unlink(l)
                except OSError:
                    pass
        for c in self.canaries:
            shutil.rmtree(c, ignore_errors=True)
        shutil.rmtree(self.stage, ignore_errors=True)
        self.assertFalse(self.stage.exists(), "staging dir not removed")

    def canary(self, text="CANARY-SECRET-123") -> Path:
        # Plain mkdir => default inherited ACL (user/admins/system), no AppContainer ACE.
        d = TMP / f"ngsb-canary-{uuid.uuid4().hex[:8]}"
        os.mkdir(d)
        (d / "secret.txt").write_text(text)
        self.canaries.append(d)
        self.assertEqual((d / "secret.txt").read_text(), text)  # positive control: host can read
        return d

    def run_sb(self, script=None, *, argv=None, ro=(), env=None, runtime=None, **kw):
        if argv is None:
            (self.ws / "job.py").write_text(script, encoding="utf-8")
            argv = [str(self.py / "python.exe"), "job.py"]
        e = {**BASE_ENV, **(env or {})}
        r = sb.run_sandboxed(argv, workspace=self.ws, readonly_dirs=[runtime or self.py, *ro], env=e, **kw)
        self.assertFalse(sb.profile_exists(r["sandbox"]["profile"]), "AppContainer profile leaked")
        return r

    def ok(self, r):
        self.assertEqual(r["rc"], 0, f"stdout={r['stdout']!r}\nstderr={r['stderr']!r}")


class T01Positive(Base):
    def test_python_runs_reads_ro_writes_ws_and_unittest(self):
        ro = self.stage / "ro"; ro.mkdir(); (ro / "data.txt").write_text("hello-ro")
        r = self.run_sb(PRE + '''
print("ro:", open(os.environ["RO"] + "/data.txt").read())
open("out.txt", "w", encoding="utf-8").write("written-in-sandbox")
print("cwd:", os.getcwd())
''', ro=[ro], env={"RO": str(ro)})
        self.ok(r)
        self.assertIn("ro: hello-ro", r["stdout"])
        self.assertIn(str(self.ws), r["stdout"])
        self.assertEqual((self.ws / "out.txt").read_text(), "written-in-sandbox")  # host reads result
        self.assertEqual(r["sandbox"]["kind"], "appcontainer+job")
        self.assertTrue(r["sandbox"]["sid"].startswith("S-1-15-2-"))
        self.assertEqual(r["sandbox"]["network"], "none")
        # tiny unittest package inside the workspace
        pkg = self.ws / "pkg"; pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "test_a.py").write_text("import unittest\nclass T(unittest.TestCase):\n"
                                       "    def test_ok(self): self.assertEqual(2+2, 4)\n")
        r = self.run_sb(argv=[str(self.py / "python.exe"), "-m", "unittest", "-v", "pkg.test_a"])
        self.ok(r)
        self.assertIn("OK", r["stderr"])


class T02CannotRead(Base):
    def test_canary_unreadable(self):
        c = self.canary()
        r = self.run_sb(PRE + '''
c, d = os.environ["CANARY_FILE"], os.environ["CANARY_DIR"]
attempt("read", lambda: open(c).read())
attempt("list", lambda: os.listdir(d))
attempt("profile_list", lambda: os.listdir(os.environ["USERPROFILE_DIR"]))
attempt("stat", lambda: os.stat(c).st_size)
''', env={"CANARY_FILE": str(c / "secret.txt"), "CANARY_DIR": str(c), "USERPROFILE_DIR": os.environ["USERPROFILE"]})
        self.ok(r)
        res = parse(r["stdout"])
        self.assertNotIn("CANARY-SECRET", r["stdout"])
        for k in ("read", "list", "profile_list"):
            self.assertEqual(res[k][0], "PermissionError", (k, res[k]))
        self.assertNotEqual(res["stat"][0], "OK")


class T03CannotWrite(Base):
    def test_writes_outside_workspace_denied(self):
        c = self.canary()
        ro = self.stage / "ro"; ro.mkdir(); (ro / "keep.txt").write_text("keep")
        r = self.run_sb(PRE + '''
E = os.environ
def w(p): open(os.path.join(E[p], "pwned.txt"), "w").write("x")
attempt("canary_dir", lambda: w("CANARY_DIR"))
attempt("ws_parent", lambda: w("WS_PARENT"))
attempt("win_temp", lambda: w("WIN_TEMP"))
attempt("ro_create", lambda: w("RO"))
attempt("ro_overwrite", lambda: open(os.path.join(E["RO"], "keep.txt"), "w").write("x"))
attempt("ro_delete", lambda: os.remove(os.path.join(E["RO"], "keep.txt")))
attempt("runtime_create", lambda: w("RT"))
attempt("ws_control", lambda: open("ok.txt", "w").write("x"))
''', ro=[ro], env={"CANARY_DIR": str(c), "WS_PARENT": str(self.stage), "WIN_TEMP": r"C:\Windows\Temp",
                   "RO": str(ro), "RT": str(self.py)})
        self.ok(r)
        res = parse(r["stdout"])
        for k in ("canary_dir", "ws_parent", "win_temp", "ro_create", "ro_overwrite", "ro_delete", "runtime_create"):
            self.assertEqual(res[k][0], "PermissionError", (k, res[k]))
        self.assertEqual(res["ws_control"][0], "OK")
        for p in (c / "pwned.txt", self.stage / "pwned.txt", Path(r"C:\Windows\Temp\pwned.txt"),
                  ro / "pwned.txt", self.py / "pwned.txt"):
            self.assertFalse(p.exists(), f"host sees {p}")
        self.assertEqual((ro / "keep.txt").read_text(), "keep")


class T04NoNetwork(Base):
    def test_no_loopback_no_internet_no_dns(self):
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(8); srv.settimeout(0.2)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); udp.bind(("127.0.0.1", 0)); udp.settimeout(4)
        port, hits, stop = srv.getsockname()[1], [], threading.Event()

        def acceptor():
            while not stop.is_set():
                try:
                    conn, addr = srv.accept(); conn.close(); hits.append(addr)
                except OSError:
                    pass
        threading.Thread(target=acceptor, daemon=True).start()
        try:  # positive control: a host connection IS seen
            socket.create_connection(("127.0.0.1", port), 2).close(); time.sleep(0.5)
            self.assertEqual(len(hits), 1); hits.clear()
            r = self.run_sb(PRE + '''
import socket
def c(h, p):
    s = socket.socket(); s.settimeout(3); s.connect((h, p)); s.close(); return "connected"
attempt("loopback", lambda: c("127.0.0.1", int(os.environ["PORT"])))
attempt("internet", lambda: c("1.1.1.1", 443))
attempt("dns", lambda: socket.getaddrinfo("example.com", 80))
def udp():
    u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); u.settimeout(2)
    u.sendto(b"ngsb-udp", ("127.0.0.1", int(os.environ["UDP_PORT"])))   # to host listener
    u.sendto(bytes.fromhex("123401000001000000000000076578616d706c6503636f6d0000010001"), ("1.1.1.1", 53))
    return u.recvfrom(512)                                             # a DNS answer must never arrive
attempt("udp", udp)
''', env={"PORT": str(port), "UDP_PORT": str(udp.getsockname()[1])}, timeout_s=60)
            self.ok(r)
            time.sleep(0.5)
            udp.settimeout(0.5)
            try:
                got = udp.recvfrom(100)
            except OSError:
                got = None
        finally:
            stop.set(); srv.close(); udp.close()
        res = parse(r["stdout"])
        for k in ("loopback", "internet", "dns"):
            self.assertNotEqual(res[k][0], "OK", (k, res[k]))
            self.assertIn(res[k][0], ("PermissionError", "TimeoutError", "gaierror", "ConnectionRefusedError",
                                      "OSError", "TimeoutError"), (k, res[k]))
        self.assertNotEqual(res["udp"][0], "OK", res["udp"])  # no reply from 1.1.1.1
        self.assertIsNone(got, "host UDP listener received a datagram from the sandbox")
        self.assertEqual(hits, [], "host listener saw a connection from the sandbox")
        self.addCleanup(print, "  [net evidence]", {k: v for k, v in res.items()})


class T05Env(Base):
    def test_host_env_not_inherited(self):
        os.environ["NGSB_SECRET"] = "top-secret"
        self.addCleanup(os.environ.pop, "NGSB_SECRET", None)
        r = self.run_sb(PRE + '''
import json; print("ENV|" + json.dumps(sorted(os.environ)) + "|", flush=True)
print("SECRET|" + str(os.environ.get("NGSB_SECRET")) + "|", flush=True)
print("PASSED|" + str(os.environ.get("MY_VAR")) + "|", flush=True)
''', env={"MY_VAR": "mine"})
        self.ok(r)
        self.assertNotIn("top-secret", r["stdout"])
        res = parse(r["stdout"])
        self.assertEqual(res["SECRET"][0], "None")
        self.assertEqual(res["PASSED"][0], "mine")
        import json
        keys = set(json.loads(res["ENV"][0]))
        self.assertLessEqual(keys, {"SYSTEMROOT", "TEMP", "TMP", "LOCALAPPDATA", "PYTHONUTF8",
                                    "PYTHONDONTWRITEBYTECODE", "MY_VAR"}, keys)
        # a second, non-Python view of the environment block
        r = self.run_sb(argv=[r"C:\Windows\System32\cmd.exe", "/c", "set"])
        self.ok(r)
        self.assertNotIn("NGSB_SECRET", r["stdout"]); self.assertNotIn("top-secret", r["stdout"])
        self.assertNotIn("USERPROFILE", r["stdout"].upper().replace("LOCALAPPDATA", ""))


class T06Links(Base):
    def test_junction_and_symlink_escape_blocked(self):
        c = self.canary()
        j = self.ws / "jlink"
        subprocess.run(["cmd", "/c", "mklink", "/J", str(j), str(c)], check=True, capture_output=True)
        self.links.append(j)
        self.assertEqual((j / "secret.txt").read_text(), "CANARY-SECRET-123")  # host follows the junction
        sym = self.ws / "slink"
        try:
            os.symlink(c, sym, target_is_directory=True); self.links.append(sym)
        except OSError:
            sym = None
        r = self.run_sb(PRE + '''
attempt("junction_read", lambda: open("jlink/secret.txt").read())
attempt("junction_list", lambda: os.listdir("jlink"))
if os.path.exists("slink") or os.path.islink("slink"):
    attempt("symlink_read", lambda: open("slink/secret.txt").read())
''')
        self.ok(r)
        self.assertNotIn("CANARY-SECRET", r["stdout"])
        res = parse(r["stdout"])
        self.assertEqual(res["junction_read"][0], "PermissionError", res)
        self.assertEqual(res["junction_list"][0], "PermissionError", res)
        if sym is not None and "symlink_read" in res:
            self.assertNotEqual(res["symlink_read"][0], "OK")


class T07Limits(Base):
    def test_memory_limit(self):
        r = self.run_sb('''
import sys
chunks = []
for i in range(1, 65):
    chunks.append(bytearray(64 * 1024 * 1024))
    print("ALLOC", i * 64, flush=True)
print("ALLOCATED_OK", flush=True)
''', memory_mb=256, timeout_s=60)
        self.assertNotIn("ALLOCATED_OK", r["stdout"])
        self.assertTrue(r["killed_by_job"] or r["rc"] != 0, r)
        got = [int(l.split()[1]) for l in r["stdout"].splitlines() if l.startswith("ALLOC ")]
        self.assertLessEqual(max(got or [0]), 256)
        self.assertIn("MemoryError", r["stderr"])  # job quota => allocation fails (not a silent kill)

    def test_process_limit(self):
        r = self.run_sb('''
import subprocess, sys
kids, errs = [], 0
for i in range(40):
    try:
        kids.append(subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    except OSError:
        errs += 1
print("SPAWNED", len(kids), "FAILED", errs, flush=True)
''', max_processes=8, timeout_s=60)
        self.ok(r)
        n_ok = int(r["stdout"].split("SPAWNED")[1].split()[0]); n_err = int(r["stdout"].split("FAILED")[1].split()[0])
        self.assertTrue(1 <= n_ok <= 7, r["stdout"])   # parent itself occupies one of the 8 slots
        self.assertGreater(n_err, 0)

    def test_infinite_loop_killed_with_whole_tree(self):
        r = self.run_sb('''
import subprocess, sys
k = subprocess.Popen([sys.executable, "-c", "while True: pass"])
print("CHILD", k.pid, flush=True)
while True: pass
''', timeout_s=3)
        self.assertTrue(r["timed_out"]); self.assertTrue(r["killed_by_job"]); self.assertIsNone(r["rc"])
        self.assertTrue(2.5 < r["latency_s"] < 20, r["latency_s"])
        pid = int(r["stdout"].split("CHILD")[1].split()[0])
        time.sleep(0.5)
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"], capture_output=True, text=True).stdout
        self.assertNotIn("python", out.lower(), f"child {pid} survived: {out}")


class T08Child(Base):
    def test_child_is_also_confined(self):
        c = self.canary()
        r = self.run_sb(PRE + '''
import subprocess
child = r"""
import os, ctypes
try:
    print('LEAK', open(os.environ['CANARY_FILE']).read())
except PermissionError:
    print('child-denied')
"""
p = subprocess.run([sys.executable, "-c", child], capture_output=True, text=True)
print("child_out|" + p.stdout.strip() + "|", flush=True)
''', env={"CANARY_FILE": str(c / "secret.txt")})
        self.ok(r)
        self.assertNotIn("LEAK", r["stdout"]); self.assertNotIn("CANARY-SECRET", r["stdout"])
        self.assertEqual(parse(r["stdout"])["child_out"][0], "child-denied")


class T09Process(Base):
    def test_cannot_open_host_process(self):
        r = self.run_sb(PRE + '''
import ctypes
k = ctypes.WinDLL("kernel32", use_last_error=True)
k.OpenProcess.restype = ctypes.c_void_p
k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
def op(pid, acc):
    h = k.OpenProcess(acc, 0, pid)
    return "handle" if h else f"denied err={ctypes.get_last_error()}"
print("parent_write_term|" + op(int(os.environ["PARENT_PID"]), 0x20 | 0x1) + "|", flush=True)
print("parent_vm_read|" + op(int(os.environ["PARENT_PID"]), 0x10) + "|", flush=True)
print("system4_term|" + op(4, 0x1) + "|", flush=True)
''', env={"PARENT_PID": str(os.getpid())})
        self.ok(r)
        res = parse(r["stdout"])
        for k in ("parent_write_term", "parent_vm_read", "system4_term"):
            self.assertTrue(res[k][0].startswith("denied err=5"), (k, res[k]))


class T10IO(Base):
    TXT = "مخزون ✓"

    def test_exact_capture_utf8_exit_code_stdin(self):
        (self.ws / "job.py").write_text('''
import sys
sys.stdout.buffer.write(("OUT:" + sys.argv[1] + "\\n").encode("utf-8"))
sys.stderr.buffer.write(("ERR:" + sys.argv[1] + "\\n").encode("utf-8"))
data = sys.stdin.buffer.read().decode("utf-8")
sys.stdout.buffer.write(("IN:" + data.upper() + "\\n").encode("utf-8"))
sys.exit(7)
''', encoding="utf-8")
        r = self.run_sb(argv=[str(self.py / "python.exe"), "job.py", self.TXT], stdin_text="abc " + self.TXT)
        self.assertEqual(r["rc"], 7)
        self.assertEqual(r["stdout"], f"OUT:{self.TXT}\nIN:ABC {self.TXT}\n")
        self.assertEqual(r["stderr"], f"ERR:{self.TXT}\n")
        self.assertFalse(r["timed_out"])

    def test_large_output_no_deadlock(self):
        r = self.run_sb('''
import sys
line = b"A" * 1023 + b"\\n"
for i in range(5120):          # 5 MiB on each stream, interleaved
    sys.stdout.buffer.write(line); sys.stderr.buffer.write(line)
''', timeout_s=60)
        self.ok(r)
        self.assertEqual(len(r["stdout"]), 5 * 1024 * 1024)
        self.assertEqual(len(r["stderr"]), 5 * 1024 * 1024)
        self.assertLess(r["latency_s"], 30)


class T11Php(Base):
    def test_php_pdo_sqlite_and_confinement(self):
        if not (PHP_SRC / "php.exe").exists():
            self.skipTest("no PHP")
        php = stage_cached("php", PHP_SRC)
        c = self.canary()
        srv = socket.socket(); srv.bind(("127.0.0.1", 0)); srv.listen(1); port = srv.getsockname()[1]
        (self.ws / "t.php").write_text(r'''<?php
$o = [];
$m = new PDO("sqlite::memory:"); $m->exec("create table t(a)"); $m->exec("insert into t values (42)");
$o["mem"] = $m->query("select a from t")->fetchColumn();
$f = new PDO("sqlite:" . getcwd() . "/file.db"); $f->exec("create table t(a)"); $f->exec("insert into t values ('persisted')");
$o["file"] = $f->query("select a from t")->fetchColumn();
$o["canary"] = @file_get_contents(getenv("CANARY_FILE"));
$o["sock"] = @stream_socket_client("tcp://127.0.0.1:" . getenv("PORT"), $en, $es, 3);
$o["sock_err"] = $en;
echo json_encode(["mem"=>$o["mem"], "file"=>$o["file"], "canary"=>$o["canary"], "sock"=>$o["sock"] !== false, "sock_err"=>$o["sock_err"]]);
''')
        flags = ["-n", "-d", f"extension_dir={php}\\ext", "-d", "extension=pdo_sqlite", "-d", "extension=sqlite3"]
        try:
            v = self.run_sb(argv=[str(php / "php.exe"), "-n", "-v"], runtime=php, ro=[self.py])
            self.ok(v); self.assertIn("PHP 8.2", v["stdout"])
            r = self.run_sb(argv=[str(php / "php.exe"), *flags, "t.php"], runtime=php,
                            env={"CANARY_FILE": str(c / "secret.txt"), "PORT": str(port)}, timeout_s=60)
            self.ok(r)
        finally:
            srv.close()
        import json
        j = json.loads(r["stdout"])
        self.assertEqual(str(j["mem"]), "42"); self.assertEqual(j["file"], "persisted")
        self.assertTrue((self.ws / "file.db").exists())
        self.assertFalse(j["canary"]); self.assertNotIn("CANARY-SECRET", r["stdout"])
        self.assertFalse(j["sock"], j)


class T12Cleanup(Base):
    def test_profile_and_staging_cleanup(self):
        before_dirs = set(glob.glob(str(TMP / "ngsb-*"))); before_maps = count_mappings()
        r = self.run_sb("print('hi')")
        self.ok(r)
        name, sid = r["sandbox"]["profile"], r["sandbox"]["sid"]
        self.assertFalse(sb.profile_exists(name))
        with self.assertRaises(OSError):  # registry mapping key is gone
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, sb._MAPPINGS + "\\" + sid)
        self.assertFalse((Path(os.environ["LOCALAPPDATA"]) / "Packages" / name).exists())
        self.assertEqual(set(glob.glob(str(TMP / "ngsb-*"))), before_dirs)  # run created no stray dirs
        self.assertEqual(count_mappings(), before_maps)
        # ACEs for the dead SID were revoked from the cached runtime and the workspace
        acl = subprocess.run(["icacls", str(self.py)], capture_output=True, text=True).stdout
        self.assertNotIn(sid, acl)
        # failing launch still cleans up
        with self.assertRaises(OSError):
            sb.run_sandboxed([r"C:\nonexistent\x.exe"], workspace=self.ws)
        self.assertEqual(count_mappings(), before_maps)
        # other staging dirs from other tests are gone (each test removes its own)
        self.assertEqual({Path(p).name for p in glob.glob(str(TMP / "ngsb-run-*"))}, {self.stage.name})


class T13Token(Base):
    def test_not_admin_low_integrity_appcontainer(self):
        self.assertTrue(ctypes.windll.shell32.IsUserAnAdmin(), "host not elevated: caveat test is weaker")
        r = self.run_sb(PRE + r'''
import ctypes
from ctypes import wintypes as wt
a, k = ctypes.WinDLL("advapi32", use_last_error=True), ctypes.WinDLL("kernel32", use_last_error=True)
k.GetCurrentProcess.restype = ctypes.c_void_p
a.OpenProcessToken.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.POINTER(ctypes.c_void_p)]
a.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
a.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte); a.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
a.GetSidSubAuthority.restype = ctypes.POINTER(wt.DWORD); a.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wt.DWORD]
tok = ctypes.c_void_p()
assert a.OpenProcessToken(k.GetCurrentProcess(), 8, ctypes.byref(tok))   # TOKEN_QUERY
def q(cls, size=4096):
    b = ctypes.create_string_buffer(size); n = wt.DWORD()
    assert a.GetTokenInformation(tok, cls, b, size, ctypes.byref(n)), ctypes.get_last_error(); return b
print("is_appcontainer|%d|" % ctypes.c_uint32.from_buffer(q(29, 4)).value)    # TokenIsAppContainer
print("is_elevated|%d|" % ctypes.c_uint32.from_buffer(q(20, 4)).value)        # TokenElevation.TokenIsElevated
b = q(25)                                                                 # TokenIntegrityLevel -> TOKEN_MANDATORY_LABEL
sid = ctypes.c_void_p.from_buffer(b).value
n = a.GetSidSubAuthorityCount(sid)[0]
print("integrity_rid|0x%x|" % a.GetSidSubAuthority(sid, n - 1)[0])
''')
        self.ok(r)
        res = parse(r["stdout"])
        self.assertEqual(res["is_appcontainer"][0], "1"); self.assertEqual(res["is_elevated"][0], "0")
        self.assertEqual(res["integrity_rid"][0], "0x1000")  # Low
        w = self.run_sb(argv=[r"C:\Windows\System32\whoami.exe", "/groups"])
        self.ok(w)
        self.assertIn("Low Mandatory Level", w["stdout"])
        self.assertIn("deny only", w["stdout"].lower())  # admin groups are deny-only (see ngsandbox._filtered_token)
        for line in w["stdout"].splitlines():
            if "S-1-5-32-544" in line:  # Administrators may only appear as deny-only
                self.assertIn("deny only", line.lower())


class T14Limitations(Base):
    def test_documented_limitation_system_dirs_readable(self):
        """LIMITATION (documented, asserted as-is): every AppContainer carries the well-known group
        ALL APPLICATION PACKAGES, which the OS grants read on C:\\Windows and Program Files. Without
        LPAC (needs per-process policy + capability opt-ins that break python.exe loading) these stay
        readable. Secrets must therefore never live there; user-profile data is NOT readable (T02)."""
        r = self.run_sb(PRE + '''
attempt("win_ini", lambda: len(open(r"C:\\Windows\\win.ini").read()) >= 0)
''')
        self.ok(r)
        self.assertEqual(parse(r["stdout"])["win_ini"][0], "OK")


if __name__ == "__main__":
    unittest.main(verbosity=2)
