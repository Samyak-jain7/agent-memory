ALTER TABLE formation_jobs ADD COLUMN lease_token uuid;
ALTER TABLE formation_jobs ADD COLUMN available_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE formation_jobs ADD COLUMN completed_at timestamptz;
ALTER TABLE formation_jobs ADD COLUMN request_id text NOT NULL DEFAULT 'legacy';
ALTER TABLE formation_jobs ADD COLUMN trace_context jsonb NOT NULL DEFAULT '{}';
ALTER TABLE formation_jobs ADD COLUMN outcomes jsonb NOT NULL DEFAULT '[]';
ALTER TABLE formation_jobs ADD COLUMN error_code text;
CREATE INDEX formation_ready ON formation_jobs(tenant_id,available_at) WHERE state IN ('pending','leased','retryable_failure');
ALTER TABLE memories ADD COLUMN formation_job_id uuid;
ALTER TABLE memories ADD COLUMN candidate_index integer;
ALTER TABLE memories ADD CONSTRAINT formation_candidate UNIQUE(tenant_id,formation_job_id,candidate_index);
ALTER TABLE memories ADD FOREIGN KEY(tenant_id,formation_job_id) REFERENCES formation_jobs(tenant_id,id);

ALTER TABLE subjects ADD COLUMN memory_consent boolean NOT NULL DEFAULT false;
