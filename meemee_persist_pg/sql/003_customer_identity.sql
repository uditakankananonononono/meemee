ALTER TABLE meemee_api_tokens ADD COLUMN owner_id text;
ALTER TABLE meemee_api_tokens ADD COLUMN token_kind text NOT NULL DEFAULT 'api';
CREATE INDEX meemee_api_tokens_owner ON meemee_api_tokens(owner_id, created_at DESC);

CREATE TABLE meemee_accounts (
 id text PRIMARY KEY, email text NOT NULL UNIQUE, display_name text NOT NULL,
 password_hash bytea NOT NULL, password_salt bytea NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), disabled_at timestamptz,
 failed_logins integer NOT NULL DEFAULT 0, locked_until timestamptz);

CREATE TABLE meemee_email_verifications (
 account_id text PRIMARY KEY REFERENCES meemee_accounts(id) ON DELETE CASCADE,
 token_digest bytea NOT NULL, expires_at timestamptz NOT NULL,
 verified_at timestamptz, sent_at timestamptz NOT NULL DEFAULT clock_timestamp());

CREATE TABLE meemee_password_resets (
 account_id text PRIMARY KEY REFERENCES meemee_accounts(id) ON DELETE CASCADE,
 token_digest bytea NOT NULL, expires_at timestamptz NOT NULL,
 used_at timestamptz, sent_at timestamptz NOT NULL DEFAULT clock_timestamp());
