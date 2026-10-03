import os
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import create_engine,text
from sqlalchemy.engine import make_url
DATABASE_URL=os.environ.get('TEST_DATABASE_URL','')
@pytest.fixture(scope="session")
def engine():
    if not DATABASE_URL or not (make_url(DATABASE_URL).database or "").startswith("memory_test"):
        pytest.fail("TEST_DATABASE_URL must explicitly select a disposable memory_test database")
    engine = create_engine(DATABASE_URL)
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
            for migration in sorted(Path("migrations").glob("*.sql")):
                connection.execute(text(migration.read_text()))
    except Exception as error:
        pytest.fail(f"PostgreSQL with pgvector is required: {error}")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(engine):
    with engine.begin() as connection:
        connection.execute(text("TRUNCATE audit_events,memory_sources,memory_versions,memories,formation_jobs,episodes,sessions,actor_subject_grants,subjects,actors,tenants CASCADE"))


@pytest.fixture
def identities(engine):
    values = {name: uuid4() for name in ("tenant_a", "tenant_b", "actor_a", "actor_b", "subject_a", "subject_a2", "subject_b", "session_a")}
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO tenants(id,name) VALUES (:tenant_a,'A'),(:tenant_b,'B')"), values)
        connection.execute(text("INSERT INTO actors(id,tenant_id) VALUES (:actor_a,:tenant_a),(:actor_b,:tenant_b)"), values)
        connection.execute(text("INSERT INTO subjects(id,tenant_id) VALUES (:subject_a,:tenant_a),(:subject_a2,:tenant_a),(:subject_b,:tenant_b)"), values)
        connection.execute(text("UPDATE subjects SET memory_consent=true"))
        connection.execute(text("INSERT INTO actor_subject_grants(tenant_id,actor_id,subject_id) VALUES (:tenant_a,:actor_a,:subject_a),(:tenant_b,:actor_b,:subject_b)"), values)
        connection.execute(text("INSERT INTO sessions(id,tenant_id,subject_id) VALUES (:session_a,:tenant_a,:subject_a)"), values)
    return values

