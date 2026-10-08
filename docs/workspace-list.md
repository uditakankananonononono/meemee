# Workspace directory discovery

workspace.list_files returns nonrecursive names/kinds from one directory.
Default hides dot-prefixed names; include_hidden enables them. Results sort by
name, then limit1..1000 (default100). truncated reports additional visible
entries. scan_limit1..10000(default2000) counts ALL entries including hidden;
exceeding it refuses, never silently returns a partial unordered scan.
No file contents or symlink target paths returned; entry symlinks not followed.
Both async and legacy runtime factories register the tool. Real async factory
and registry tested without any provider call.

Entry count bound is not a time/byte bound or authorization. Existing workspace
resolve/symlink-directory race policy remains; concurrent listing is not a
consistent snapshot. Nonrecursive only. Names themselves can be private.
