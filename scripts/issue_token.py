"""Print an expiring token only to the trusted operator terminal."""
import argparse
from uuid import UUID
from apps.api.auth import issue_token
parser=argparse.ArgumentParser();parser.add_argument('--tenant',required=True,type=UUID);parser.add_argument('--actor',required=True,type=UUID);args=parser.parse_args()
print(issue_token(args.tenant,args.actor))
