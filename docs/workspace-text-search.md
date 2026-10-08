# Literal single-file workspace search

workspace.search_text requires path/query. Case-sensitive literal matching,
not regex. One bounded UTF-8 file, max_bytes1..1MB(default1MB). Per-line matches,
line numbers1-based; max_matches1..1000(default100), previews1..2000characters
(default500). matched_lines counts matching lines, not occurrences. truncated
reports extra matched lines; preview_truncated reports clipped returned lines.
Query anywhere in a line can match even if outside its first preview characters.
Both real runtime factories register the tool; async construction tested.

No recursive directory search, binary decoding or provider call. Existing
read_file regular-file/workspace policy reused. Directory races unchanged.
File/read bound is not authorization or constant-memory proof; decoded text
and splitlines allocate within this bounded input. Unicode splitlines semantics.
Previews may contain private content; use appropriate product disclosure policy.
