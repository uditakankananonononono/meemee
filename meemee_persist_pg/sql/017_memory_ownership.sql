-- Legacy rows stay quarantined to the local default identity, not all tenants.
ALTER TABLE meemee_memories ADD COLUMN owner_id text NOT NULL DEFAULT 'default';
CREATE INDEX meemee_memories_owner_recent ON meemee_memories(owner_id,id DESC);
