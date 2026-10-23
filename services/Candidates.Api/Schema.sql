CREATE TABLE IF NOT EXISTS admission_windows (
  kind text NOT NULL CHECK(kind IN ('session','submission')), window_at timestamptz NOT NULL,
  used integer NOT NULL CHECK(used>0), PRIMARY KEY(kind,window_at));
CREATE TABLE IF NOT EXISTS owner_sessions (
  id uuid PRIMARY KEY, expires_at timestamptz NOT NULL, revoked boolean NOT NULL DEFAULT false);
CREATE TABLE IF NOT EXISTS candidates (
  id uuid PRIMARY KEY, owner_id uuid NOT NULL REFERENCES owner_sessions(id), request_key uuid NOT NULL,
  payload_hash bytea NOT NULL, display_name text, profile text, requirements jsonb,
  status text NOT NULL CHECK(status IN ('Queued','Screening','Completed','Failed','Deleting')),
  created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL,
  error_code text, result jsonb, deleted_at timestamptz, cleanup_confirmed boolean NOT NULL DEFAULT false,
  UNIQUE(owner_id,request_key));
CREATE INDEX IF NOT EXISTS candidates_owner_page ON candidates(owner_id,created_at DESC,id DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS candidates_pending ON candidates(status,expires_at)
  WHERE deleted_at IS NULL AND status IN ('Queued','Screening');
CREATE INDEX IF NOT EXISTS candidates_expiry ON candidates(expires_at) WHERE deleted_at IS NULL;
CREATE TABLE IF NOT EXISTS notifications (
  id uuid PRIMARY KEY, candidate_id uuid NOT NULL UNIQUE REFERENCES candidates(id) ON DELETE CASCADE,
  owner_id uuid NOT NULL, status text NOT NULL CHECK(status IN ('Completed','Failed')),
  read boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX IF NOT EXISTS notifications_owner_page ON notifications(owner_id,created_at DESC,id DESC);
