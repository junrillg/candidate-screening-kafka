CREATE TABLE flow_handler_failures (
  id uuid PRIMARY KEY, topic text NOT NULL, payload jsonb NOT NULL, attempts integer NOT NULL DEFAULT 0,
  exhausted_at timestamptz, received_at timestamptz NOT NULL DEFAULT now());
