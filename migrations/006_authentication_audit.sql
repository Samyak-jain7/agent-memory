CREATE TABLE authentication_events (
 id uuid PRIMARY KEY,
 request_id text NOT NULL,
 action text NOT NULL CHECK(action='authentication.denied'),
 created_at timestamptz NOT NULL DEFAULT now()
);
GRANT INSERT ON authentication_events TO memory_app;
