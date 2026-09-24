-- Persistent per-principal tool grants, shared by every API and worker process on this database.
-- Same semantics as the SQLite tool_approvals table: one row per (principal, tool), re-grant replaces it,
-- revoke stamps revoked_at, expiry is exclusive.
CREATE TABLE meemee_tool_approvals (
  principal text NOT NULL CHECK (principal <> ''),
  tool text NOT NULL CHECK (tool <> ''),
  granted_at timestamptz NOT NULL,
  expires_at timestamptz,
  revoked_at timestamptz,
  granted_by text NOT NULL CHECK (granted_by <> ''),
  argument_constraints jsonb CHECK (argument_constraints IS NULL OR jsonb_typeof(argument_constraints) = 'object'),
  PRIMARY KEY (principal, tool)
);
