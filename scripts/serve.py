"""Required production entry point for API and tenant workers."""
import os,sys,subprocess
from deploy.readiness import enforce_environment
if len(sys.argv)<2 or sys.argv[1] not in {'api','worker','erase'}:raise SystemExit('Usage: python -m scripts.serve api|worker|erase [arguments]')
enforce_environment(os.environ['DATABASE_URL'])
command={'api':[sys.executable,'-m','uvicorn','apps.api.main:app','--no-access-log'],'worker':[sys.executable,'-m','apps.worker.main'],'erase':[sys.executable,'-m','apps.worker.erase']}[sys.argv[1]]
raise SystemExit(subprocess.call(command+sys.argv[2:]))
