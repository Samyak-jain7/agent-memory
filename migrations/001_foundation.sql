CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'memory_app') THEN
        CREATE ROLE memory_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
    END IF;
END $$;

GRANT memory_app TO CURRENT_USER WITH INHERIT FALSE, SET TRUE;

CREATE TABLE tenants (
    id uuid PRIMARY KEY,
    name text NOT NULL
);

CREATE TABLE actors (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    active boolean NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE subjects (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    active boolean NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE sessions (
    id uuid NOT NULL,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    subject_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, subject_id) REFERENCES subjects(tenant_id, id)
);

CREATE TABLE episodes (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    subject_id uuid NOT NULL,
    actor_id uuid NOT NULL,
    session_id uuid NOT NULL,
    content jsonb NOT NULL,
    source_type text NOT NULL,
    idempotency_key text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, idempotency_key),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, subject_id) REFERENCES subjects(tenant_id, id),
    FOREIGN KEY (tenant_id, actor_id) REFERENCES actors(tenant_id, id),
    FOREIGN KEY (tenant_id, session_id) REFERENCES sessions(tenant_id, id)
);

CREATE TABLE formation_jobs (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    episode_id uuid NOT NULL UNIQUE,
    state text NOT NULL CHECK (state IN ('pending','leased','succeeded','rejected','retryable_failure','dead_letter')),
    attempts integer NOT NULL DEFAULT 0,
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, episode_id) REFERENCES episodes(tenant_id, id)
);

CREATE TABLE memories (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    subject_id uuid NOT NULL,
    kind text NOT NULL,
    lifecycle_state text NOT NULL CHECK (lifecycle_state IN ('active','suppressed')),
    current_version_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    suppressed_at timestamptz,
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, subject_id) REFERENCES subjects(tenant_id, id)
);

CREATE TABLE memory_versions (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    memory_id uuid NOT NULL,
    content text NOT NULL,
    origin text NOT NULL CHECK (origin IN ('explicit','observed','derived')),
    confidence double precision NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    importance double precision NOT NULL CHECK (importance BETWEEN 0 AND 1),
    valid_from timestamptz NOT NULL,
    valid_to timestamptz,
    supersedes_version_id uuid,
    actor_id uuid NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (tenant_id, id),
    FOREIGN KEY (tenant_id, memory_id) REFERENCES memories(tenant_id, id),
    FOREIGN KEY (tenant_id, actor_id) REFERENCES actors(tenant_id, id),
    FOREIGN KEY (tenant_id, supersedes_version_id) REFERENCES memory_versions(tenant_id, id),
    CHECK (valid_to IS NULL OR valid_to > valid_from)
);

ALTER TABLE memories ADD CONSTRAINT current_version_fk
    FOREIGN KEY (tenant_id, current_version_id) REFERENCES memory_versions(tenant_id, id)
    DEFERRABLE INITIALLY DEFERRED;

CREATE TABLE memory_sources (
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    memory_version_id uuid NOT NULL,
    episode_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (memory_version_id, episode_id),
    FOREIGN KEY (tenant_id, memory_version_id) REFERENCES memory_versions(tenant_id, id),
    FOREIGN KEY (tenant_id, episode_id) REFERENCES episodes(tenant_id, id)
);

CREATE TABLE audit_events (
    id uuid PRIMARY KEY,
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    actor_id uuid NOT NULL,
    subject_id uuid,
    action text NOT NULL,
    resource_type text NOT NULL,
    resource_id uuid,
    request_id text NOT NULL,
    outcome text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, actor_id) REFERENCES actors(tenant_id, id)
);

CREATE FUNCTION enforce_active_memory_provenance() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM memories m
        WHERE m.id = NEW.id AND m.lifecycle_state <> 'active'
    ) AND NOT EXISTS (
            SELECT 1 FROM memory_versions v
            JOIN memory_sources s ON s.memory_version_id = v.id AND s.tenant_id = v.tenant_id
            JOIN memories m ON m.current_version_id = v.id AND m.tenant_id = v.tenant_id
            WHERE m.id = NEW.id AND v.memory_id = NEW.id AND v.tenant_id = NEW.tenant_id
              AND v.origin IS NOT NULL AND v.confidence IS NOT NULL AND v.actor_id IS NOT NULL
              AND v.created_at IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'active memory requires a complete, source-linked version';
    END IF;
    RETURN NEW;
END $$;

GRANT USAGE ON SCHEMA public TO memory_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO memory_app;

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['actors','subjects','sessions','episodes','formation_jobs','memories','memory_versions','memory_sources','audit_events']
    LOOP
        EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', table_name);
    END LOOP;
END $$;

CREATE CONSTRAINT TRIGGER active_memory_provenance
AFTER INSERT OR UPDATE ON memories DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION enforce_active_memory_provenance();

DO $$
DECLARE table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['actors','subjects','sessions','episodes','formation_jobs','memories','memory_versions','memory_sources','audit_events']
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', table_name);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON %I USING (tenant_id = nullif(current_setting(''app.tenant_id'', true), '''')::uuid) WITH CHECK (tenant_id = nullif(current_setting(''app.tenant_id'', true), '''')::uuid)',
            table_name
        );
    END LOOP;
END $$;
