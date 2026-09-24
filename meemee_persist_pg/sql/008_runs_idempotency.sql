-- Completed run reports and idempotency records shared by every API host on this database.
-- Same semantics as runs.sqlite3 / idempotency.sqlite3.
CREATE TABLE meemee_runs (
  run_id text PRIMARY KEY CHECK (run_id <> ''),
  principal text NOT NULL CHECK (principal <> ''),
  goal text NOT NULL,
  final text NOT NULL,
  steps_used integer NOT NULL,
  tool_results jsonb NOT NULL,
  created_at timestamptz NOT NULL,
  approvals_required jsonb NOT NULL DEFAULT '[]'::jsonb
);
CREATE INDEX meemee_runs_principal_created ON meemee_runs(principal, created_at DESC, run_id DESC);

CREATE TABLE meemee_idempotency (
  principal text NOT NULL,
  route text NOT NULL,
  key text NOT NULL CHECK (key <> '' AND length(key) <= 200),
  request_hash char(64) NOT NULL,
  response jsonb NOT NULL,
  status integer NOT NULL,
  created_at timestamptz NOT NULL,
  expires_at timestamptz NOT NULL,
  PRIMARY KEY (principal, route, key)
);
CREATE INDEX meemee_idempotency_expires ON meemee_idempotency(expires_at);
