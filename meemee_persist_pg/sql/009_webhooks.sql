-- Webhook subscriptions and the delivery outbox shared by every API host and dispatcher on this database.
-- Same semantics as webhooks.sqlite3 (no foreign keys, so SQLite history with orphaned attempt rows copies as-is). Payloads stay text: signatures are computed over the exact bytes.
CREATE TABLE meemee_webhook_subscriptions (
  id text PRIMARY KEY,
  principal text NOT NULL CHECK (principal <> ''),
  url text NOT NULL,
  secret text NOT NULL,
  events text NOT NULL,
  fields text,
  headers text,
  active boolean NOT NULL,
  created_at timestamptz NOT NULL
);
CREATE INDEX meemee_webhook_subscriptions_principal ON meemee_webhook_subscriptions(principal, created_at DESC, id DESC);

CREATE TABLE meemee_webhook_deliveries (
  id text PRIMARY KEY,
  subscription_id text NOT NULL,
  event_id text NOT NULL,
  event_type text NOT NULL,
  payload text NOT NULL,
  payload_sha256 text,
  status text NOT NULL CHECK (status IN ('queued','sending','delivered','failed')),
  attempts integer NOT NULL DEFAULT 0,
  next_attempt_at double precision NOT NULL,
  response_status integer,
  last_error text,
  sending_started_at double precision,
  created_at timestamptz NOT NULL,
  UNIQUE (subscription_id, event_id)
);
CREATE INDEX meemee_webhook_due ON meemee_webhook_deliveries(status, next_attempt_at);
CREATE INDEX meemee_webhook_deliveries_created ON meemee_webhook_deliveries(created_at DESC, id DESC);

CREATE TABLE meemee_webhook_attempts (
  id bigserial PRIMARY KEY,
  delivery_id text NOT NULL,
  attempt integer NOT NULL,
  started_at timestamptz NOT NULL,
  finished_at timestamptz,
  outcome text,
  response_status integer,
  error text
);
CREATE INDEX meemee_webhook_attempt_delivery ON meemee_webhook_attempts(delivery_id, id);
