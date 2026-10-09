# Scoped peer inventory merge

Source: Udita's second-account peer repair candidate 3121ca6bd4db5073313718765e51b13551891c2a,
base 653bc158207d2e4ae4612bc3d3dc679d7d75ae61. Candidate commit retained in merge history.
Archive SHA256 c38badb214cf67279faa563d855f3b3338bdd8afec02c01027ebcd38f0530798;
all internal SHA256SUMS validated before integration. Independent gate SCOPED PASS,
15 tests, reported through owner main. This lane separately reproduced 15 passing
tests on Python 3.12.14. No full regression claim.

Verified scope: pure function over supplied exact lists of dicts; strict record fields,
exact integer caps/types, lowercase hash format, detached sorted immutable tuple and
byte/file totals. No filesystem access, content checksum verification, SQLite integrity,
backup restoration, model behavior or live data migration. Supplied hashes are claims,
not proof of bytes. Names described in code as safe components are only bounded labels
in this scope.

## Validation gap and mandatory future condition

Accepted names can include case-only variants, colon, newline, internal/trailing-stem
spaces, Windows-reserved stems, empty stem (.sqlite3) and non-ASCII. Exact duplicate
labels are refused, but case-insensitive collisions are not. Final suffix must remain
.sqlite3; trailing whitespace after that suffix is refused by the current check.

This is acceptable ONLY while names remain opaque labels in a supplied-dictionary
inventory. Before any later caller joins these names to real filesystem paths, tighten
and test cross-platform name validation and collision handling first. Do not wire this
helper into restore/path construction under the current verdict. No restoration
expansion was made in this merge.

Peer credit: candidate implementation and its tests were supplied by Udita's other
account. This lane performed source inspection, checksum/ancestry checks, local test
reproduction, merge and scope ledger. No new AGI or product-completion claim.
