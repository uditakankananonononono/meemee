-- Supplied event identities, retained indefinitely. No external producer authenticity.
CREATE TABLE meemee_monitor_source_events (
 owner_id text NOT NULL, source_id text NOT NULL, event_id text NOT NULL,
 payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
 created_at text NOT NULL, PRIMARY KEY(owner_id,source_id,event_id)
);
