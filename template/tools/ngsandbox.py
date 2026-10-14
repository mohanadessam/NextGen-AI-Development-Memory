"""ngsandbox - Windows process sandbox: per-run AppContainer + Job Object (stdlib + ctypes only).

Boundary (all OS-enforced, no Windows feature / driver / account needed):
  * AppContainer token, ZERO capabilities  -> no network (not even loopback), Low integrity,
    no admin rights, file access only where the per-run AppContainer SID has been granted an ACE
    (plus the system-wide "ALL APPLICATION PACKAGES" read ACEs on C:\\Windows / Program Files).
  * Job Object: kill-on-close, per-process memory cap, active-process cap, CPU-time cap,
    UI restrictions; no breakaway, so every descendant stays in the job and in the AppContainer.
  * Explicit environment block (host env never inherited) + inheritable pipes only
    (PROC_THREAD_ATTRIBUTE_HANDLE_LIST limits inheritance to the 3 std handles).

Public API: run_sandboxed(), stage_runtime(), available(), profile_exists().
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import threading
import time
import uuid
import winreg
from ctypes import wintypes as wt
from pathlib import Path

# --------------------------------------------------------------------------- DLLs
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
userenv = ctypes.WinDLL("userenv", use_last_error=True)
advapi = ctypes.WinDLL("advapi32", use_last_error=True)

PVOID = ctypes.c_void_p
HRESULT = ctypes.c_long
ULONG_PTR = ctypes.c_size_t


# --------------------------------------------------------------------------- structures
class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Sid", PVOID), ("Attributes", wt.DWORD)]


class SECURITY_CAPABILITIES(ctypes.Structure):
    # AppContainerSid : the per-run package SID that becomes the token's "user"
    # Capabilities    : array of SID_AND_ATTRIBUTES; we pass NULL / 0 => no capabilities
    #                   (no internetClient, no privateNetworkClientServer, ...) => no sockets out.
    _fields_ = [("AppContainerSid", PVOID), ("Capabilities", PVOID),
                ("CapabilityCount", wt.DWORD), ("Reserved", wt.DWORD)]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("nLength", wt.DWORD), ("lpSecurityDescriptor", PVOID), ("bInheritHandle", wt.BOOL)]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR),
                ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD),
                ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD), ("dwXCountChars", wt.DWORD),
                ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD), ("dwFlags", wt.DWORD),
                ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD), ("lpReserved2", PVOID),
                ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE), ("hStdError", wt.HANDLE)]


class STARTUPINFOEXW(ctypes.Structure):
    # StartupInfo + opaque attribute list (built by InitializeProcThreadAttributeList)
    _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", PVOID)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE),
                ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ULONG_PTR),
                ("MaximumWorkingSetSize", ULONG_PTR), ("ActiveProcessLimit", wt.DWORD),
                ("Affinity", ULONG_PTR), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in
                ("ReadOp", "WriteOp", "OtherOp", "ReadBytes", "WriteBytes", "OtherBytes")]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ULONG_PTR), ("JobMemoryLimit", ULONG_PTR),
                ("PeakProcessMemoryUsed", ULONG_PTR), ("PeakJobMemoryUsed", ULONG_PTR)]


class JOBOBJECT_BASIC_ACCOUNTING_INFORMATION(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64), ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64), ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wt.DWORD), ("TotalProcesses", wt.DWORD),
                ("ActiveProcesses", wt.DWORD), ("TotalTerminatedProcesses", wt.DWORD)]


# --------------------------------------------------------------------------- constants
PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_SUSPENDED = 0x4
CREATE_NO_WINDOW = 0x08000000
CREATE_UNICODE_ENVIRONMENT = 0x400
STARTF_USESTDHANDLES = 0x100
HANDLE_FLAG_INHERIT = 1
WAIT_TIMEOUT = 0x102
INFINITE = 0xFFFFFFFF
HRESULT_ALREADY_EXISTS = 0x800700B7
JOB_LIMIT_JOB_TIME, JOB_LIMIT_ACTIVE_PROCESS, JOB_LIMIT_PROCESS_MEMORY = 0x4, 0x8, 0x100
JOB_LIMIT_DIE_ON_UNHANDLED_EXCEPTION, JOB_LIMIT_KILL_ON_JOB_CLOSE = 0x400, 0x2000
JobObjectBasicAccountingInformation, JobObjectBasicUIRestrictions, JobObjectExtendedLimitInformation = 1, 4, 9
MAX_CAPTURE = 64 * 1024 * 1024  # per stream; excess is drained and dropped

# --------------------------------------------------------------------------- prototypes
def _proto(fn, restype, *argtypes):
    fn.restype, fn.argtypes = restype, list(argtypes)

_proto(userenv.CreateAppContainerProfile, HRESULT, wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, PVOID, wt.DWORD, ctypes.POINTER(PVOID))
_proto(userenv.DeleteAppContainerProfile, HRESULT, wt.LPCWSTR)
_proto(userenv.DeriveAppContainerSidFromAppContainerName, HRESULT, wt.LPCWSTR, ctypes.POINTER(PVOID))
_proto(advapi.ConvertSidToStringSidW, wt.BOOL, PVOID, ctypes.POINTER(wt.LPWSTR))
_proto(advapi.FreeSid, PVOID, PVOID)
_proto(k32.LocalFree, PVOID, PVOID)
_proto(k32.CloseHandle, wt.BOOL, wt.HANDLE)
_proto(k32.CreatePipe, wt.BOOL, ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE), ctypes.POINTER(SECURITY_ATTRIBUTES), wt.DWORD)
_proto(k32.SetHandleInformation, wt.BOOL, wt.HANDLE, wt.DWORD, wt.DWORD)
_proto(k32.ReadFile, wt.BOOL, wt.HANDLE, PVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), PVOID)
_proto(k32.WriteFile, wt.BOOL, wt.HANDLE, PVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), PVOID)
_proto(k32.InitializeProcThreadAttributeList, wt.BOOL, PVOID, wt.DWORD, wt.DWORD, ctypes.POINTER(ULONG_PTR))
_proto(k32.UpdateProcThreadAttribute, wt.BOOL, PVOID, wt.DWORD, ULONG_PTR, PVOID, ULONG_PTR, PVOID, PVOID)
_proto(k32.DeleteProcThreadAttributeList, None, PVOID)
_proto(k32.CreateProcessW, wt.BOOL, wt.LPCWSTR, wt.LPWSTR, PVOID, PVOID, wt.BOOL, wt.DWORD, PVOID,
       wt.LPCWSTR, ctypes.POINTER(STARTUPINFOEXW), ctypes.POINTER(PROCESS_INFORMATION))
_proto(k32.GetCurrentProcess, wt.HANDLE)
_proto(advapi.OpenProcessToken, wt.BOOL, wt.HANDLE, wt.DWORD, ctypes.POINTER(wt.HANDLE))
_proto(advapi.CreateRestrictedToken, wt.BOOL, wt.HANDLE, wt.DWORD, wt.DWORD, PVOID, wt.DWORD, PVOID,
       wt.DWORD, PVOID, ctypes.POINTER(wt.HANDLE))
_proto(advapi.CreateProcessAsUserW, wt.BOOL, wt.HANDLE, wt.LPCWSTR, wt.LPWSTR, PVOID, PVOID, wt.BOOL, wt.DWORD,
       PVOID, wt.LPCWSTR, ctypes.POINTER(STARTUPINFOEXW), ctypes.POINTER(PROCESS_INFORMATION))
_proto(k32.CreateJobObjectW, wt.HANDLE, PVOID, wt.LPCWSTR)
_proto(k32.SetInformationJobObject, wt.BOOL, wt.HANDLE, ctypes.c_int, PVOID, wt.DWORD)
_proto(k32.QueryInformationJobObject, wt.BOOL, wt.HANDLE, ctypes.c_int, PVOID, wt.DWORD, PVOID)
_proto(k32.AssignProcessToJobObject, wt.BOOL, wt.HANDLE, wt.HANDLE)
_proto(k32.TerminateJobObject, wt.BOOL, wt.HANDLE, wt.UINT)
_proto(k32.ResumeThread, wt.DWORD, wt.HANDLE)
_proto(k32.TerminateProcess, wt.BOOL, wt.HANDLE, wt.UINT)
_proto(k32.WaitForSingleObject, wt.DWORD, wt.HANDLE, wt.DWORD)
_proto(k32.GetExitCodeProcess, wt.BOOL, wt.HANDLE, ctypes.POINTER(wt.DWORD))


def _winerr(what: str) -> OSError:
    return OSError(f"{what} failed: WinError {ctypes.get_last_error()}")


# --------------------------------------------------------------------------- AppContainer profile
def _sid_to_str(psid) -> str:
    out = wt.LPWSTR()
    if not advapi.ConvertSidToStringSidW(psid, ctypes.byref(out)):
        raise _winerr("ConvertSidToStringSid")
    try:
        return out.value
    finally:
        k32.LocalFree(ctypes.cast(out, PVOID))


def _create_profile(name: str):
    """Create (or reopen) the AppContainer profile; returns (PSID, sid_string)."""
    psid = PVOID()
    hr = userenv.CreateAppContainerProfile(name, name, "ngsandbox per-run profile", None, 0, ctypes.byref(psid))
    if hr & 0xFFFFFFFF == HRESULT_ALREADY_EXISTS:
        hr = userenv.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(psid))
    if hr != 0:
        raise OSError(f"CreateAppContainerProfile failed: HRESULT 0x{hr & 0xFFFFFFFF:08X}")
    return psid, _sid_to_str(psid)


def _delete_profile(name: str) -> None:
    userenv.DeleteAppContainerProfile(name)


_MAPPINGS = r"Software\Classes\Local Settings\Software\Microsoft\Windows\CurrentVersion\AppContainer\Mappings"


def profile_exists(name: str) -> bool:
    """True if the AppContainer profile is still registered (registry mapping key or Packages folder)."""
    psid = PVOID()
    if userenv.DeriveAppContainerSidFromAppContainerName(name, ctypes.byref(psid)) != 0:
        return False
    sid = _sid_to_str(psid)
    advapi.FreeSid(psid)
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MAPPINGS + "\\" + sid))
        return True
    except OSError:
        pass
    return (Path(os.environ.get("LOCALAPPDATA", "")) / "Packages" / name).exists()


def available() -> tuple[bool, str]:
    """True only if an AppContainer profile can really be created (and removed) here."""
    name = "ngsb-probe" + uuid.uuid4().hex[:12]
    try:
        psid, sid = _create_profile(name)
        advapi.FreeSid(psid)
        return True, f"AppContainer creation works (probe SID {sid})"
    except Exception as e:  # noqa: BLE001
        return False, str(e)
    finally:
        _delete_profile(name)


# --------------------------------------------------------------------------- ACLs (staging dirs only)
def _icacls(path: Path, *args: str) -> None:
    r = subprocess.run(["icacls", str(path), *args, "/C", "/Q"], capture_output=True, text=True)
    if r.returncode != 0:
        raise OSError(f"icacls {args} on {path} failed: {r.stdout}{r.stderr}")


def _grant(path: Path, sid: str, rights: str) -> None:
    """rights: 'M' (modify) or 'RX'. Directories get inheritable ACEs (propagated to children)."""
    flags = "(OI)(CI)" if path.is_dir() else ""
    _icacls(path, "/grant", f"*{sid}:{flags}({rights})")


def _revoke(path: Path, sid: str) -> None:
    try:
        _icacls(path, "/remove:g", f"*{sid}")
    except OSError:
        pass


def stage_runtime(src_dir: Path, dst_dir: Path) -> Path:
    """Copy a runtime (Python / PHP ...) into a staging dir. Bulky/irrelevant dirs are skipped.

    The AppContainer SID only exists per run, so the read+execute ACE for it is granted on this
    copy (and only on this copy) by run_sandboxed() when the copy is passed in readonly_dirs.
    """
    src, dst = Path(src_dir), Path(dst_dir)
    skip = {"site-packages", "__pycache__", "Doc", "include", "libs", "Tools", "tcl", "test", "tests",
            "dev", "docs", "extras", "sapi", "licenses", "pear", "phpunit", "www", "man",
            "windowsXamppPhp", "tmp", "CompatInfo", "pci", "pciconf", "scripts", "cfg"}
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=lambda d, names: [n for n in names if n in skip])
    return dst


# --------------------------------------------------------------------------- job object
def _make_job(timeout_s: float, memory_mb: int, max_processes: int):
    job = k32.CreateJobObjectW(None, None)
    if not job:
        raise _winerr("CreateJobObject")
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    b = info.BasicLimitInformation
    b.LimitFlags = (JOB_LIMIT_KILL_ON_JOB_CLOSE | JOB_LIMIT_PROCESS_MEMORY | JOB_LIMIT_ACTIVE_PROCESS
                    | JOB_LIMIT_JOB_TIME | JOB_LIMIT_DIE_ON_UNHANDLED_EXCEPTION)
    b.ActiveProcessLimit = max_processes
    b.PerJobUserTimeLimit = int(timeout_s * 2 * 10_000_000)  # CPU (100ns units), 2x wall to allow multi-core
    info.ProcessMemoryLimit = memory_mb * 1024 * 1024
    if not k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
        raise _winerr("SetInformationJobObject(limits)")
    ui = wt.DWORD(0xFF)  # no USER handles outside job, clipboard, sysparams, display, desktop, exitwindows
    k32.SetInformationJobObject(job, JobObjectBasicUIRestrictions, ctypes.byref(ui), 4)
    return job


def _terminated_by_job(job) -> int:
    acc = JOBOBJECT_BASIC_ACCOUNTING_INFORMATION()
    if k32.QueryInformationJobObject(job, JobObjectBasicAccountingInformation, ctypes.byref(acc), ctypes.sizeof(acc), None):
        return acc.TotalTerminatedProcesses
    return 0


# --------------------------------------------------------------------------- de-elevated base token
def _filtered_token() -> int:
    """Primary token = caller's token minus admin power (CreateRestrictedToken with
    DISABLE_MAX_PRIVILEGE | LUA_TOKEN: all privileges except SeChangeNotify removed, Administrators and
    other admin-ish groups become deny-only, integrity Medium). The AppContainer attribute is then applied
    on top of it, so the child has: no admin SIDs, no privileges, Low IL, AppContainer package SID."""
    src, out = wt.HANDLE(), wt.HANDLE()
    # TOKEN_DUPLICATE | TOKEN_ASSIGN_PRIMARY | TOKEN_QUERY
    if not advapi.OpenProcessToken(k32.GetCurrentProcess(), 0x1 | 0x2 | 0x8, ctypes.byref(src)):
        raise _winerr("OpenProcessToken")
    try:
        if not advapi.CreateRestrictedToken(src, 0x1 | 0x4, 0, None, 0, None, 0, None, ctypes.byref(out)):
            raise _winerr("CreateRestrictedToken")
    finally:
        k32.CloseHandle(src)
    return out.value


# --------------------------------------------------------------------------- pipes
def _pipe(child_reads: bool):
    """Anonymous pipe; returns (parent_end, child_end). Only the child end stays inheritable."""
    sa = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
    r, w = wt.HANDLE(), wt.HANDLE()
    if not k32.CreatePipe(ctypes.byref(r), ctypes.byref(w), ctypes.byref(sa), 0):
        raise _winerr("CreatePipe")
    parent, child = (w.value, r.value) if child_reads else (r.value, w.value)
    k32.SetHandleInformation(parent, HANDLE_FLAG_INHERIT, 0)
    return parent, child


def _drain(h, chunks: list) -> None:
    buf, n, total = ctypes.create_string_buffer(65536), wt.DWORD(), 0
    while k32.ReadFile(h, buf, 65536, ctypes.byref(n), None) and n.value:
        if total < MAX_CAPTURE:
            chunks.append(buf.raw[:n.value])
        total += n.value


def _feed(h, data: bytes) -> None:
    n, view = wt.DWORD(), ctypes.create_string_buffer(data, len(data))
    off = 0
    while off < len(data):
        if not k32.WriteFile(h, ctypes.byref(view, off), min(len(data) - off, 65536), ctypes.byref(n), None):
            break
        off += n.value
    k32.CloseHandle(h)


# --------------------------------------------------------------------------- main entry
def _env_block(workspace: Path, env: dict | None) -> ctypes.Array:
    tmp = workspace / ".tmp"
    tmp.mkdir(exist_ok=True)
    e = {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "TEMP": str(tmp), "TMP": str(tmp),
         # CreateProcess for an AppContainer fails (WinError 203) unless LOCALAPPDATA is in the block
         "LOCALAPPDATA": str(tmp)}
    e.update(env or {})
    items = sorted(e.items(), key=lambda kv: kv[0].upper())
    # UTF-16LE, each "k=v" NUL-terminated + a final NUL. (create_unicode_buffer truncates at NULs.)
    return ctypes.create_string_buffer(("".join(f"{k}={v}\0" for k, v in items) + "\0").encode("utf-16-le"))


def run_sandboxed(argv, *, workspace, readonly_dirs=(), env=None, timeout_s=120, memory_mb=1024,
                  max_processes=32, stdin_text=None) -> dict:
    workspace = Path(workspace).resolve()
    ro = [Path(p).resolve() for p in readonly_dirs]
    name = "ngsb" + uuid.uuid4().hex[:24]
    granted: list[Path] = []
    psid = job = sid = None
    handles: list[int] = []
    t0 = time.perf_counter()
    res = {"rc": None, "stdout": "", "stderr": "", "timed_out": False, "killed_by_job": False}
    try:
        psid, sid = _create_profile(name)
        for p in ro:
            _grant(p, sid, "RX"); granted.append(p)
        _grant(workspace, sid, "M"); granted.append(workspace)

        in_p, in_c = _pipe(True); out_p, out_c = _pipe(False); err_p, err_c = _pipe(False)
        handles += [in_p, in_c, out_p, out_c, err_p, err_c]
        job = _make_job(timeout_s, memory_mb, max_processes)
        envbuf = _env_block(workspace, env)

        # Attribute list with 2 entries: security capabilities (AppContainer) + handle whitelist.
        size = ULONG_PTR()
        k32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
        attr = ctypes.create_string_buffer(size.value)
        if not k32.InitializeProcThreadAttributeList(attr, 2, 0, ctypes.byref(size)):
            raise _winerr("InitializeProcThreadAttributeList")
        try:
            caps = SECURITY_CAPABILITIES(psid, None, 0, 0)  # ZERO capabilities
            if not k32.UpdateProcThreadAttribute(attr, 0, PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                                                 ctypes.byref(caps), ctypes.sizeof(caps), None, None):
                raise _winerr("UpdateProcThreadAttribute(SECURITY_CAPABILITIES)")
            hl = (wt.HANDLE * 3)(in_c, out_c, err_c)
            if not k32.UpdateProcThreadAttribute(attr, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST,
                                                 hl, ctypes.sizeof(hl), None, None):
                raise _winerr("UpdateProcThreadAttribute(HANDLE_LIST)")
            si = STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(si)
            si.StartupInfo.dwFlags = STARTF_USESTDHANDLES
            si.StartupInfo.hStdInput, si.StartupInfo.hStdOutput, si.StartupInfo.hStdError = in_c, out_c, err_c
            si.lpAttributeList = ctypes.addressof(attr)
            pi = PROCESS_INFORMATION()
            cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(a) for a in argv]))
            flags = (EXTENDED_STARTUPINFO_PRESENT | CREATE_SUSPENDED | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT)
            tok = _filtered_token(); handles.append(tok)
            if not advapi.CreateProcessAsUserW(tok, None, cmd, None, None, True, flags, envbuf, str(workspace),
                                               ctypes.byref(si), ctypes.byref(pi)):
                raise _winerr("CreateProcessAsUserW")
        finally:
            k32.DeleteProcThreadAttributeList(attr)
        handles += [pi.hProcess, pi.hThread]
        # Suspended -> into the job -> resume: the process never runs outside the job.
        if not k32.AssignProcessToJobObject(job, pi.hProcess):
            k32.TerminateProcess(pi.hProcess, 1)
            raise _winerr("AssignProcessToJobObject")
        # Close our copies of the child ends so EOF is seen when the tree dies.
        for h in (in_c, out_c, err_c):
            k32.CloseHandle(h); handles.remove(h)
        k32.ResumeThread(pi.hThread)

        outc, errc = [], []
        threads = [threading.Thread(target=_drain, args=(out_p, outc), daemon=True),
                   threading.Thread(target=_drain, args=(err_p, errc), daemon=True),
                   threading.Thread(target=_feed, args=(in_p, (stdin_text or "").encode("utf-8")), daemon=True)]
        handles.remove(in_p)  # owned by the feeder thread now
        for t in threads:
            t.start()
        if k32.WaitForSingleObject(pi.hProcess, int(timeout_s * 1000)) == WAIT_TIMEOUT:
            res["timed_out"] = True
        code = wt.DWORD()
        if not res["timed_out"] and k32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code)):
            res["rc"] = code.value
        res["killed_by_job"] = res["timed_out"] or _terminated_by_job(job) > 0
        k32.TerminateJobObject(job, 1)  # whole tree dies, even orphans holding our pipes
        for t in threads:
            t.join(15)
        res["stdout"] = b"".join(outc).decode("utf-8", "replace")
        res["stderr"] = b"".join(errc).decode("utf-8", "replace")
        res["sandbox"] = {"kind": "appcontainer+job", "sid": sid, "network": "none", "profile": name}
    finally:
        if job:
            k32.TerminateJobObject(job, 1)
            k32.CloseHandle(job)  # KILL_ON_JOB_CLOSE as a second line of defence
        for h in handles:
            k32.CloseHandle(h)
        for p in granted:
            _revoke(p, sid) if psid else None
        if psid:
            advapi.FreeSid(psid)
        _delete_profile(name)
        res["latency_s"] = time.perf_counter() - t0
    return res
