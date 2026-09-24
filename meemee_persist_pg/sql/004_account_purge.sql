ALTER TABLE meemee_jobs ADD COLUMN purge_pending boolean NOT NULL DEFAULT false;
CREATE INDEX meemee_jobs_purge_pending ON meemee_jobs(id) WHERE purge_pending;
