-- Daily job quotas and plan assignments shared by every API host on this database.
-- Same semantics as quotas.sqlite3 / entitlements.sqlite3: per-principal limit override,
-- per-(principal, UTC day) usage counter, one plan row per principal (absent = default plan).
CREATE TABLE meemee_quota_limits (
  principal text PRIMARY KEY CHECK (principal <> ''),
  daily_jobs integer NOT NULL CHECK (daily_jobs > 0)
);
CREATE TABLE meemee_quota_usage (
  principal text NOT NULL CHECK (principal <> ''),
  day date NOT NULL,
  jobs integer NOT NULL CHECK (jobs >= 0),
  PRIMARY KEY (principal, day)
);
CREATE TABLE meemee_principal_plans (
  principal text PRIMARY KEY CHECK (principal <> ''),
  plan text NOT NULL,
  updated_at timestamptz NOT NULL
);
