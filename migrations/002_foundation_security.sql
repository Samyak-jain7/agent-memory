-- Forward migration: default-deny subject grants and immutable evidence.
CREATE TABLE actor_subject_grants (
    tenant_id uuid NOT NULL REFERENCES tenants(id),
    actor_id uuid NOT NULL,
    subject_id uuid NOT NULL,
    PRIMARY KEY (tenant_id, actor_id, subject_id),
    FOREIGN KEY (tenant_id, actor_id) REFERENCES actors(tenant_id, id),
    FOREIGN KEY (tenant_id, subject_id) REFERENCES subjects(tenant_id, id)
);
ALTER TABLE actor_subject_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE actor_subject_grants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON actor_subject_grants
USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
GRANT SELECT ON actor_subject_grants TO memory_app;

ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON tenants
USING (id=nullif(current_setting('app.tenant_id',true),'')::uuid)
WITH CHECK (id=nullif(current_setting('app.tenant_id',true),'')::uuid);
REVOKE INSERT, UPDATE, DELETE ON tenants, actors, subjects FROM memory_app;
REVOKE UPDATE, DELETE ON episodes, audit_events, memory_sources, memory_versions FROM memory_app;
-- A correction may close the preceding interval; payload and evidence stay immutable.
GRANT UPDATE (valid_to) ON memory_versions TO memory_app;

CREATE FUNCTION enforce_source_subject() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM memory_versions v
        JOIN memories m ON m.id=v.memory_id AND m.tenant_id=v.tenant_id
        JOIN episodes e ON e.id=NEW.episode_id AND e.tenant_id=v.tenant_id
        WHERE v.id=NEW.memory_version_id AND v.tenant_id=NEW.tenant_id
          AND e.subject_id=m.subject_id
    ) THEN
        RAISE EXCEPTION 'source evidence must belong to the memory subject';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER source_subject BEFORE INSERT OR UPDATE ON memory_sources
FOR EACH ROW EXECUTE FUNCTION enforce_source_subject();

CREATE FUNCTION enforce_remaining_provenance() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM memories m
        JOIN memory_versions v ON v.tenant_id=m.tenant_id AND v.memory_id=m.id
        WHERE m.tenant_id=OLD.tenant_id AND v.id=OLD.memory_version_id
          AND m.lifecycle_state='active'
          AND NOT EXISTS (SELECT 1 FROM memory_sources s
              WHERE s.tenant_id=m.tenant_id AND s.memory_version_id=v.id)
    ) THEN
        RAISE EXCEPTION 'active memory requires source evidence';
    END IF;
    RETURN OLD;
END $$;
CREATE CONSTRAINT TRIGGER remaining_provenance AFTER DELETE OR UPDATE ON memory_sources
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION enforce_remaining_provenance();

CREATE FUNCTION close_version_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.valid_to IS NOT NULL OR NEW.valid_to IS NULL OR NEW.valid_to<=OLD.valid_from THEN
        RAISE EXCEPTION 'version interval may only be closed once';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER version_interval BEFORE UPDATE OF valid_to ON memory_versions
FOR EACH ROW EXECUTE FUNCTION close_version_once();

CREATE FUNCTION enforce_version_provenance() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM memories m WHERE m.id=NEW.memory_id
          AND m.tenant_id=NEW.tenant_id AND m.lifecycle_state='active')
       AND NOT EXISTS (SELECT 1 FROM memory_sources s WHERE s.memory_version_id=NEW.id
          AND s.tenant_id=NEW.tenant_id) THEN
        RAISE EXCEPTION 'active memory version requires source evidence';
    END IF;
    RETURN NEW;
END $$;
CREATE CONSTRAINT TRIGGER version_provenance AFTER INSERT OR UPDATE ON memory_versions
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION enforce_version_provenance();
ALTER TABLE memory_versions ADD CONSTRAINT version_identity UNIQUE(tenant_id,memory_id,id);
ALTER TABLE memories ADD CONSTRAINT current_version_memory_fk
FOREIGN KEY(tenant_id,id,current_version_id) REFERENCES memory_versions(tenant_id,memory_id,id)
DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE memory_versions ADD CONSTRAINT supersedes_same_memory_fk
FOREIGN KEY(tenant_id,memory_id,supersedes_version_id) REFERENCES memory_versions(tenant_id,memory_id,id)
DEFERRABLE INITIALLY DEFERRED;

-- Memory identity, subject and kind are stable; controls may only move lifecycle/current version.
REVOKE UPDATE ON memories FROM memory_app;
GRANT UPDATE (current_version_id, lifecycle_state, suppressed_at) ON memories TO memory_app;
