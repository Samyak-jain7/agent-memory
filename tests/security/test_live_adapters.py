"""Synthetic credentials and mocked HTTP only; never calls either provider."""
import json
from datetime import datetime,timedelta,timezone
import httpx
import pytest
from model_gateway import Candidate,OfflineProvider,provider_from_env,extraction_provider_from_env
from model_gateway.live import GeminiExtractor,JevVerifier,GEMINI_MODEL,JEV_MODEL,ProviderConfigurationError,ProviderContractError,ProviderQuotaError,ProviderRequestError

@pytest.fixture
def free_reviews(tmp_path,monkeypatch):
    paths={}
    for provider,model,prefix,key in [('gemini',GEMINI_MODEL,'GEMINI','GEMINI_API_KEY'),('typesafe',JEV_MODEL,'TYPESAFE','TYPESAFE_API_KEY')]:
        now=datetime.now(timezone.utc)
        review={'provider':provider,'model':model,'account_id':'synthetic-test-account','billing_enabled':False,'remaining_requests':20,'verified_at':now.isoformat(),'expires_at':(now+timedelta(hours=1)).isoformat(),'evidence':'synthetic fixture; not actual account approval'}
        path=tmp_path/(provider+'.json');path.write_text(json.dumps(review));paths[provider]=path
        monkeypatch.setenv(prefix+'_FREE_TIER_FILE',str(path));monkeypatch.setenv(key,'synthetic-test-key')
    monkeypatch.delenv('EXTRACTION_MODEL',raising=False);monkeypatch.delenv('JEV_MODEL',raising=False)
    monkeypatch.setenv('MODEL_PROVIDER','offline');monkeypatch.delenv('MEMORY_VERIFIER',raising=False)
    return paths

def gemini_response(candidates=None,finish='STOP'):
    return {'candidates':[{'finishReason':finish,'content':{'parts':[{'text':json.dumps({'candidates':candidates if candidates is not None else [{'kind':'profile','content':'I prefer tea','confidence':.9,'importance':.5}]})}]}}],'usageMetadata':{'promptTokenCount':10,'candidatesTokenCount':20,'totalTokenCount':30}}

def jev_response(request,override=None):
    body=json.loads(request.content)
    return {'model':JEV_MODEL,'answers':{key:{'type':'noul','noul':(override or {}).get(key,1.0)} for key in body['questions']},'usage':{'input_tokens':30,'output_tokens':10}}

def test_gemini_request_schema_header_model_and_usage(free_reviews):
    def respond(request):
        assert str(request.url)=='https://generativelanguage.googleapis.com/v1beta/models/'+GEMINI_MODEL+':generateContent'
        assert request.headers['x-goog-api-key']=='synthetic-test-key' and 'key=' not in str(request.url)
        body=json.loads(request.content);schema=body['generationConfig']['responseJsonSchema']
        assert schema['additionalProperties'] is False and schema['properties']['candidates']['maxItems']==16
        assert body['generationConfig']['responseMimeType']=='application/json'
        assert 'untrusted' in body['systemInstruction']['parts'][0]['text']
        assert not {'tools','cachedContent'} & set(body)
        response=gemini_response();response['candidates'][0]['content']['parts'][0]['thoughtSignature']='opaque-metadata'
        response['candidates'][0]['content']['parts'].insert(0,{'thought':True,'text':'internal reasoning ignored'})
        return httpx.Response(200,json=response)
    provider=GeminiExtractor(httpx.MockTransport(respond));result=provider.extract([{'role':'user','content':'I prefer tea'}])
    assert result[0].content=='I prefer tea' and provider.usage=={'prompt_tokens':10,'completion_tokens':20,'total_tokens':30}

@pytest.mark.parametrize('change',['blocked','truncated','unknown-field','wrong-type','too-many','missing-score','empty-response','duplicate-json','nan'])
def test_gemini_malformed_or_incomplete_output_rejected(free_reviews,change):
    response=gemini_response();candidate={'kind':'profile','content':'I prefer tea','confidence':.9,'importance':.5}
    if change=='blocked':response={'promptFeedback':{'blockReason':'SAFETY'}}
    if change=='truncated':response['candidates'][0]['finishReason']='MAX_TOKENS'
    if change=='empty-response':response={'candidates':[]}
    if change in {'unknown-field','wrong-type','too-many','missing-score'}:
        if change=='unknown-field':candidate['origin']='inferred'
        if change=='wrong-type':candidate['confidence']='0.9'
        if change=='missing-score':del candidate['confidence']
        response=gemini_response([candidate]*17 if change=='too-many' else [candidate])
    if change=='duplicate-json':response['candidates'][0]['content']['parts'][0]['text']='{"candidates":[],"candidates":[]}'
    if change=='nan':response['candidates'][0]['content']['parts'][0]['text']='{"candidates":[{"kind":"profile","content":"I prefer tea","confidence":NaN,"importance":0.5}]}'
    provider=GeminiExtractor(httpx.MockTransport(lambda r:httpx.Response(200,json=response)))
    with pytest.raises(ProviderContractError):provider.extract([{'role':'user','content':'I prefer tea'}])
    assert provider.usage=={}

