# Git inspection does not run textconv

GitInspect diff now uses --no-textconv alongside --no-ext-diff. A configured
textconv helper can otherwise execute during this READ tool. Actual local Git
repository test has a helper that writes a marker; base invokes it, repaired
inspection returns raw old/new diff without marker execution.

Narrow textconv suppression only, not arbitrary Git-config/executable isolation.
Git hooks/filters in other actions unchanged; no sandbox, device permission or
safe hostile repository claim. Existing timeout/capture limits remain.
