"""Fail closed until production approvals AND deployment evidence exist."""
import json,math,re,hashlib
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlparse
REQUIRED=['latency_p95_ms','throughput_rps','availability','retention_seconds','erasure_seconds','backup_retention_seconds','extraction_precision','retrieval_relevance','citation_correctness','correction_rate']

def finite_number(value):
    try:return math.isfinite(value)
    except (TypeError,OverflowError):return False

def validate(config,evidence_directory=None):
    errors=[]
    if not isinstance(config,dict):return ['configuration must be an object; approved targets and encryption evidence missing']
    for key in ['approval','targets','observed']:
        if not isinstance(config.get(key,{}),dict):return ['invalid configuration object: '+key]
    if not isinstance(config.get('public_api_url',''),str):return ['invalid public HTTPS URL']
    allowed={'mode','approval','targets','observed','public_api_url','database_sslmode','deployment_id','release_sha','evidence_receipts','storage_encryption_verified','backup_encryption_verified','restore_verified','live_provider_evaluated','telemetry_export_verified','storage_encryption_evidence','backup_encryption_evidence','provider_data_policy','retention_policy','erasure_policy','backup_expiry_policy','deployment_evidence'}
    if set(config)-allowed:errors.append('Unknown readiness fields')
    errors.extend(validate_receipts(config,evidence_directory))
    if config.get('mode')!='production':errors.append('production configuration required')
    approval=config.get('approval',{})
    if not all(isinstance(approval.get(k),str) and approval[k].strip() for k in ['approved_by','approved_at','evidence']):errors.append('human approval evidence missing')
    try:
        if set(approval)!={'approved_by','approved_at','evidence'}:raise ValueError()
        approved=datetime.fromisoformat(approval['approved_at'].replace('Z','+00:00'))
        if approved.tzinfo is None or approved>datetime.now(timezone.utc):raise ValueError()
    except (ValueError,KeyError,AttributeError,TypeError):errors.append('Valid approval timestamp and fields required')
    targets=config.get('targets',{});observed=config.get('observed',{})
    if set(targets)!=set(REQUIRED) or set(observed)!=set(REQUIRED):errors.append('Exact approved target and measured value fields required')
    for name in REQUIRED:
        target=targets.get(name);actual=observed.get(name)
        if isinstance(target,bool) or not isinstance(target,(int,float)) or not finite_number(target) or target<0:errors.append('approved target missing or invalid: '+name);continue
        if isinstance(actual,bool) or not isinstance(actual,(int,float)) or not finite_number(actual) or actual<0:errors.append('measured value missing or invalid: '+name);continue
        minimum=name in {'throughput_rps','availability','extraction_precision','retrieval_relevance','citation_correctness'}
        if name in {'retention_seconds','backup_retention_seconds'}:
            if actual!=target:errors.append('policy mismatch: '+name)
        elif (minimum and actual<target) or (not minimum and actual>target):errors.append('target unmet: '+name)
        if name in {'latency_p95_ms','throughput_rps','availability','erasure_seconds'} and target<=0:errors.append('positive target required: '+name)
        if name in {'availability','extraction_precision','retrieval_relevance','citation_correctness','correction_rate'} and (target>1 or actual>1):errors.append('fraction exceeds one: '+name)
    try:
        parsed=urlparse(config.get('public_api_url',''))
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:raise ValueError()
        parsed.port
    except ValueError:errors.append('public HTTPS evidence missing')
    if config.get('database_sslmode')!='verify-full':errors.append('verified database TLS required')
    for key in ['storage_encryption_verified','backup_encryption_verified','restore_verified','live_provider_evaluated','telemetry_export_verified']:
        if config.get(key) is not True:errors.append('deployment evidence missing: '+key)
    for key in ['storage_encryption_evidence','backup_encryption_evidence','provider_data_policy','retention_policy','erasure_policy','backup_expiry_policy','deployment_evidence']:
        if not isinstance(config.get(key),str) or not config[key].strip():errors.append('approval/evidence missing: '+key)
    return errors

