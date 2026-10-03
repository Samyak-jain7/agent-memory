import os,stat,base64
from pathlib import Path
import pytest
from scripts.encrypted_backup import encrypt_backup,decrypt_backup,write_artifact,key_from_environment,MAGIC

def test_encrypted_backup_roundtrip_and_random_nonces(tmp_path):
    data=b'synthetic private episode data';key=os.urandom(32);metadata={'deployment_id':'test-only','release_sha':'0'*40}
    first=encrypt_backup(data,key,metadata);second=encrypt_backup(data,key,metadata)
    assert first!=second and data not in first
    plaintext,manifest=decrypt_backup(first,key);assert plaintext==data and manifest['deployment_id']=='test-only'
    path=tmp_path/'backup.ambak';receipt=write_artifact(path,first)
    assert receipt['bytes']==len(first) and stat.S_IMODE(path.stat().st_mode)==0o600
    assert list(tmp_path.iterdir())==[path]
    with pytest.raises(FileExistsError):write_artifact(path,second)

@pytest.mark.parametrize('where',['header','nonce','ciphertext','truncated','key'])
def test_backup_tamper_wrong_key_and_truncation_fail_closed(where):
    key=os.urandom(32);artifact=encrypt_backup(b'synthetic private payload',key,{})
    if where=='key':key=os.urandom(32)
    elif where=='truncated':artifact=artifact[:-20]
    else:
        index={'header':len(MAGIC)+6,'nonce':-20,'ciphertext':-1}[where]
        changed=bytearray(artifact);changed[index]^=1;artifact=bytes(changed)
    with pytest.raises(ValueError):decrypt_backup(artifact,key)

def test_no_symlink_overwrite_or_key_logging(tmp_path,monkeypatch,capsys):
    target=tmp_path/'target';target.write_bytes(b'existing');link=tmp_path/'link';link.symlink_to(target)
    with pytest.raises(FileExistsError):write_artifact(link,b'artifact')
    assert target.read_bytes()==b'existing'
    monkeypatch.setenv('BACKUP_ENCRYPTION_KEY','invalid-key')
    with pytest.raises(ValueError):key_from_environment()
    value=base64.b64encode(os.urandom(32)).decode();monkeypatch.setenv('BACKUP_ENCRYPTION_KEY',value)
    assert len(key_from_environment())==32 and value not in ''.join(capsys.readouterr())


def test_dump_subprocess_deadline_and_secret_free_arguments(tmp_path,monkeypatch):
    import scripts.encrypted_backup as backup
    executable=tmp_path/'pg_dump'
    executable.write_text("#!/usr/bin/env python3\nimport os,sys\nassert 'synthetic-db-password' not in str(sys.argv)\nassert os.environ['PGPASSWORD']=='synthetic-db-password'\nsys.stdout.buffer.write(b'synthetic dump')\n")
    executable.chmod(0o700);monkeypatch.setenv('PATH',str(tmp_path)+os.pathsep+os.environ['PATH'])
    assert backup.dump_database('postgresql+psycopg://postgres:synthetic-db-password@127.0.0.1/memory_test')==b'synthetic dump'
    executable.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(5)\n')
    monkeypatch.setattr(backup,'DUMP_TIMEOUT_SECONDS',.1)
    with pytest.raises(RuntimeError,match='deadline'):backup.dump_database('postgresql+psycopg://postgres@127.0.0.1/memory_test')


def test_backup_size_bound_and_authoritative_manifest(monkeypatch):
    import scripts.encrypted_backup as backup
    key=os.urandom(32);monkeypatch.setattr(backup,'MAX_BYTES',8)
    with pytest.raises(ValueError):backup.encrypt_backup(b'0123456789',key,{})
    artifact=backup.encrypt_backup(b'data',key,{'format':0,'cipher':'invalid','plaintext_sha256':'invalid'})
    assert backup.decrypt_backup(artifact,key)[0]==b'data'
