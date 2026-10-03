ALTER TABLE episodes ADD COLUMN erased_at timestamptz;
ALTER TABLE actor_subject_grants ADD COLUMN can_inspect_sources boolean NOT NULL DEFAULT false;
CREATE TABLE erasure_jobs (
 id uuid PRIMARY KEY,
 tenant_id uuid NOT NULL,
 memory_id uuid NOT NULL,
 actor_id uuid NOT NULL,
 request_id text NOT NULL,
 state text NOT NULL CHECK(state IN ('pending','completed','blocked')),
 available_at timestamptz NOT NULL,
 completed_at timestamptz,
 retained_shared_episodes integer NOT NULL DEFAULT 0,
 error_code text,
 UNIQUE(tenant_id,memory_id),
 FOREIGN KEY(tenant_id,memory_id) REFERENCES memories(tenant_id,id),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES actors(tenant_id,id)
);
ALTER TABLE erasure_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE erasure_jobs FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON erasure_jobs USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
GRANT SELECT,INSERT,UPDATE ON erasure_jobs TO memory_app;

CREATE OR REPLACE FUNCTION enforce_source_subject() RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE linked uuid;
BEGIN
 SELECT e.id INTO linked FROM public.memory_versions v
 JOIN public.memories m ON m.id=v.memory_id AND m.tenant_id=v.tenant_id
 JOIN public.episodes e ON e.id=NEW.episode_id AND e.tenant_id=v.tenant_id
 WHERE v.id=NEW.memory_version_id AND v.tenant_id=NEW.tenant_id
 AND e.subject_id=m.subject_id AND e.erased_at IS NULL FOR SHARE OF e;
 IF linked IS NULL THEN RAISE EXCEPTION 'source evidence must belong to the memory subject and not be erased'; END IF;
 RETURN NEW;
END $$;

-- Narrow exceptional boundary: ordinary application permissions remain immutable.
CREATE FUNCTION erase_suppressed_memory(p_tenant uuid,p_memory uuid,p_actor uuid,p_request text)
RETURNS integer LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
DECLARE m public.memories%ROWTYPE; job public.erasure_jobs%ROWTYPE; episode uuid; linked uuid[]; retained integer:=0;
BEGIN
 IF nullif(current_setting('app.tenant_id',true),'')::uuid IS DISTINCT FROM p_tenant THEN
   RAISE EXCEPTION 'tenant scope required'; END IF;
 SELECT * INTO m FROM public.memories WHERE tenant_id=p_tenant AND id=p_memory FOR UPDATE;
 IF NOT FOUND OR m.lifecycle_state<>'suppressed' THEN RAISE EXCEPTION 'suppressed memory required'; END IF;
 SELECT * INTO job FROM public.erasure_jobs WHERE tenant_id=p_tenant AND memory_id=p_memory FOR UPDATE;
 IF NOT FOUND OR job.state<>'pending' OR job.available_at>now() THEN RAISE EXCEPTION 'pending due erasure required'; END IF;
 IF job.actor_id IS DISTINCT FROM p_actor OR job.request_id IS DISTINCT FROM p_request THEN RAISE EXCEPTION 'job identity required'; END IF;
 IF NOT EXISTS(SELECT 1 FROM public.actor_subject_grants g JOIN public.actors a ON a.tenant_id=g.tenant_id AND a.id=g.actor_id
   JOIN public.subjects s ON s.tenant_id=g.tenant_id AND s.id=g.subject_id
   WHERE g.tenant_id=p_tenant AND g.actor_id=p_actor AND g.subject_id=m.subject_id AND a.active AND s.active) THEN
   UPDATE public.erasure_jobs SET state='blocked',error_code='authorization_revoked' WHERE tenant_id=p_tenant AND memory_id=p_memory;
   INSERT INTO public.audit_events(id,tenant_id,actor_id,subject_id,action,resource_type,resource_id,request_id,outcome)
   VALUES(gen_random_uuid(),p_tenant,p_actor,m.subject_id,'memory.erase','memory',p_memory,p_request,'blocked');
   RETURN -1;
 END IF;
 SELECT array_agg(DISTINCT s.episode_id) INTO linked FROM public.memory_sources s
 JOIN public.memory_versions v ON v.id=s.memory_version_id AND v.tenant_id=s.tenant_id
 WHERE v.tenant_id=p_tenant AND v.memory_id=p_memory;
 DELETE FROM public.memory_indexes WHERE tenant_id=p_tenant AND version_id IN
   (SELECT id FROM public.memory_versions WHERE tenant_id=p_tenant AND memory_id=p_memory);
 DELETE FROM public.memory_sources WHERE tenant_id=p_tenant AND memory_version_id IN
   (SELECT id FROM public.memory_versions WHERE tenant_id=p_tenant AND memory_id=p_memory);
 UPDATE public.memory_versions SET content='[erased]',confidence=0,importance=0
   WHERE tenant_id=p_tenant AND memory_id=p_memory;
 FOREACH episode IN ARRAY coalesce(linked,ARRAY[]::uuid[]) LOOP
   PERFORM 1 FROM public.episodes WHERE tenant_id=p_tenant AND id=episode FOR UPDATE;
   IF NOT EXISTS(SELECT 1 FROM public.memory_sources WHERE tenant_id=p_tenant AND episode_id=episode) THEN
     UPDATE public.episodes SET content='[]'::jsonb,erased_at=now() WHERE tenant_id=p_tenant AND id=episode;
     UPDATE public.formation_jobs SET state='rejected',lease_token=NULL,error_code='source_erased',completed_at=now()
       WHERE tenant_id=p_tenant AND episode_id=episode AND state IN ('pending','leased','retryable_failure');
   ELSE retained:=retained+1; END IF;
 END LOOP;
 UPDATE public.erasure_jobs SET state='completed',completed_at=now(),retained_shared_episodes=retained
 WHERE tenant_id=p_tenant AND memory_id=p_memory;
 INSERT INTO public.audit_events(id,tenant_id,actor_id,subject_id,action,resource_type,resource_id,request_id,outcome)
 VALUES(gen_random_uuid(),p_tenant,p_actor,m.subject_id,'memory.erase','memory',p_memory,p_request,'completed');
 RETURN retained;
END $$;
REVOKE ALL ON FUNCTION erase_suppressed_memory(uuid,uuid,uuid,text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION erase_suppressed_memory(uuid,uuid,uuid,text) TO memory_app;

CREATE UNIQUE INDEX one_open_memory_version ON memory_versions(tenant_id,memory_id) WHERE valid_to IS NULL;
