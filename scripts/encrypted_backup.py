"""Authenticated backup artifacts. Decrypted bytes never enter a plaintext file."""
import base64,hashlib,json,os,struct,subprocess
from datetime import datetime,timezone
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
MAGIC=b'AMBACKUP1'
MAX_BYTES=256*1024*1024
MAX_HEADER=4096
DUMP_TIMEOUT_SECONDS=120

def key_from_environment():
    try:key=base64.b64decode(os.environ['BACKUP_ENCRYPTION_KEY'],validate=True)
    except (KeyError,ValueError):raise ValueError('A secure base64-encoded 32-byte backup key is required') from None
    if len(key)!=32:raise ValueError('Backup key must contain exactly 32 bytes')
    return key

def encrypt_backup(data,key,metadata):
    if len(key)!=32 or not isinstance(data,bytes) or len(data)>MAX_BYTES:raise ValueError('Invalid backup key or backup exceeds the 256 MiB in-memory limit')
    # ponytail: bounded in-memory encryption; use an approved streaming backup service for larger datasets.
    header=json.dumps({**metadata,'format':1,'cipher':'AES-256-GCM','plaintext_sha256':hashlib.sha256(data).hexdigest()},sort_keys=True,separators=(',',':')).encode()
    if len(header)>MAX_HEADER:raise ValueError('Backup metadata exceeds limit')
    nonce=os.urandom(12);aad=MAGIC+struct.pack('>I',len(header))+header
    return aad+nonce+AESGCM(key).encrypt(nonce,data,aad)

def decrypt_backup(artifact,key):
    if len(key)!=32 or not isinstance(artifact,bytes) or len(artifact)>MAX_BYTES+MAX_HEADER+64:raise ValueError('Invalid backup key or oversized artifact')
    if not artifact.startswith(MAGIC) or len(artifact)<len(MAGIC)+4+12+16:raise ValueError('Invalid backup artifact')
    size=struct.unpack('>I',artifact[len(MAGIC):len(MAGIC)+4])[0]
    offset=len(MAGIC)+4+size
    if size>MAX_HEADER or offset+28>len(artifact):raise ValueError('Invalid backup metadata')
    aad=artifact[:offset]
    try:
        plaintext=AESGCM(key).decrypt(artifact[offset:offset+12],artifact[offset+12:],aad)
        metadata=json.loads(artifact[len(MAGIC)+4:offset])
    except (InvalidTag,ValueError):raise ValueError('Backup authentication failed') from None
    if not isinstance(metadata,dict) or metadata.get('format')!=1 or metadata.get('cipher')!='AES-256-GCM' or metadata.get('plaintext_sha256')!=hashlib.sha256(plaintext).hexdigest():raise ValueError('Invalid authenticated backup manifest')
    return plaintext,metadata

def write_artifact(path,artifact):
    # Exclusive create rejects symlinks and existing artifacts; never overwrite a backup.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(artifact);stream.flush();os.fsync(stream.fileno())
    except BaseException:
        Path(path).unlink(missing_ok=True);raise
    return {'artifact_sha256':hashlib.sha256(artifact).hexdigest(),'bytes':len(artifact)}

def dump_database(database_url):
    from sqlalchemy.engine import make_url
    url=make_url(database_url)
    if url.drivername not in {'postgresql','postgresql+psycopg'} or not url.database:raise ValueError('PostgreSQL URL required')
    env=os.environ.copy()
    for name in ['PGSERVICE','PGSERVICEFILE','PGPASSFILE','PGOPTIONS','PGHOSTADDR']:env.pop(name,None)
    env.update(PGHOST=url.host or '127.0.0.1',PGPORT=str(url.port or 5432),PGUSER=url.username or '',PGDATABASE=url.database,PGPASSWORD=url.password or '',PGSSLMODE=url.query.get('sslmode','prefer'))
    if url.query.get('sslrootcert'):env['PGSSLROOTCERT']=url.query['sslrootcert']
    if os.environ.get('MEMORY_ENV')=='production' and env['PGSSLMODE']!='verify-full':raise ValueError('Production backup database TLS must use verify-full')
    # Credentials remain in subprocess environment, never command arguments or error output.
    process=subprocess.Popen(['pg_dump','--no-owner'],stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env)
    try:
        import selectors,time
        data=bytearray();deadline=time.monotonic()+DUMP_TIMEOUT_SECONDS
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise RuntimeError('Database backup deadline exceeded')
                if not selector.select(remaining):raise RuntimeError('Database backup deadline exceeded')
                chunk=os.read(process.stdout.fileno(),min(65536,MAX_BYTES+1-len(data)))
                if not chunk:break
                data.extend(chunk)
                if len(data)>MAX_BYTES:raise ValueError('Backup exceeds the 256 MiB in-memory limit')
        if process.wait(timeout=max(.01,deadline-time.monotonic()))!=0:raise RuntimeError('Database backup command failed')
        return bytes(data)
    finally:
        if process.poll() is None:process.kill();process.wait()
        process.stdout.close()

def main():
    import argparse,re
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True,type=Path);parser.add_argument('--deployment-id',required=True);parser.add_argument('--release-sha',required=True);parser.add_argument('--retention-seconds',required=True,type=int);args=parser.parse_args()
    if not re.fullmatch(r'[a-zA-Z0-9_.-]{1,100}',args.deployment_id) or not re.fullmatch(r'[0-9a-f]{40}',args.release_sha) or args.retention_seconds<=0:parser.error('Valid deployment/release identity and explicitly approved positive backup retention required')
    key=key_from_environment();now=datetime.now(timezone.utc)
    from datetime import timedelta
    metadata={'deployment_id':args.deployment_id,'release_sha':args.release_sha,'created_at':now.isoformat(),'expires_at':(now+timedelta(seconds=args.retention_seconds)).isoformat()}
    artifact=encrypt_backup(dump_database(os.environ['ADMIN_DATABASE_URL']),key,metadata)
    result=write_artifact(args.output,artifact)
    print(json.dumps({'status':'encrypted','cipher':'AES-256-GCM',**result,'storage_encryption_verified':False,'expiry_enforced':False}))

if __name__=='__main__':main()
