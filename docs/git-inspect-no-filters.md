# Git inspection disables discovered conversion filters

Inspection first reads effective Git config key names (not values), with a 1MB
capture cap. Truncated discovery refuses inspection. Discovered filter drivers
have clean, smudge and process cleared and required=false for this invocation.
The existing fsmonitor, textconv and external-diff suppressions remain.

Real repositories demonstrate clean helpers execute in status and worktree diff
on the base. Tests cover ordinary and dotted driver names, plus a process helper
that writes a marker then exits (not a full process-protocol implementation).
Repaired status/diff retain tracked changes and raw old/new content without
executing these helpers. A synthetic truncated-discovery test verifies refusal.

Raw content inspection intentionally differs from filter-normalized content;
files normally normalized by filters may appear changed. This is not a Git
config sandbox. Config changes between discovery and inspection can introduce
undiscovered drivers; parent/repository replacement and hostile environments or
Git executable are not contained. Config discovery and inspection each receive
the specified timeout, so total elapsed time may approach twice that timeout.
The 1MB config cap is capture-only, not an execution/output-generation budget.
Smudge suppression is defensive, not a demonstrated READ execution path.
Write actions and their filter/hook behavior are unchanged. No full M22 closure.
