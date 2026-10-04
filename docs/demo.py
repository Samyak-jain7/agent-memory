"""Fresh disposable offline app demo. Never connects to an existing database."""
import json,os,secrets,socket,subprocess,sys,tempfile,time
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sqlalchemy import create_engine
from scripts.database import migrate,bootstrap,provision_runtime
from apps.api.auth import issue_token
from apps.worker.main import Worker
from persistence.repositories import Repository
from contracts.models import CaptureEpisodeRequest,Message

def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

def main():
    os.chdir(ROOT)
    name='agent-memory-demo-'+uuid4().hex[:10]
    password=secrets.token_hex(24);runtime_password=secrets.token_hex(24)
    env={**os.environ,'POSTGRES_PASSWORD':password,'MODEL_PROVIDER':'offline','EXTRACTION_PROVIDER':'offline','MEMORY_VERIFIER':'none','MEMORY_FORMATION_MODE':'supervised','MEMORY_ENV':'development','MEMORY_AUTH_SECRET':secrets.token_hex(32),'ERASURE_RETENTION_SECONDS':'0','PYTHONDONTWRITEBYTECODE':'1','NEXT_TELEMETRY_DISABLED':'1'}
    children=[];engine=None;started=False
    output=None if '--verbose' in sys.argv[1:] else subprocess.DEVNULL
    with tempfile.TemporaryDirectory(prefix='agent-memory-demo-') as temporary:
        try:
            print('Building the local console…',flush=True)
            subprocess.run(['npm','--prefix','apps/web','run','build'],env=env,stdout=output,stderr=output,check=True)
            subprocess.run(['docker','run','--rm','-d','--name',name,'-e','POSTGRES_PASSWORD','-e','POSTGRES_DB=memory_demo','-p','127.0.0.1::5432','pgvector/pgvector:pg17'],env=env,stdout=subprocess.DEVNULL,check=True);started=True
            port=subprocess.check_output(['docker','port',name,'5432/tcp'],text=True).strip().rsplit(':',1)[1]
            admin=f'postgresql+psycopg://postgres:{password}@127.0.0.1:{port}/memory_demo'
            engine=create_engine(admin)
            for _ in range(60):
                ready=subprocess.run(['docker','exec',name,'pg_isready','-U','postgres','-d','memory_demo'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
                if ready:break
                time.sleep(.5)
            else:raise RuntimeError('Demo PostgreSQL did not become ready')
            migrate(engine);provision_runtime(engine,runtime_password,role='memory_demo_service')
            # These grants exist only inside this newly created synthetic demo container.
            ids=bootstrap(engine,consent=True,inspect_sources=True,review_memory=True)
            env['DATABASE_URL']=f'postgresql+psycopg://memory_demo_service:{runtime_password}@127.0.0.1:{port}/memory_demo'
            os.environ.update({k:env[k] for k in ['MODEL_PROVIDER','EXTRACTION_PROVIDER','MEMORY_VERIFIER','MEMORY_FORMATION_MODE','MEMORY_AUTH_SECRET','MEMORY_ENV']})
            runtime=create_engine(env['DATABASE_URL']);repo=Repository(runtime)
            from apps.api.auth import Principal
            p=Principal(ids['tenant'],ids['actor'])
            repo.capture_episode(p,CaptureEpisodeRequest(subject_id=ids['subject'],session_id=ids['session'],messages=[Message(role='user',content='I prefer jasmine tea.'),Message(role='user',content='I enjoy hiking on weekends.')],idempotency_key='synthetic-demo'),'demo')
            Worker(repo,formation_mode='supervised').run_once(ids['tenant']);runtime.dispose()
            api_port,web_port=free_port(),free_port();env['API_URL']=f'http://127.0.0.1:{api_port}'
            credentials=Path(temporary)/'connection.json';credentials.write_text(json.dumps({'url':f'http://127.0.0.1:{web_port}','api_url':env['API_URL'],'subject_id':str(ids['subject']),'tenant_id':str(ids['tenant']),'actor_id':str(ids['actor']),'session_id':str(ids['session']),'token':issue_token(ids['tenant'],ids['actor'])}));credentials.chmod(0o600)
            children.append(subprocess.Popen([sys.executable,'-m','uvicorn','apps.api.main:app','--host','127.0.0.1','--port',str(api_port),'--no-access-log'],env=env,stdout=output,stderr=output))
            children.append(subprocess.Popen(['npm','--prefix','apps/web','run','start','--','--hostname','127.0.0.1','--port',str(web_port)],env=env,stdout=output,stderr=output,start_new_session=True))
            for module in ['apps.worker.main','apps.worker.erase']:
                children.append(subprocess.Popen([sys.executable,'-m',module,'--tenant',str(ids['tenant'])],env=env,stdout=output,stderr=output))
            print(f'Offline synthetic demo: http://127.0.0.1:{web_port}\nAPI: {env["API_URL"]}/docs\nPrivate connection file: {credentials}\nRead the token locally; never share it. Ctrl+C removes this demo only.',flush=True)
            while True:
                if any(c.poll() is not None for c in children):raise RuntimeError('A demo server stopped; check dependencies and ports')
                time.sleep(1)
        finally:
            import signal
            for i,c in reversed(list(enumerate(children))):
                if c.poll() is None:
                    if i==1:os.killpg(c.pid,signal.SIGTERM)
                    else:c.terminate()
                    try:c.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        if i==1:os.killpg(c.pid,signal.SIGKILL)
                        else:c.kill()
                        c.wait()
            if engine:engine.dispose()
            if started:subprocess.run(['docker','stop',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

if __name__=='__main__':
    try:main()
    except KeyboardInterrupt:pass
