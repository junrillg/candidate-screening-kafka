CREATE TABLE IF NOT EXISTS screening_jobs (
  id uuid PRIMARY KEY, owner_id uuid NOT NULL, expires_at timestamptz NOT NULL,
  state text NOT NULL CHECK(state IN ('Queued','Screening','Completed','Failed','Deleted')),
  attempts integer NOT NULL DEFAULT 0, due_at timestamptz NOT NULL DEFAULT now(),
  lease_id uuid, lease_until timestamptz, result jsonb, deleted_at timestamptz);
CREATE INDEX IF NOT EXISTS screening_jobs_due ON screening_jobs(due_at) WHERE state IN ('Queued','Screening');
CREATE INDEX IF NOT EXISTS screening_jobs_expiry ON screening_jobs(expires_at) WHERE state <> 'Deleted';
