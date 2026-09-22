ALTER TABLE meemee_jobs ADD COLUMN principal text;
CREATE INDEX meemee_jobs_principal_updated ON meemee_jobs(principal,updated_at DESC,id DESC);
