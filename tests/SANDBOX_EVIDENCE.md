# ngsandbox evidence (AppContainer + Job Object, stdlib/ctypes only)

Files: `template/tools/ngsandbox.py`, `tests/test_sandbox.py`. Run: `python tests/test_sandbox.py`.
Result of the final run (Windows 11 26200, Python 3.12.10, elevated host): **Ran 17, passed 17, failed 0, skipped 0** (about 18 s). Run twice in a row, same result.

## Design
- Per-run AppContainer profile (`CreateAppContainerProfile`), `STARTUPINFOEX` with `PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES` (0 capabilities) and `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` (only the 3 std pipes are inherited).
- Base token = caller token passed through `CreateRestrictedToken(DISABLE_MAX_PRIVILEGE|LUA_TOKEN)`, then `CreateProcessAsUserW`. Reason: a plain AppContainer child of an elevated parent still showed `BUILTIN\Administrators` as ENABLED, `TokenIsElevated=1`, and 2 privileges (the T13 test failed on that first version). With the filtered token: Administrators are deny-only, only SeChangeNotify remains, IL = Low, AppContainer = 1.
- Job Object: KILL_ON_JOB_CLOSE, process memory limit, active-process limit, job CPU limit (2x timeout), die-on-unhandled-exception, UI restrictions 0xFF. Process is created suspended, assigned to the job, then resumed. No breakaway.
- Env block is built explicitly (SystemRoot, TEMP, TMP, LOCALAPPDATA + caller vars). `LOCALAPPDATA` is mandatory: without it `CreateProcess` for an AppContainer fails with WinError 203. Windows then redirects TEMP/TMP/LOCALAPPDATA to `<workspace>\.tmp\Packages\<profile>\AC`, so it all stays inside the workspace.
- ACLs (icacls) only on the workspace and on readonly dirs passed in (the staged runtime copy); ACEs are revoked in `finally`, then the profile is deleted. Because the SID is per run, `stage_runtime` only copies; the per-run RX grant happens in `run_sandboxed` when the copy is in `readonly_dirs`.

## Results
| # | Test | Result | Evidence |
|---|------|--------|----------|
| 1 | positive: run python, read ro, rw workspace, unittest | PASS | ro file read, host sees out.txt, `python -m unittest pkg.test_a` rc 0 "OK" |
| 2 | canary outside grants unreadable | PASS | open/listdir of canary dir and of %USERPROFILE% -> PermissionError; content never in stdout |
| 3 | no writes outside workspace | PASS | canary dir, ws parent, C:\Windows\Temp, ro dir (create/overwrite/delete), runtime dir -> PermissionError; host confirms nothing created, ro file unchanged |
| 4 | no network | PASS | loopback connect -> TimeoutError (WFP silently drops), 1.1.1.1:443 -> WinError 10013, DNS -> gaierror 11001, UDP to 1.1.1.1:53 no reply; host TCP listener 0 hits and UDP listener received nothing (positive control: host's own connection was seen) |
| 5 | environment not inherited | PASS | NGSB_SECRET absent in python and in `cmd /c set`; env keys subset of SystemRoot/TEMP/TMP/LOCALAPPDATA/passed vars |
| 6 | junction/symlink escape | PASS | read and listdir through junction -> PermissionError (symlink too if created) |
| 7a | memory limit | PASS | 256 MB cap: allocation fails with MemoryError after <=256 MB, rc!=0 (job quota fails the commit; process is not silently killed) |
| 7b | process limit | PASS | max_processes=8: 1..7 children spawned, remaining Popen raised OSError |
| 7c | timeout kills tree | PASS | timed_out, rc None, ~3 s; grandchild PID gone per tasklist |
| 8 | child also confined | PASS | python child of sandboxed python gets PermissionError on canary |
| 9 | host process untouchable | PASS | OpenProcess(VM_WRITE\|TERMINATE), VM_READ on parent PID and TERMINATE on PID 4 -> err 5 |
| 10 | exact IO | PASS | Arabic `مخزون ✓` round-trips via argv/stdout/stderr/stdin, rc 7 exact, 5 MiB on each of stdout+stderr captured without deadlock |
| 11 | PHP | PASS | `php -v` (8.2), PDO sqlite memory + file DB in workspace, canary unreadable, socket to host listener fails |
| 12 | cleanup | PASS | registry mapping key and Packages folder gone, no stray ngsb-* dirs, runtime ACEs revoked, failed launch leaves no profile; whole-suite check in tearDownModule |
| 13 | token: not admin | PASS | IsAppContainer=1, IsElevated=0, integrity 0x1000 (Low); `whoami /groups`: Administrators "Group used for deny only" |
| 14 | system dirs readable | LIMITATION (asserted as-is) | C:\Windows\win.ini readable via ALL APPLICATION PACKAGES ACEs |

## Residual limitations
- Shared kernel: no VM boundary; a kernel/driver or win32k exploit escapes. Low-IL AppContainer reduces, not removes, that surface (GPU/driver and win32k reachable via DLLs).
- ALL APPLICATION PACKAGES makes C:\Windows, System32 and Program Files readable (LPAC would shrink that but breaks python.exe loading). Never keep secrets there.
- Network: zero capabilities blocks outbound and loopback in tests here, but this is WFP policy; loopback appears as timeout, not refusal. No firewall/loopback exemptions were touched. Named pipes / local RPC to system services remain reachable if their DACL admits AppContainers.
- Memory limit is a commit quota (allocation failure), not a kill; CPU cap is job-wide at 2x timeout, wall timeout is the real stop. Disk quota and file-count limits are not enforced (workspace can be filled).
- Readonly dirs and workspace get ACEs on the host for the duration of the run; a crash of the host python between grant and `finally` leaves an orphan ACE and profile.
- Tests rely on the host being Windows 11 with the per-user profile registry; the cached runtime lives in %TEMP%\ngsb-runtime (kept on purpose).