def enforce_environment(database_url):
    import os
    from sqlalchemy import create_engine,text
    from sqlalchemy.engine import make_url
    environment=os.environ.get('MEMORY_ENV','development')
    if environment=='development':return
    if environment!='production':raise RuntimeError('Unknown MEMORY_ENV; expected development or production')
    path=os.environ.get('PRODUCTION_READINESS_FILE')
    if not path:raise RuntimeError('Production readiness approval file required')
    config=load_config(Path(path));errors=validate(config,Path(path).resolve().parent)
    if errors:raise RuntimeError('Production readiness blocked: '+'; '.join(errors))
    if os.environ.get('MEMORY_RELEASE_SHA')!=config.get('release_sha') or os.environ.get('MEMORY_DEPLOYMENT_ID')!=config.get('deployment_id'):errors.append('Runtime release and deployment must match reviewed evidence')
    if make_url(database_url).query.get('sslmode')!='verify-full':errors.append('Runtime database URL requires verify-full TLS')
    if os.environ.get('MODEL_PROVIDER','offline')=='offline':errors.append('Live evaluated provider required')
    if len(os.environ.get('MEMORY_AUTH_SECRET',''))<32:errors.append('Production signing secret must be at least 32 characters')
    if os.environ.get('MEMORY_TELEMETRY')!='console':errors.append('Reviewed telemetry configuration required')
    try:
        if int(os.environ.get('ERASURE_RETENTION_SECONDS','-1'))!=config.get('observed',{}).get('retention_seconds'):errors.append('Runtime retention does not match verified policy')
    except ValueError:errors.append('Invalid runtime retention')
    if errors:raise RuntimeError('Production readiness blocked: '+'; '.join(errors))
    engine=create_engine(database_url)
    try:
        with engine.connect() as c:
            if c.execute(text('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')).scalar_one_or_none() is not True:raise RuntimeError('Database TLS is not active')
            validate_runtime_role(c)
    finally:engine.dispose()


def load_config(path):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('duplicate field')
            result[key]=value
        return result
    try:
        if path.stat().st_size>65536:raise ValueError('oversized file')
        return json.loads(path.read_text(),object_pairs_hook=unique,parse_constant=lambda value: (_ for _ in ()).throw(ValueError('nonfinite number')))
    except (OSError,ValueError):raise RuntimeError('Invalid production readiness document') from None

def validate_receipts(config,evidence_directory=None):
    if not isinstance(config,dict):return ['Release-bound evidence required']
    deployment=config.get('deployment_id');release=config.get('release_sha')
    if not isinstance(deployment,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}',deployment):return ['Deployment identity required']
    if not isinstance(release,str) or not re.fullmatch(r'[0-9a-f]{40}',release):return ['Release identity required']
    receipts=config.get('evidence_receipts')
    kinds={'storage','backup','restore','provider','telemetry','retention','erasure','backup_expiry','deployment','approval'}
    if not isinstance(receipts,dict) or set(receipts)!=kinds:return ['Complete release-bound evidence receipts required']
    errors=[];now=datetime.now(timezone.utc)
    for kind,receipt in receipts.items():
        try:
            if not isinstance(receipt,dict) or set(receipt)!={'deployment_id','release_sha','verified_at','artifact_sha256','artifact_path','provenance'}:raise ValueError()
            if receipt['deployment_id']!=deployment or receipt['release_sha']!=release:raise ValueError()
            if not isinstance(receipt['artifact_sha256'],str) or not re.fullmatch(r'[0-9a-f]{64}',receipt['artifact_sha256']):raise ValueError()
            if not isinstance(receipt['provenance'],str) or not receipt['provenance'].strip():raise ValueError()
            relative=Path(receipt['artifact_path'])
            if relative.is_absolute() or '..' in relative.parts or not relative.parts:raise ValueError()
            if evidence_directory is not None:
                base=Path(evidence_directory).resolve();artifact=(base/relative).resolve()
                if not artifact.is_relative_to(base) or artifact.stat().st_size>1048576:raise ValueError()
                if hashlib.sha256(artifact.read_bytes()).hexdigest()!=receipt['artifact_sha256']:raise ValueError()
            timestamp=datetime.fromisoformat(receipt['verified_at'].replace('Z','+00:00'))
            if timestamp.tzinfo is None or not 0<=(now-timestamp).total_seconds()<=604800:raise ValueError()
        except (OSError,ValueError,TypeError,AttributeError):errors.append('Invalid or stale evidence receipt: '+kind)
    return errors

