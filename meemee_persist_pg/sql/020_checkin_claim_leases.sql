ALTER TABLE meemee_companion_checkins ADD COLUMN claim_token text;
ALTER TABLE meemee_companion_checkins ADD COLUMN claim_generation integer NOT NULL DEFAULT 0;
ALTER TABLE meemee_companion_checkins ADD COLUMN claimed_at timestamptz;
ALTER TABLE meemee_companion_checkins ADD COLUMN lease_until timestamptz;
ALTER TABLE meemee_companion_checkins ADD COLUMN delivery_state text NOT NULL DEFAULT 'unknown';
CREATE INDEX meemee_checkin_expired_claims ON meemee_companion_checkins(status,lease_until);
