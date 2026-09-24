-- Account-deletion ledger shared by every API host, worker and CLI on this database. Same semantics
-- as account-deletions.sqlite3; time and JSON columns stay text, as in SQLite.
CREATE TABLE meemee_account_deletions (
  id text PRIMARY KEY,
  principal text NOT NULL,
  requested_by text NOT NULL,
  status text NOT NULL CHECK (status IN ('in_progress','completed')),
  started_at text NOT NULL,
  completed_at text
);
CREATE INDEX meemee_account_deletions_principal ON meemee_account_deletions(principal, status);
-- One open deletion per principal, even when two hosts start one at the same moment.
CREATE UNIQUE INDEX meemee_account_deletions_open ON meemee_account_deletions(principal) WHERE status = 'in_progress';

CREATE TABLE meemee_account_deletion_steps (
  deletion_id text NOT NULL,
  step text NOT NULL,
  counts text NOT NULL,
  finished_at text NOT NULL,
  PRIMARY KEY (deletion_id, step)
);
