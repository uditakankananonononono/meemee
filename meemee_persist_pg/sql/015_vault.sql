-- Encrypted secret vault shared by every host and CLI on this database. Same format as
-- vault.sqlite3: AES-256-GCM, a random 12-byte nonce per record, the secret name as associated data.
CREATE TABLE meemee_vault_secrets (
  name text PRIMARY KEY,
  nonce bytea NOT NULL,
  ciphertext bytea NOT NULL
);
