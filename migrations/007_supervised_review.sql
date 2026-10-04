ALTER TABLE actor_subject_grants ADD COLUMN can_review_memory boolean NOT NULL DEFAULT false;
ALTER TABLE formation_jobs DROP CONSTRAINT formation_jobs_state_check;
ALTER TABLE formation_jobs ADD CONSTRAINT formation_jobs_state_check CHECK(state IN ('pending','leased','succeeded','rejected','retryable_failure','dead_letter','awaiting_review'));
CREATE TABLE memory_suggestions (
 id uuid PRIMARY KEY,
 tenant_id uuid NOT NULL,
 subject_id uuid NOT NULL,
 episode_id uuid NOT NULL,
 formation_job_id uuid NOT NULL,
 candidate_index integer NOT NULL,
 kind text NOT NULL,
 content text,
 confidence double precision NOT NULL CHECK(confidence BETWEEN 0 AND 1),
 importance double precision NOT NULL CHECK(importance BETWEEN 0 AND 1),
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','approved','rejected','expired','invalidated')),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '7 days',
 decided_at timestamptz,
 decided_by uuid,
 memory_id uuid,
 UNIQUE(tenant_id,id),
 UNIQUE(tenant_id,formation_job_id,candidate_index),
 FOREIGN KEY(tenant_id,subject_id) REFERENCES subjects(tenant_id,id),
 FOREIGN KEY(tenant_id,episode_id) REFERENCES episodes(tenant_id,id),
 FOREIGN KEY(tenant_id,formation_job_id) REFERENCES formation_jobs(tenant_id,id),
 FOREIGN KEY(tenant_id,decided_by) REFERENCES actors(tenant_id,id),
 FOREIGN KEY(tenant_id,memory_id) REFERENCES memories(tenant_id,id),
 CHECK((state='pending' AND content IS NOT NULL) OR (state<>'pending' AND content IS NULL))
);
CREATE INDEX suggestions_pending ON memory_suggestions(tenant_id,subject_id,created_at,id) WHERE state='pending';
ALTER TABLE memory_suggestions ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_suggestions FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON memory_suggestions USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid) WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
GRANT SELECT,INSERT,UPDATE ON memory_suggestions TO memory_app;
CREATE FUNCTION validate_suggestion_source() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM episodes e JOIN formation_jobs j ON j.episode_id=e.id AND j.tenant_id=e.tenant_id WHERE e.id=NEW.episode_id AND e.tenant_id=NEW.tenant_id AND e.subject_id=NEW.subject_id AND e.erased_at IS NULL AND j.id=NEW.formation_job_id) THEN
 RAISE EXCEPTION 'suggestion requires matching unerased source and formation job'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER suggestion_source BEFORE INSERT ON memory_suggestions FOR EACH ROW EXECUTE FUNCTION validate_suggestion_source();
CREATE FUNCTION invalidate_erased_suggestions() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF NEW.erased_at IS NOT NULL THEN
 UPDATE public.memory_suggestions SET state='invalidated',content=NULL,decided_at=clock_timestamp() WHERE tenant_id=NEW.tenant_id AND episode_id=NEW.id AND state='pending';
 UPDATE public.formation_jobs j SET state=CASE WHEN EXISTS(SELECT 1 FROM public.memory_suggestions s WHERE s.formation_job_id=j.id AND s.state='approved') THEN 'succeeded' ELSE 'rejected' END WHERE j.tenant_id=NEW.tenant_id AND j.episode_id=NEW.id AND j.state='awaiting_review' AND NOT EXISTS(SELECT 1 FROM public.memory_suggestions s WHERE s.formation_job_id=j.id AND s.state='pending');
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER erased_suggestions AFTER UPDATE OF erased_at ON episodes FOR EACH ROW EXECUTE FUNCTION invalidate_erased_suggestions();
-- A forgotten source cannot seed new suggestions, even before physical erasure.
CREATE TABLE memory_review_blocks (
 tenant_id uuid NOT NULL,
 episode_id uuid NOT NULL,
 PRIMARY KEY(tenant_id,episode_id),
 FOREIGN KEY(tenant_id,episode_id) REFERENCES episodes(tenant_id,id)
);
ALTER TABLE memory_review_blocks ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_review_blocks FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON memory_review_blocks USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid) WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
GRANT SELECT,INSERT ON memory_review_blocks TO memory_app;
-- Ordinary runtime cannot mutate immutable episodes; this function only locks them.
CREATE FUNCTION lock_review_episode(p_episode uuid) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE available boolean;
BEGIN
 SELECT erased_at IS NULL INTO available FROM public.episodes WHERE id=p_episode AND tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid FOR UPDATE;
 RETURN coalesce(available,false);
END $$;
REVOKE ALL ON FUNCTION lock_review_episode(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION lock_review_episode(uuid) TO memory_app;
CREATE FUNCTION can_review_subject(p_actor uuid,p_subject uuid) RETURNS boolean LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE allowed boolean;
BEGIN
 SELECT g.can_review_memory AND g.can_inspect_sources AND s.memory_consent INTO allowed
 FROM public.actor_subject_grants g JOIN public.actors a ON a.tenant_id=g.tenant_id AND a.id=g.actor_id
 JOIN public.subjects s ON s.tenant_id=g.tenant_id AND s.id=g.subject_id
 WHERE g.tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid AND g.actor_id=p_actor AND g.subject_id=p_subject AND a.active AND s.active
 FOR SHARE OF g,a,s;
 RETURN coalesce(allowed,false);
END $$;
REVOKE ALL ON FUNCTION can_review_subject(uuid,uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION can_review_subject(uuid,uuid) TO memory_app;
