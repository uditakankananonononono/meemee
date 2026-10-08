# Bounded workspace text reading

workspace.read_file uses max_bytes (strict integer 1..1000000, default 1000000).
It reads at most max_bytes+1 raw bytes and refuses oversized files, rather than
returning silently truncated text. Successful reads include raw bytes count.
UTF-8 decoding remains strict; binary data is refused. Normal small-file content
and path fields remain. The existing runtime registration uses this schema.

On POSIX, O_NONBLOCK permits refusing a FIFO without waiting for a writer.
Regular-file validation happens on the opened handle. Workspace path resolution
is unchanged: this does not close adversarial directory/symlink replacement
races, and is not a filesystem sandbox or Windows containment proof.
