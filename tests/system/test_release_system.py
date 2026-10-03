import os,subprocess,re
from uuid import uuid4
from sqlalchemy import text,create_engine
from sqlalchemy.engine import make_url
from contracts.models import CorrectMemoryRequest,SourceInput,ForgetMemoryRequest
from apps.api.auth import Principal
from persistence.repositories import Repository
from scripts.database import migrate,bootstrap
from test_worker import capture
from apps.worker.main import Worker

def test_migration_forward_from_prior_schema_and_idempotency(engine):
    with engine.begin() as c:c.execute(text('DROP SCHEMA public CASCADE; CREATE SCHEMA public'))
    migrate(engine,through='002_foundation_security.sql')
    with engine.connect() as c:assert c.execute(text('SELECT count(*) FROM schema_migrations')).scalar_one()==2
    migrate(engine);migrate(engine)
    with engine.connect() as c:assert c.execute(text('SELECT count(*) FROM schema_migrations')).scalar_one()==6

def test_dump_restore_rechecks_isolation_correction_and_suppression(engine,identities):
    container=os.environ.get('BACKUP_TEST_CONTAINER','')
    assert re.fullmatch(r'[A-Za-z0-9_.-]+',container),'BACKUP_TEST_CONTAINER must identify the disposable test PostgreSQL container'
    repo=Repository(engine);principal=Principal(identities['tenant_a'],identities['actor_a'])
    episode=capture(engine,identities);Worker(repo).run_once(identities['tenant_a'])
    memory=repo.list_memories(principal,identities['subject_a'],'restore-prepare')[0][0]
    source_db=make_url(os.environ['TEST_DATABASE_URL']).database
    assert source_db.startswith('memory_test')
    target='memory_test_restore_'+uuid4().hex[:12]
    dump=subprocess.run(['docker','exec',container,'pg_dump','--username','postgres','--no-owner','--dbname',source_db],check=True,capture_output=True).stdout
    from scripts.encrypted_backup import encrypt_backup,decrypt_backup
    key=os.urandom(32);artifact=encrypt_backup(dump,key,{'deployment_id':'synthetic-restore','release_sha':'0'*40})
    assert b'I prefer tea' not in artifact
    dump,manifest=decrypt_backup(artifact,key)
    assert manifest['deployment_id']=='synthetic-restore'
    # Synthetic decrypted data stays in memory; only authenticated ciphertext can be persisted.
    admin=create_engine(make_url(os.environ['TEST_DATABASE_URL']).set(database='postgres'),isolation_level='AUTOCOMMIT')
    restored=None
    try:
        with admin.connect() as c:c.execute(text('CREATE DATABASE '+target))
        subprocess.run(['docker','exec','-i',container,'psql','--username','postgres','--dbname',target,'--single-transaction','--set','ON_ERROR_STOP=1'],input=dump,check=True,capture_output=True)
        restored=create_engine(make_url(os.environ['TEST_DATABASE_URL']).set(database=target));r=Repository(restored)
        assert r.get_memory(Principal(identities['tenant_b'],identities['actor_b']),memory.id,'restore-denied') is None
        own=r.get_memory(principal,memory.id,'restore-read');assert own.content=='I prefer tea'
        corrected=r.correct(principal,own.id,CorrectMemoryRequest(expected_version_id=own.version_id,content='I prefer coffee',confidence=1,source=SourceInput(episode_id=episode.episode.id)),'restore-correct')
        history=r.history(principal,own.id,'restore-history');assert len(history['versions'])==2 and history['versions'][0]['valid_to'] is not None
        r.forget(principal,own.id,ForgetMemoryRequest(confirm=True,expected_version_id=corrected.version_id),'restore-forget')
        assert r.get_memory(principal,own.id,'restore-hidden') is None
        assert not r.search(principal,identities['subject_a'],'coffee','restore-search')
        assert r.erase_next(identities['tenant_a']);assert r.erasure_status(principal,own.id,'restore-status')['state']=='completed'
    finally:
        if restored:restored.dispose()
        with admin.connect() as c:c.execute(text('DROP DATABASE IF EXISTS '+target+' WITH (FORCE)'))
        admin.dispose()


def test_applied_migration_checksum_change_is_rejected(engine):
    with engine.begin() as c:
        original=c.execute(text("SELECT sha256 FROM schema_migrations WHERE name='001_foundation.sql'")).scalar_one()
        c.execute(text("UPDATE schema_migrations SET sha256='invalid' WHERE name='001_foundation.sql'"))
    try:
        import pytest
        with pytest.raises(ValueError,match='checksum changed'):migrate(engine)
    finally:
        with engine.begin() as c:c.execute(text("UPDATE schema_migrations SET sha256=:hash WHERE name='001_foundation.sql'"),{'hash':original})
