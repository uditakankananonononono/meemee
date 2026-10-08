# Git inspection disables configured fsmonitor

GitInspect commands now pass `-c core.fsmonitor=false`. Actual Git status and
worktree diff can invoke a configured fsmonitor executable even though these
are READ operations. Regression tests use a real repository and an executable
Python helper that writes a marker. Both tests fail on the previous commit;
status and diff still return the changed tracked file after the repair, without
a marker. The override also applies to log for consistency.

This disables this one native execution mechanism, including built-in fsmonitor
use in these commands. It is not a hostile-repository sandbox. Other Git config,
executables, environment, hooks and filters in write operations are not contained.
The earlier no-textconv repair remains. No write-operation behavior changes.
