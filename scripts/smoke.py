"""Public SDK lifecycle against an isolated synthetic development subject."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json,os,secrets,threading,socket,time
from uuid import uuid4
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
import uvicorn
from apps.api.main import create_app
from apps.api.auth import issue_token
from apps.worker.main import Worker
from persistence.repositories import Repository
from contracts.models import CaptureEpisodeRequest,Message,ContextRequest,CorrectMemoryRequest,SourceInput,ForgetMemoryRequest
from memory_sdk import MemoryClient,MemoryAPIError
from scripts.database import migrate,bootstrap

def run():
    url=os.environ.get('TEST_DATABASE_URL','')
    if not url or not (make_url(url).database or '').startswith('memory_test'):raise RuntimeError('An explicit disposable memory_test database is required')
    engine=create_engine(url);migrate(engine);ids=bootstrap(engine,consent=True,inspect_sources=True)
    os.environ.setdefault('MEMORY_AUTH_SECRET',secrets.token_urlsafe(48))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(create_app(url),log_level='error',access_log=False));thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    try:
        for _ in range(100):
            if server.started:break
            time.sleep(.02)
        if not server.started:raise RuntimeError('Local API failed to start')
        with MemoryClient(issue_token(ids['tenant'],ids['actor']),base_url=f'http://127.0.0.1:{port}') as sdk:
            episode=sdk.create_episode(CaptureEpisodeRequest(subject_id=ids['subject'],session_id=ids['session'],messages=[Message(role='user',content='I prefer jasmine tea')],idempotency_key=str(uuid4())))
            assert sdk.job_status(episode.job.id).job.state=='pending'
            assert Worker(Repository(engine)).run_once(ids['tenant'])
            assert sdk.job_status(episode.job.id).job.state=='succeeded'
            memory=sdk.search(ids['subject'],'jasmine tea').items[0]
            context=sdk.build_context(ContextRequest(subject_id=ids['subject'],query='jasmine tea'));assert memory.id in context.memory_ids and str(memory.id) in context.citations
            correction=sdk.update_memory(memory.id,CorrectMemoryRequest(expected_version_id=memory.version_id,content='I prefer oolong tea',confidence=1,source=SourceInput(episode_id=episode.episode.id))).memory
            assert len(sdk.history(memory.id).versions)==2
            sdk.forget_memory(memory.id,ForgetMemoryRequest(confirm=True,expected_version_id=correction.version_id))
            assert not sdk.search(ids['subject'],'oolong tea').items
            try:sdk.get_memory(memory.id)
            except MemoryAPIError as error:assert error.status==404
            else:raise AssertionError('Suppressed memory remained visible')
            assert Repository(engine).erase_next(ids['tenant'])
            assert sdk.erasure_status(memory.id).erasure.state=='completed'
        print(json.dumps({'suite':'public-sdk-smoke-v1','transport':'real-loopback-http','status':'passed','model':'offline-development-v1'}))
    finally:
        server.should_exit=True;thread.join(5);sock.close();engine.dispose()
        if thread.is_alive():raise RuntimeError('Local API shutdown failed')

if __name__=='__main__':run()
