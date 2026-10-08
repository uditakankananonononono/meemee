# Binary-safe file paging

workspace.read_chunk reads one regular workspace file by raw-byte offset.
max_bytes1..262144(default65536), offset strict nonnegative integer. Result has
base64 raw bytes, bytes count, offset, next_offset and eof from one-byte lookahead.
Beyond EOF returns empty bytes/eof. Large files can be read without full loading.
No UTF-8 character-boundary claim; clients must decode/assemble bytes themselves.
Real async/legacy runtime registration; no provider call.

Bounded read, not snapshot/auth boundary. Concurrent edits can mix pages. Existing
workspace directory/symlink races remain. Base64 expands output size. POSIX
nonblocking open refuses FIFO after fd check; Windows behavior not proved.
