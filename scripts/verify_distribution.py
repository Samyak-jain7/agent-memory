"""Build and verify the wheel in an isolated environment outside the checkout."""
import os,subprocess,sys,tempfile,zipfile,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CHECK='''
import os
from pathlib import Path
import apps.api.auth,apps.worker.main,apps.worker.erase,memory_sdk,contracts,deploy.readiness
from scripts.database import migration_directory
assert len(list(migration_directory().glob('*.sql')))==6
assert 'site-packages' in str(Path(apps.api.auth.__file__))
assert not Path('pyproject.toml').exists()
print('Installed wheel: API, workers, SDK and six migration files verified outside checkout.')
'''

def main():
    with tempfile.TemporaryDirectory(prefix='memory-wheel-') as temporary:
        directory=Path(temporary);wheels=directory/'wheels';environment=directory/'venv'
        source=directory/'source';source.mkdir()
        for name in ['pyproject.toml','README.md']:
            shutil.copy2(ROOT/name,source/name)
        for name in ['packages','scripts','deploy','migrations','apps/api','apps/worker']:
            shutil.copytree(ROOT/name,source/name,ignore=shutil.ignore_patterns('__pycache__','*.pyc','*.egg-info'))
        subprocess.run(['uv','--quiet','build','--wheel','--out-dir',str(wheels),str(source)],check=True)
        wheel,=wheels.glob('*.whl')
        with zipfile.ZipFile(wheel) as archive:
            names=archive.namelist()
            assert not any('/node_modules/' in name or name.startswith('apps/web/') or name.startswith('.genesis/') for name in names)
            assert len([name for name in names if '/share/agent-memory/migrations/' in name and name.endswith('.sql')])==6
        subprocess.run(['uv','--quiet','venv','--python',sys.executable,str(environment)],check=True)
        python=environment/'bin'/'python'
        subprocess.run(['uv','--quiet','pip','install','--python',str(python),str(wheel)],check=True)
        subprocess.run([str(python),'-I','-c',CHECK],cwd=directory,check=True,env={**os.environ,'MEMORY_ENV':'development','PYTHONDONTWRITEBYTECODE':'1'})
if __name__=='__main__':main()
