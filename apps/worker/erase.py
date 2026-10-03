import argparse
import os
import time
from uuid import UUID
from sqlalchemy import create_engine
from persistence.repositories import Repository

def main():
    p=argparse.ArgumentParser();p.add_argument('--tenant',required=True,type=UUID);p.add_argument('--once',action='store_true');args=p.parse_args()
    engine=create_engine(os.environ['DATABASE_URL']);repo=Repository(engine)
    try:
        while True:
            worked=repo.erase_next(args.tenant)
            if args.once:break
            if not worked:time.sleep(1)
    finally:engine.dispose()

if __name__=='__main__':main()
