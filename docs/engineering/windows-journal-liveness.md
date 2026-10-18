# Windows journal liveness

Default journal reconciliation must leave records alone while their owner may
still be running. Python's `os.kill(pid, 0)` is a POSIX existence check, but on
Windows it calls `TerminateProcess`. The Windows branch now inspects a native
process handle's signaled state through the Python standard library; it never
signals a process.

Only a signaled process handle with successful cleanup establishes exit. Access
refusal, query failure, an unavailable API or an invalid nonzero PID keeps the
owner live for reconciliation. Missing/zero owner IDs mean no recorded owner. Windows
PIDs are checked before native conversion so oversized values cannot wrap to a
different process ID.

This is an existence query, not proof of the original process's identity. A
reused PID that is currently live keeps the old record pending. Clock expiry
does not authorize takeover. The service's kernel-held `ProfileLock` remains
the authority for its existing `force=True` reconciliation after lock acquisition.
This change adds no takeover route and does not enable Windows or macOS sorting,
Undo, recovery mutation or metadata writes.

## Native API boundary

`OpenProcess` requests only `SYNCHRONIZE`, with handle inheritance disabled.
`WaitForSingleObject(handle, 0)` checks the process without blocking. Only
`WAIT_OBJECT_0` means the opened process exited; every other result is treated
as live or unknown. Each opened handle is passed to `CloseHandle` exactly once
in a `finally` block. A close failure also keeps the record pending. Explicit
ctypes signatures preserve pointer-width handles on 64-bit Windows.

The helper requests no termination, memory-write, debug or privilege adjustment
capability. It does not query the process's exit code, so an exit code equal to
`STILL_ACTIVE` cannot confuse it. If a PID cannot be opened, including after its
process object disappeared, the query conservatively retains the record. The
kernel-locked service recovery path remains responsible for authoritative
takeover; this helper does not infer it from PID absence or expiry.

The existing psutil library was considered, but the inspected Windows versions
attempt to enable debug privilege during module initialization. This narrow
query therefore uses only the three native functions above, without importing
psutil or changing that unrelated application dependency.

Primary references:

- [Python 3.12 `os.kill`](https://docs.python.org/3.12/library/os.html#os.kill)
- [Microsoft OpenProcess](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-openprocess)
- [Microsoft zero-timeout wait semantics](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject)
- [Microsoft CloseHandle](https://learn.microsoft.com/en-us/windows/win32/api/handleapi/nf-handleapi-closehandle)
- [Microsoft process access rights](https://learn.microsoft.com/en-us/windows/win32/procthread/process-security-and-access-rights)
- [psutil 5.9.0 Windows initialization](https://github.com/giampaolo/psutil/blob/release-5.9.0/psutil/_psutil_windows.c#L1627)

## Qualification

`tests/test_mutation_platform.py` is already selected by Windows and macOS CI.
Its deterministic regressions replace only the journal's OS API view, fail on
any attempted signal, and cover confirmed exit, live/unknown owners, query and
cleanup errors, missing APIs, 64-bit handles, PID conversion and reuse. Default reconciliation
must leave both legacy and current records unchanged for uncertain owners.

The native test starts one harmless child waiting on its own stdin. Repeated
liveness queries and default reconciliation must leave it alive and its journal
record unchanged; the test then asks it to exit through stdin and requires a
confirmed stopped result while `Popen` still retains its process handle. An
always-unknown fallback cannot pass that assertion. On Windows a
journal-only signal tripwire remains active, so a regression fails before it can
call `os.kill`. No external process is terminated by this test.

Linux results establish local and simulated Windows behavior only. The native
Windows CI run is still required for this revision. This narrow test does not
qualify Windows filesystem transactions, device removal or power-loss recovery.
