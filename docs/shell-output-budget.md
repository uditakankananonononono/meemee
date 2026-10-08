# Shell output budget

The real shell tool accepts `max_output_bytes`, default 200000, from 1 to
1000000, independently for stdout and stderr. This budget limits retained raw
bytes, not runtime or total emitted output. Existing timeout remains separate.
No new sandbox, containment or account-signup capability is implied.

Results include stdout_bytes/stderr_bytes (observed raw totals),
stdout_retained_bytes/stderr_retained_bytes, and
stdout_truncated_bytes/stderr_truncated_bytes. Counts describe raw bytes,
not characters or UTF-8 encoded output strings. The last retained partial
UTF-8 character can decode as a replacement character, so encoding the
returned text may exceed the raw retained-byte count. The existing truncated
flag remains compatible. A timeout raises the existing error, not partial
counts. Process-group cleanup is POSIX; Windows cleanup limits are unchanged.
