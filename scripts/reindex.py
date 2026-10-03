"""Trusted, explicitly tenant-scoped reindex of active legacy current versions."""
import argparse,os
from uuid import UUID
from deploy.readiness import enforce_environment
from sqlalchemy import create_engine,text
from persistence.repositories import Repository
parser=argparse.ArgumentParser();parser.add_argument('--tenant',required=True,type=UUID);args=parser.parse_args()
enforce_environment(os.environ['DATABASE_URL'])
engine=create_engine(os.environ['DATABASE_URL']);repo=Repository(engine)
try:
    with engine.begin() as c:
        repo._scope(c,args.tenant)
        rows=c.execute(text("SELECT m.current_version_id,v.content FROM memories m JOIN memory_versions v ON v.id=m.current_version_id LEFT JOIN memory_indexes i ON i.version_id=v.id WHERE m.lifecycle_state='active' AND (i.state IS NULL OR i.state='pending') FOR UPDATE OF m")).mappings().all()
        for row in rows:repo._index(c,args.tenant,row['current_version_id'],row['content'])
    print('Reindexed',len(rows),'active current versions.')
finally:engine.dispose()
