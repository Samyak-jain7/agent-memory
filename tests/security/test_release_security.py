import json,logging
import pytest
from sqlalchemy import text
from deploy.readiness import validate,REQUIRED
from model_gateway import Policy,Candidate
from apps.worker.main import Worker
from persistence.repositories import Repository
from test_worker import capture

def test_secret_filter_has_no_structured_or_telemetry_payload(engine,identities,caplog):
    sentinel='synthetic-secret-never-log'
    caplog.set_level(logging.INFO,logger='agent_memory')
    capture(engine,identities,'My password='+sentinel)
    Worker(Repository(engine)).run_once(identities['tenant_a'])
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==0
        for table in ['audit_events','formation_jobs','memory_versions']:
            assert sentinel not in str(c.execute(text('SELECT * FROM '+table)).all())
    assert sentinel not in caplog.text

@pytest.mark.parametrize('value',[{}, {'mode':'production'},json.loads(__import__('pathlib').Path('deploy/production.example.json').read_text())])
def test_production_readiness_fails_without_approved_values_or_encryption(value):
    errors=validate(value);assert errors
    assert any('target' in error for error in errors)
    if value.get('database_sslmode')!='verify-full':assert any('TLS' in error for error in errors)
    assert any('encryption' in error for error in errors)

def approved_fixture():
    return {'mode':'production','deployment_id':'synthetic-test','release_sha':'0'*40,'evidence_receipts':{kind:{'deployment_id':'synthetic-test','release_sha':'0'*40,'verified_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'artifact_sha256':'0'*64,'artifact_path':'synthetic.txt','provenance':'synthetic fixture only'} for kind in ['storage','backup','restore','provider','telemetry','retention','erasure','backup_expiry','deployment','approval']},'approval':{'approved_by':'synthetic test operator','approved_at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'evidence':'test-only'},'targets':{name:(.9 if name in {'availability','extraction_precision','retrieval_relevance','citation_correctness'} else .1 if name=='correction_rate' else 10) for name in REQUIRED},'observed':{name:(.95 if name in {'availability','extraction_precision','retrieval_relevance','citation_correctness'} else .05 if name=='correction_rate' else 10) for name in REQUIRED},'public_api_url':'https://example.test','database_sslmode':'verify-full',**{k:True for k in ['storage_encryption_verified','backup_encryption_verified','restore_verified','live_provider_evaluated','telemetry_export_verified']},**{k:'synthetic test evidence' for k in ['storage_encryption_evidence','backup_encryption_evidence','provider_data_policy','retention_policy','erasure_policy','backup_expiry_policy','deployment_evidence']}}

def test_readiness_regressions_and_invalid_numbers_fail():
    fixture=approved_fixture();assert not validate(fixture)
    for name in REQUIRED:
        value=approved_fixture();value['observed'][name]=0 if name in {'throughput_rps','availability','extraction_precision','retrieval_relevance','citation_correctness'} else 100
        assert validate(value)
    fixture['targets']['throughput_rps']=True;assert validate(fixture)
    fixture['targets']['throughput_rps']=float('nan');assert validate(fixture)

def test_evidence_privileges_are_immutable(engine,identities):
    with engine.begin() as c:
        for table in ['episodes','memory_sources','audit_events','authentication_events']:
            assert c.execute(text("SELECT has_table_privilege('memory_app',:table,'UPDATE')"),{'table':table}).scalar_one() is False
            assert c.execute(text("SELECT has_table_privilege('memory_app',:table,'DELETE')"),{'table':table}).scalar_one() is False


def test_restricted_login_role_enforces_database_boundary(engine,identities):
    import secrets
    from uuid import uuid4
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url
    from scripts.database import provision_runtime
    from apps.api.auth import Principal
    role='memory_verify_'+uuid4().hex[:12];password=secrets.token_hex(24)
    provision_runtime(engine,password,role);runtime=create_engine(engine.url.set(username=role,password=password))
    try:
        with runtime.begin() as c:
            assert c.execute(text('SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user')).scalar_one() is False
            from deploy.readiness import validate_runtime_role
            validate_runtime_role(c)
        with pytest.raises(Exception):
            with runtime.begin() as c:c.execute(text('SELECT * FROM episodes'))
        with runtime.begin() as c:
            c.execute(text('SET LOCAL ROLE memory_app'))
            c.execute(text("SELECT set_config('app.tenant_id',:tenant,true)"),{'tenant':str(identities['tenant_a'])})
            assert c.execute(text('SELECT count(*) FROM subjects')).scalar_one()==2
            assert c.execute(text('SELECT count(*) FROM subjects WHERE id=:id'),{'id':identities['subject_b']}).scalar_one()==0
    finally:
        runtime.dispose()
        with engine.begin() as c:c.execute(text('DROP ROLE '+role))


def test_production_api_start_fails_closed_without_evidence(monkeypatch):
    from apps.api.main import create_app
    monkeypatch.setenv('MEMORY_ENV','production');monkeypatch.delenv('PRODUCTION_READINESS_FILE',raising=False)
    with pytest.raises(RuntimeError,match='approval file required'):create_app('postgresql+psycopg://127.0.0.1/unused')


def test_local_trace_exporter_ignores_unapproved_attributes_and_foreign_spans(caplog):
    import logging
    from types import SimpleNamespace
    from observability import SafeSpanExporter
    caplog.set_level(logging.INFO,logger='agent_memory')
    context=SimpleNamespace(trace_id=1,span_id=2)
    own=SimpleNamespace(name='memory.test',context=context,parent=None,attributes={'outcome':'succeeded','payload':'synthetic-secret-export-test'})
    foreign=SimpleNamespace(name='foreign.synthetic-secret-export-test',context=context,parent=None,attributes={})
    SafeSpanExporter().export([own,foreign]);assert 'synthetic-secret-export-test' not in caplog.text
    assert 'memory.test' in caplog.text

@pytest.mark.parametrize('value',[None,[],{'approval':[]},{'targets':'invalid'},{'public_api_url':False}])
def test_readiness_malformed_shapes_fail_closed(value):
    assert validate(value)

def test_retention_policy_requires_exact_match():
    fixture=approved_fixture();fixture['observed']['retention_seconds']=0
    assert any('policy mismatch' in e for e in validate(fixture))

@pytest.mark.parametrize('module',['apps.worker.main','apps.worker.erase','scripts.reindex'])
def test_direct_entrypoints_require_production_evidence(module):
    import subprocess,sys,os
    env={**os.environ,'MEMORY_ENV':'production','DATABASE_URL':'postgresql+psycopg://127.0.0.1/unused'}
    env.pop('PRODUCTION_READINESS_FILE',None)
    result=subprocess.run([sys.executable,'-m',module,'--tenant','00000000-0000-0000-0000-000000000001','--once'] if module!='scripts.reindex' else [sys.executable,'-m',module,'--tenant','00000000-0000-0000-0000-000000000001'],env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode!=0 and 'approval file required' in result.stderr

def test_unknown_environment_fails_closed(monkeypatch):
    from deploy.readiness import enforce_environment
    monkeypatch.setenv('MEMORY_ENV','prodution')
    with pytest.raises(RuntimeError,match='Unknown MEMORY_ENV'):enforce_environment('unused')

def test_unsafe_application_role_prevents_migration(engine):
    from scripts.database import migrate
    with engine.begin() as c:c.execute(text('ALTER ROLE memory_app BYPASSRLS'))
    try:
        with pytest.raises(RuntimeError,match='Unsafe application role'):migrate(engine)
    finally:
        with engine.begin() as c:c.execute(text('ALTER ROLE memory_app NOBYPASSRLS'))

def test_restricted_role_validator_rejects_admin(engine):
    from deploy.readiness import validate_runtime_role
    with engine.connect() as c:
        with pytest.raises(RuntimeError,match='Unsafe effective'):validate_runtime_role(c)

def test_duplicate_readiness_fields_rejected(tmp_path):
    from deploy.readiness import load_config
    path=tmp_path/'duplicate.json';path.write_text('{"mode":"production","mode":"development"}')
    with pytest.raises(RuntimeError,match='Invalid production'):load_config(path)


def test_receipts_require_bound_intact_recent_artifacts(tmp_path):
    from datetime import datetime,timedelta,timezone
    from hashlib import sha256
    fixture=approved_fixture();artifact=tmp_path/'synthetic.txt';artifact.write_text('Synthetic evidence, not production verification')
    for receipt in fixture['evidence_receipts'].values():receipt['artifact_sha256']=sha256(artifact.read_bytes()).hexdigest()
    assert not validate(fixture,tmp_path)
    artifact.write_text('Changed');assert validate(fixture,tmp_path)
    fixture['evidence_receipts']['storage']['release_sha']='1'*40;assert validate(fixture)
    fixture=approved_fixture();fixture['evidence_receipts']['backup']['verified_at']=(datetime.now(timezone.utc)-timedelta(days=8)).isoformat();assert validate(fixture)
    fixture=approved_fixture();fixture['evidence_receipts']['backup']['artifact_path']='../escape';assert validate(fixture)


def test_invalid_url_and_oversized_number_fail_closed():
    fixture=approved_fixture();fixture['public_api_url']='https://[malformed';assert validate(fixture)
    fixture=approved_fixture();fixture['targets']['throughput_rps']=10**1000;assert validate(fixture)


def test_runtime_database_owner_is_rejected(engine):
    import secrets
    from uuid import uuid4
    from sqlalchemy import create_engine
    from scripts.database import provision_runtime
    from deploy.readiness import validate_runtime_role
    role='memory_owner_'+uuid4().hex[:12];database='memory_test_owner_'+uuid4().hex[:12];password=secrets.token_hex(24)
    provision_runtime(engine,password,role)
    admin=create_engine(engine.url.set(database='postgres'),isolation_level='AUTOCOMMIT');runtime=None
    try:
        with admin.connect() as c:c.execute(text('CREATE DATABASE '+database+' OWNER '+role))
        runtime=create_engine(engine.url.set(database=database,username=role,password=password))
        with runtime.connect() as c:
            assert c.execute(text("SELECT nspowner=(SELECT oid FROM pg_roles WHERE rolname='pg_database_owner') FROM pg_namespace WHERE nspname='public'")).scalar_one()
            with pytest.raises(RuntimeError,match='must not own'):validate_runtime_role(c)
    finally:
        if runtime:runtime.dispose()
        with admin.connect() as c:
            c.execute(text('DROP DATABASE IF EXISTS '+database+' WITH (FORCE)'))
            c.execute(text('DROP ROLE '+role))
        admin.dispose()
