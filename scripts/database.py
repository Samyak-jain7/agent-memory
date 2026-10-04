"""Trusted migration/bootstrap helpers. Runtime users must not own the schema."""
import hashlib,json,os,sysconfig
from pathlib import Path
from uuid import uuid4
from sqlalchemy import text
from deploy.readiness import validate_application_role
ROOT=Path(__file__).resolve().parents[1]

def migration_directory():
    source=ROOT/'migrations'
    installed=Path(sysconfig.get_path('data'))/'share'/'agent-memory'/'migrations'
    directory=source if source.is_dir() else installed
    if not directory.is_dir() or not list(directory.glob('*.sql')):raise RuntimeError('Packaged migrations missing')
    return directory

def migrate(engine,through=None):
    with engine.begin() as c:
        c.execute(text('SELECT pg_advisory_xact_lock(174643013)'))
        validate_application_role(c,allow_missing=True)
        c.execute(text('CREATE TABLE IF NOT EXISTS schema_migrations(name text PRIMARY KEY,sha256 text NOT NULL,applied_at timestamptz NOT NULL DEFAULT now())'))
        for path in sorted(migration_directory().glob('*.sql')):
            if through and path.name>through:break
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            old=c.execute(text('SELECT sha256 FROM schema_migrations WHERE name=:name'),{'name':path.name}).scalar_one_or_none()
            if old:
                if old!=digest:raise ValueError('Applied migration checksum changed: '+path.name)
                continue
            c.execute(text(path.read_text()))
            c.execute(text('INSERT INTO schema_migrations(name,sha256) VALUES (:name,:hash)'),{'name':path.name,'hash':digest})

def bootstrap(engine,consent=False,inspect_sources=False,review_memory=False):
    ids={name:uuid4() for name in ['tenant','actor','subject','session']}
    with engine.begin() as c:
        c.execute(text("INSERT INTO tenants(id,name) VALUES (:tenant,'Local development')"),ids)
        c.execute(text('INSERT INTO actors(id,tenant_id) VALUES (:actor,:tenant)'),ids)
        c.execute(text('INSERT INTO subjects(id,tenant_id,memory_consent) VALUES (:subject,:tenant,:consent)'),{**ids,'consent':consent})
        c.execute(text('INSERT INTO sessions(id,tenant_id,subject_id) VALUES (:session,:tenant,:subject)'),ids)
        c.execute(text('INSERT INTO actor_subject_grants(tenant_id,actor_id,subject_id,can_inspect_sources,can_review_memory) VALUES (:tenant,:actor,:subject,:inspect,:review)'),{**ids,'inspect':inspect_sources,'review':review_memory})
        c.execute(text("INSERT INTO audit_events(id,tenant_id,actor_id,subject_id,action,resource_type,resource_id,request_id,outcome) VALUES (:id,:tenant,:actor,:subject,'admin.bootstrap','subject',:subject,:request,'development_provisioned')"),{**ids,'id':uuid4(),'request':str(uuid4())})
    return ids

def provision_runtime(engine,password,role='memory_service'):
    import re
    from psycopg import sql
    if not re.fullmatch(r'[a-z][a-z0-9_]{3,62}',role) or len(password)<16:raise ValueError('Restricted role name and password of at least 16 characters required')
    with engine.begin() as connection:
        validate_application_role(connection)
        raw=connection.connection.driver_connection
        # CREATE ROLE refuses to overwrite an existing role or its credentials.
        raw.execute(sql.SQL('CREATE ROLE {} LOGIN NOINHERIT NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Identifier(role),sql.Literal(password)))
        raw.execute(sql.SQL('GRANT memory_app TO {}').format(sql.Identifier(role)))

if __name__=='__main__':
    import argparse
    from sqlalchemy import create_engine
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['migrate','bootstrap','runtime-role']);parser.add_argument('--development',action='store_true');parser.add_argument('--consent',action='store_true');parser.add_argument('--inspect-sources',action='store_true');parser.add_argument('--review-memory',action='store_true');parser.add_argument('--role',default='memory_service');args=parser.parse_args()
    engine=create_engine(os.environ['ADMIN_DATABASE_URL'])
    try:
        if args.command=='migrate':migrate(engine);print('Migrations verified and applied.')
        elif args.command=='runtime-role':
            provision_runtime(engine,os.environ['MEMORY_DB_PASSWORD'],args.role);print('Restricted runtime role created.')
        else:
            if not args.development:parser.error('Bootstrap is limited to explicitly requested development data.')
            ids=bootstrap(engine,args.consent,args.inspect_sources,args.review_memory)
            print(json.dumps({k:str(v) for k,v in ids.items()}))
    finally:engine.dispose()
