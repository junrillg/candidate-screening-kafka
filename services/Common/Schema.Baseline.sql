CREATE TABLE flow_outbox (
  id uuid PRIMARY KEY, topic text NOT NULL, payload jsonb NOT NULL, expires_at timestamptz NOT NULL,
  due_at timestamptz NOT NULL DEFAULT now(), lease_until timestamptz, lease_id uuid,
  attempts integer NOT NULL DEFAULT 0, sent_at timestamptz, created_at timestamptz NOT NULL DEFAULT now());
CREATE INDEX flow_outbox_due ON flow_outbox(due_at) WHERE sent_at IS NULL;
CREATE TABLE flow_inbox (id uuid PRIMARY KEY, received_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE flow_poison (fingerprint bytea PRIMARY KEY, received_at timestamptz NOT NULL DEFAULT now());