@pytest.mark.parametrize('change',['paid','zero-billing','zero-quota','stale','model','provider','extra','missing'])
def test_free_review_rejects_unverified_billed_or_stale_config_without_request(free_reviews,change):
    path=free_reviews['gemini'];document=json.loads(path.read_text())
    if change=='paid':document['billing_enabled']=True
    if change=='zero-billing':document['billing_enabled']=0
    if change=='zero-quota':document['remaining_requests']=0
    if change=='stale':document['expires_at']='2020-01-01T00:00:00Z'
    if change=='model':document['model']='gemini-3.8-flash'
    if change=='provider':document['provider']='typesafe'
    if change=='extra':document['unknown']=True
    if change=='missing':path.unlink()
    else:path.write_text(json.dumps(document))
    def forbidden(request):raise AssertionError('must not transmit')
    provider=GeminiExtractor(httpx.MockTransport(forbidden))
    with pytest.raises(ProviderConfigurationError):provider.extract([{'role':'user','content':'I prefer tea'}])

@pytest.mark.parametrize('status',[402,429])
def test_quota_failure_latches_and_never_falls_back(free_reviews,status):
    calls=[]
    provider=GeminiExtractor(httpx.MockTransport(lambda request:(calls.append(request),httpx.Response(status,json={'error':'untrusted secret payload'}))[1]))
    for _ in range(2):
        with pytest.raises(ProviderQuotaError,match='quota'):provider.extract([{'role':'user','content':'I prefer tea'}])
    assert len(calls)==1

@pytest.mark.parametrize('status',[302,401,500])
def test_provider_http_errors_are_sanitized_and_not_redirected(free_reviews,status):
    calls=[]
    provider=GeminiExtractor(httpx.MockTransport(lambda r:(calls.append(r),httpx.Response(status,headers={'Location':'https://untrusted.example'},json={'error':'synthetic-test-key raw-personal-message'}))[1]))
    with pytest.raises(ProviderRequestError) as error:provider.extract([{'role':'user','content':'I prefer tea'}])
    assert 'synthetic-test-key' not in str(error.value) and 'raw-personal-message' not in str(error.value)
    assert len(calls)==1

def test_jev_checks_each_candidate_against_source_and_rejects_uncertain(free_reviews):
    def respond(request):
        body=json.loads(request.content)
        assert str(request.url)=='https://api.typesafe.ai/v1/systemone'
        assert request.headers['Authorization']=='Bearer synthetic-test-key'
        assert body['model']==JEV_MODEL and len(body['questions'])==6
        assert all(q['type']=='noul' for q in body['questions'].values())
        return httpx.Response(200,json=jev_response(request,{'c1_supported':.7}))
    verifier=JevVerifier(httpx.MockTransport(respond))
    candidates=[Candidate(content='I prefer tea'),Candidate(content='I live in Paris')]
    accepted,outcomes=verifier.verify([{'role':'user','content':'I prefer tea'}],candidates)
    assert accepted==candidates[:1] and outcomes==[{'index':1,'stage':'verification','outcome':'jev_verification_rejected'}]
    assert verifier.usage['total_tokens']==40

@pytest.mark.parametrize('change',['missing-answer','extra-answer','wrong-model','bad-score','wrong-type'])
def test_jev_malformed_decisions_fail_closed(free_reviews,change):
    def respond(request):
        response=jev_response(request)
        if change=='missing-answer':del response['answers']['c0_supported']
        if change=='extra-answer':response['answers']['unexpected']={'type':'noul','noul':1}
        if change=='wrong-model':response['model']='jev-other'
        if change=='bad-score':response['answers']['c0_supported']['noul']=1.1
        if change=='wrong-type':response['answers']['c0_supported']['noul']=True
        return httpx.Response(200,json=response)
    verifier=JevVerifier(httpx.MockTransport(respond))
    with pytest.raises(ProviderContractError):verifier.verify([{'role':'user','content':'I prefer tea'}],[Candidate(content='I prefer tea')])

def test_empty_extraction_and_verification_never_call_provider(free_reviews):
    transport=httpx.MockTransport(lambda r:(_ for _ in ()).throw(AssertionError('must not call')))
    assert GeminiExtractor(transport).extract([])==[]
    assert JevVerifier(transport).verify([],[])==([],[])

def test_extraction_selection_does_not_change_embedding_provider(free_reviews,monkeypatch):
    monkeypatch.setenv('EXTRACTION_PROVIDER','gemini')
    assert isinstance(extraction_provider_from_env(),GeminiExtractor)
    assert isinstance(provider_from_env(),OfflineProvider)


