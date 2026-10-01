# ShellCommand process ownership

On POSIX, each command starts a new session and process group with
`start_new_session=True`. Arguments, allowlist, working directory, environment,
and stdout/stderr result shape are unchanged.

Cancellation and timeout signal only that command's process group: SIGTERM,
250 ms graceful period, then SIGKILL. The command's direct process is awaited
and its stdout/stderr readers are drained. Cancellation during process creation
recovers the actual process handle before stopping it. Repeated cancellation
cannot abandon cleanup. A leader that has exited does not prevent signalling
its remaining group members.

This is process-group ownership, not general process-tree containment.
Descendants that call setsid or otherwise leave the owned group are outside the
boundary. No descendant scanning, parent-group signalling, or privileged sandbox
changes are used. If an escaped descendant retains inherited pipes, cleanup
allows one second for draining, then closes the local asyncio subprocess
transport and cancels the readers so cancellation cannot hang indefinitely.
The transport close uses asyncio's private transport because Process has no
public close method; supported interpreter versions should exercise this test.

Windows is explicitly unavailable and raises ValueError before launching a
process. A Windows job-object implementation would require separate work.

Successful completion still returns up to 200,000 bytes per output stream, with
`truncated` set when needed. This is a result-size cap, not a streaming memory
cap: communicate buffers output before truncation. This change does not add a
new memory limit or terminate children after successful normal completion.

## Verification

Install actual locked dependencies with `uv sync --extra dev --frozen`.
Run `uv run --frozen pytest -q tests/test_shell_process_groups.py tests/test_advanced.py`.
Tests use the real native ShellCommand, ShellArgs, and Python subprocesses.
The spawn-race test wraps native subprocess creation only to hold delivery of
its real Process handle at a deterministic cancellation point.

`PYTHONPATH=. .venv/bin/python scripts/reproduce_shell_cancel.py` reproduces the
original bug and can run at the base revision without substituting any imports.
At base 6b8c4442d4f25ba238e78ea9444b9fecdbccaeba it prints
`marker_exists=True; marker=survived`; with the fix it prints
`marker_exists=False; marker=None`. The demonstration cleans up only its own
recorded child PID.
