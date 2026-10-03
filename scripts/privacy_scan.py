"""Scan publishable working files and Git history without printing matched values."""
import re,subprocess
from pathlib import Path
PATTERNS=[rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',rb'gh[pousr]_[A-Za-z0-9]{32,}',rb'github_pat_[A-Za-z0-9_]{30,}',rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{24,}']

def scan():
    findings=[]
    paths=subprocess.check_output(['git','ls-files','-z','--cached','--others','--exclude-standard']).split(b'\0')
    for raw in paths:
        if not raw:continue
        path=Path(raw.decode())
        if path.name.startswith('.env') and path.name!='.env.example':findings.append(str(path)+': credential file')
        if path.is_file() and any(re.search(pattern,path.read_bytes()) for pattern in PATTERNS):findings.append(str(path)+': credential pattern')
    for line in subprocess.check_output(['git','rev-list','--objects','HEAD']).splitlines():
        parts=line.split(b' ',1)
        if len(parts)<2:continue
        oid,path=parts;kind=subprocess.check_output(['git','cat-file','-t',oid]).strip()
        if kind!=b'blob':continue
        name=Path(path.decode(errors='replace'))
        if name.name.startswith('.env') and name.name!='.env.example':findings.append(str(name)+': credential file in history')
        blob=subprocess.check_output(['git','cat-file','blob',oid])
        if any(re.search(pattern,blob) for pattern in PATTERNS):findings.append(str(name)+': credential pattern in history')
    if findings:
        for finding in sorted(set(findings)):print(finding)
        return False
    print('Publishable files and reachable history: no credential patterns or tracked environment secrets detected.');return True
if __name__=='__main__':raise SystemExit(not scan())