def test_mocked_gemini_jev_worker_persists_only_verified_provenance(engine,identities,free_reviews):
    from sqlalchemy import text
    from apps.worker.main import Worker
    from persistence.repositories import Repository
    from test_worker import capture
    from apps.api.auth import Principal
    source=capture(engine,identities)
    response=gemini_response([{'kind':'profile','content':'I prefer tea','confidence':.9,'importance':.5},{'kind':'profile','content':'I live in Paris','confidence':.9,'importance':.5}])
    extractor=GeminiExtractor(httpx.MockTransport(lambda r:httpx.Response(200,json=response)))
    verifier=JevVerifier(httpx.MockTransport(lambda r:httpx.Response(200,json=jev_response(r,{'c1_supported':.1}))))
    repo=Repository(engine);assert Worker(repo,extractor,verifier=verifier).run_once(identities['tenant_a'])
    principal=Principal(identities['tenant_a'],identities['actor_a'])
    memories,_=repo.list_memories(principal,identities['subject_a'],'mocked-live')
    assert len(memories)==1 and memories[0].content=='I prefer tea' and memories[0].source_episode_ids==[source.episode.id]
    status=repo.job_status(principal,source.job.id,'mocked-live-status')
    assert status['state']=='succeeded' and any(o['outcome']=='jev_verification_rejected' for o in status['outcomes'])
    with engine.connect() as c:assert c.execute(text("SELECT count(*) FROM memory_versions WHERE origin='observed'")).scalar_one()==1


def test_revocation_between_extract_and_verify_never_transmits_to_jev(engine,identities,free_reviews):
    from sqlalchemy import text
    from apps.worker.main import Worker
    from persistence.repositories import Repository
    from test_worker import capture
    capture(engine,identities)
    def extract(request):
        with engine.begin() as c:c.execute(text('UPDATE subjects SET memory_consent=false'))
        return httpx.Response(200,json=gemini_response())
    extractor=GeminiExtractor(httpx.MockTransport(extract))
    verifier=JevVerifier(httpx.MockTransport(lambda r:(_ for _ in ()).throw(AssertionError('consent revoked; no Jev transmission'))))
    Worker(Repository(engine),extractor,verifier=verifier).run_once(identities['tenant_a'])
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==0
        assert c.execute(text('SELECT error_code FROM formation_jobs')).scalar_one() is None


def test_sensitive_extracted_candidate_never_reaches_jev(engine,identities,free_reviews):
    from sqlalchemy import text
    from apps.worker.main import Worker
    from persistence.repositories import Repository
    from test_worker import capture
    capture(engine,identities)
    response=gemini_response([{'kind':'profile','content':'password=synthetic-sensitive-value','confidence':.9,'importance':.5}])
    extractor=GeminiExtractor(httpx.MockTransport(lambda r:httpx.Response(200,json=response)))
    verifier=JevVerifier(httpx.MockTransport(lambda r:(_ for _ in ()).throw(AssertionError('sensitive output must not transmit'))))
    Worker(Repository(engine),extractor,verifier=verifier).run_once(identities['tenant_a'])
    with engine.connect() as c:
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==0
        assert 'synthetic-sensitive-value' not in str(c.execute(text('SELECT outcomes FROM formation_jobs')).scalar_one())


def test_worker_does_not_retry_free_quota_failure(engine,identities,free_reviews):
    from sqlalchemy import text
    from apps.worker.main import Worker
    from persistence.repositories import Repository
    from test_worker import capture
    capture(engine,identities)
    extractor=GeminiExtractor(httpx.MockTransport(lambda r:httpx.Response(429,json={'error':'private-provider-message'})))
    Worker(Repository(engine),extractor).run_once(identities['tenant_a'])
    with engine.connect() as c:
        state,error=c.execute(text('SELECT state,error_code FROM formation_jobs')).one()
        assert state=='dead_letter' and error=='free_quota_unavailable'
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==0


def test_slow_stream_has_total_deadline_and_is_closed(free_reviews,monkeypatch):
    import asyncio,time
    import model_gateway.live as live
    closed=[]
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(.01)
                yield b' '
        async def aclose(self):closed.append(True)
    monkeypatch.setattr(live,'REQUEST_DEADLINE_SECONDS',.05)
    provider=GeminiExtractor(httpx.MockTransport(lambda r:httpx.Response(200,stream=SlowStream())))
    started=time.monotonic()
    with pytest.raises(ProviderRequestError,match='deadline'):provider.extract([{'role':'user','content':'I prefer tea'}])
    assert time.monotonic()-started<.5 and closed


def test_deadline_failure_retries_safely_without_memory_or_payload(engine,identities,free_reviews,monkeypatch):
    import asyncio
    from sqlalchemy import text
    from apps.worker.main import Worker
    from persistence.repositories import Repository
    from test_worker import capture
    import model_gateway.live as live
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(.01)
                yield b'private-provider-message'
        async def aclose(self):pass
    monkeypatch.setattr(live,'REQUEST_DEADLINE_SECONDS',.05)
    capture(engine,identities)
    provider=GeminiExtractor(httpx.MockTransport(lambda r:httpx.Response(200,stream=SlowStream())))
    Worker(Repository(engine),provider).run_once(identities['tenant_a'])
    with engine.connect() as c:
        state,error=c.execute(text('SELECT state,error_code FROM formation_jobs')).one()
        assert state=='retryable_failure' and error=='ProviderRequestError'
        assert c.execute(text('SELECT count(*) FROM memories')).scalar_one()==0
        assert 'private-provider-message' not in str(c.execute(text('SELECT outcomes FROM formation_jobs')).scalar_one())
