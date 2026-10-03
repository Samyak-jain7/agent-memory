CREATE TABLE memory_indexes (
 tenant_id uuid NOT NULL,
 version_id uuid PRIMARY KEY,
 search_document tsvector NOT NULL,
 embedding vector(64),
 provider text NOT NULL,
 state text NOT NULL CHECK(state IN ('pending','ready')),
 FOREIGN KEY(tenant_id,version_id) REFERENCES memory_versions(tenant_id,id)
);
CREATE INDEX memory_text_search ON memory_indexes USING gin(search_document);
ALTER TABLE memory_indexes ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_indexes FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON memory_indexes USING(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
 WITH CHECK(tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
GRANT SELECT,INSERT,UPDATE,DELETE ON memory_indexes TO memory_app;
-- Existing versions have a visible pending indexing state until trusted reindexing.
INSERT INTO memory_indexes(tenant_id,version_id,search_document,provider,state)
 SELECT tenant_id,id,to_tsvector('english',content),'unconfigured','pending' FROM memory_versions;
