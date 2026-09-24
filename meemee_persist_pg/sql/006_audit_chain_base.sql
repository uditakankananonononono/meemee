-- Chain base for a pruned or imported audit chain: the (sequence, entry_hash) the first remaining
-- row links to. Mirrors the SQLite audit_chain_base table so a pruned SQLite chain stays verifiable
-- after cutover. Absent row = the chain starts at the zero hash.
CREATE TABLE meemee_audit_chain_base (
  singleton integer PRIMARY KEY CHECK (singleton = 1),
  sequence bigint NOT NULL,
  entry_hash char(64) NOT NULL
);
REVOKE UPDATE, DELETE, TRUNCATE ON meemee_audit_chain_base FROM PUBLIC;
