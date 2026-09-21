CREATE TABLE meemee_memories (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, run_id text NOT NULL, kind text NOT NULL,
 content text NOT NULL, metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
 search tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE INDEX meemee_memories_run_recent ON meemee_memories(run_id, id DESC);
CREATE INDEX meemee_memories_search ON meemee_memories USING gin(search);

CREATE TABLE meemee_plans (
 id uuid PRIMARY KEY, goal text NOT NULL, version integer NOT NULL CHECK(version > 0),
 document jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE TABLE meemee_plan_history (
 plan_id uuid NOT NULL REFERENCES meemee_plans(id) ON DELETE CASCADE, version integer NOT NULL,
 document jsonb NOT NULL, reason text NOT NULL CHECK(length(btrim(reason)) > 0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), PRIMARY KEY(plan_id,version));

CREATE TYPE meemee_job_status AS ENUM ('queued','running','done','failed','cancel_requested','cancelled');
CREATE TABLE meemee_jobs (
 id uuid PRIMARY KEY, goal text NOT NULL, run_at timestamptz NOT NULL, status meemee_job_status NOT NULL,
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts >= 0), max_attempts integer NOT NULL DEFAULT 3 CHECK(max_attempts > 0),
 lease_owner text, lease_token uuid, lease_expires_at timestamptz, result jsonb, error text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE INDEX meemee_jobs_due ON meemee_jobs(run_at,id) WHERE status='queued';
CREATE INDEX meemee_jobs_expired ON meemee_jobs(lease_expires_at) WHERE status='running';
CREATE TABLE meemee_job_events (
 sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, job_id uuid NOT NULL REFERENCES meemee_jobs(id) ON DELETE CASCADE,
 kind text NOT NULL, payload jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp());
CREATE INDEX meemee_job_events_job ON meemee_job_events(job_id,sequence);

CREATE TABLE meemee_api_tokens (
 id text PRIMARY KEY, name text NOT NULL CHECK(length(btrim(name)) > 0), digest bytea NOT NULL UNIQUE,
 scopes text[] NOT NULL CHECK(cardinality(scopes) > 0), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 last_used_at timestamptz, expires_at timestamptz, revoked_at timestamptz);
CREATE INDEX meemee_api_tokens_active_digest ON meemee_api_tokens(digest) WHERE revoked_at IS NULL;

CREATE TABLE meemee_audit_log (
 sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, occurred_at timestamptz NOT NULL,
 actor_id text NOT NULL, action text NOT NULL, resource text NOT NULL, outcome text NOT NULL,
 metadata jsonb NOT NULL, previous_hash char(64) NOT NULL, entry_hash char(64) NOT NULL UNIQUE);
REVOKE UPDATE, DELETE, TRUNCATE ON meemee_audit_log FROM PUBLIC;