def validate_application_role(connection,allow_missing=False):
    from sqlalchemy import text
    row=connection.execute(text("SELECT oid,rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication,rolcanlogin FROM pg_roles WHERE rolname='memory_app'")).first()
    if row is None:
        if allow_missing:return
        raise RuntimeError('Restricted application role missing')
    if any(row[1:]):raise RuntimeError('Unsafe application role attributes')
    if connection.execute(text('SELECT EXISTS(SELECT 1 FROM pg_auth_members WHERE member=:oid)'),{'oid':row[0]}).scalar_one():raise RuntimeError('Application role must have no role memberships')
    if connection.execute(text("SELECT EXISTS(SELECT 1 FROM pg_class WHERE relowner=:oid AND relnamespace=(SELECT oid FROM pg_namespace WHERE nspname='public')) OR EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='public' AND nspowner=:oid) OR EXISTS(SELECT 1 FROM pg_database WHERE datname=current_database() AND datdba=:oid) OR EXISTS(SELECT 1 FROM pg_proc WHERE proowner=:oid AND pronamespace=(SELECT oid FROM pg_namespace WHERE nspname='public'))"),{'oid':row[0]}).scalar_one():raise RuntimeError('Application role must not own protected objects')

def validate_runtime_role(connection):
    from sqlalchemy import text
    validate_application_role(connection)
    roles=connection.execute(text("WITH RECURSIVE reachable AS (SELECT oid FROM pg_roles WHERE rolname=current_user UNION SELECT m.roleid FROM pg_auth_members m JOIN reachable r ON m.member=r.oid) SELECT p.oid,p.rolname,p.rolsuper,p.rolbypassrls,p.rolcreaterole,p.rolcreatedb,p.rolreplication FROM pg_roles p JOIN reachable r ON r.oid=p.oid")).all()
    if len(roles)!=2 or 'memory_app' not in {r[1] for r in roles} or any(any(r[2:]) for r in roles):raise RuntimeError('Unsafe effective runtime role privileges')
    if connection.execute(text('SELECT rolinherit FROM pg_roles WHERE rolname=current_user')).scalar_one():raise RuntimeError('Runtime role must use explicit role switching')
    for role in roles:
        if connection.execute(text("SELECT EXISTS(SELECT 1 FROM pg_class WHERE relowner=:oid AND relnamespace=(SELECT oid FROM pg_namespace WHERE nspname='public')) OR EXISTS(SELECT 1 FROM pg_namespace WHERE nspname='public' AND nspowner=:oid) OR EXISTS(SELECT 1 FROM pg_database WHERE datname=current_database() AND datdba=:oid) OR EXISTS(SELECT 1 FROM pg_proc WHERE proowner=:oid AND pronamespace=(SELECT oid FROM pg_namespace WHERE nspname='public'))"),{'oid':role[0]}).scalar_one():raise RuntimeError('Runtime roles must not own protected objects')

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('config',type=Path);args=parser.parse_args()
    config=load_config(args.config)
    errors=validate(config,args.config.resolve().parent)
    print(json.dumps({'production_ready':not errors,'blockers':errors},indent=2));raise SystemExit(bool(errors))
