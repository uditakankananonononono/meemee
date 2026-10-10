-- Apply with old companion workers stopped. No existing external destination
-- is trusted by migration. Challenge secrets are hashes; no nonce persistence.
CREATE TABLE meemee_companion_destination_challenges (
 id text PRIMARY KEY, owner_id text NOT NULL, destination text NOT NULL,
 nonce_sha256 text NOT NULL, created_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL, consumed_at timestamptz
);
CREATE INDEX meemee_companion_destination_challenges_owner
 ON meemee_companion_destination_challenges(owner_id,expires_at);
CREATE TABLE meemee_companion_destination_grants (
 id text PRIMARY KEY, owner_id text NOT NULL, channel text NOT NULL CHECK(channel='webhook'),
 destination text NOT NULL, issued_at timestamptz NOT NULL,
 expires_at timestamptz NOT NULL, revoked_at timestamptz
);
CREATE INDEX meemee_companion_destination_grants_owner
 ON meemee_companion_destination_grants(owner_id,channel,destination);
