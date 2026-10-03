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
    return {'mode':'production','approval':{'approved_by':'synthetic test operator','approved_at':'2026-01-01','evidence':'test-only'},'targets':{name:(.9 if name in {'availability','extraction_precision','retrieval_relevance','citation_correctness'} else .1 if name=='correction_rate' else 10) for name in REQUIRED},'observed':{name:(.95 if name in {'availability','extraction_precision','retrieval_relevance','citation_correctness'} else .05 if name=='correction_rate' else 10) for name in REQUIRED},'public_api_url':'https://example.test','database_sslmode':'verify-full',**{k:True for k in ['storage_encryption_verified','backup_encryption_verified','restore_verified','live_provider_evaluated','telemetry_export_verified']},**{k:'synthetic test evidence' for k in ['storage_encryption_evidence','backup_encryption_evidence','provider_data_policy','retention_policy','erasure_policy','backup_expiry_policy','deployment_evidence']}}

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
