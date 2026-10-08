# Advisory stale-edit precondition

workspace.write_file accepts optional expected_sha256, a lowercase64-digit
SHA256 of existing raw file bytes. A mismatched/missing file refuses before
creating directories or publishing. Successful writes return sha256 alongside
existing path/bytes. Without a precondition, prior overwrite behavior remains.

This is an advisory single-call stale-edit check, NOT atomic compare-and-swap.
A concurrent writer can change the destination after the check and before
publication. Existing symlink/directory race limits and atomic-replace scope
remain. No cross-process lock, power-loss durability or automatic retry.
Hashing uses64KB chunks; time/total input are not capped for existing files.
