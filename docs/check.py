"""Check local Markdown targets, Python examples and genuine screenshot artifacts."""
import ast,re,struct
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
files=[ROOT/'README.md',*sorted((ROOT/'docs').glob('*.md'))]
for path in files:
    content=path.read_text()
    for label,target in re.findall(r'!?\[([^\]]*)\]\(([^)]+)\)',content):
        if target.startswith(('https://','http://','#')):continue
        target=target.split('#',1)[0]
        assert (path.parent/target).resolve().exists(),f'{path.name}: missing {target}'
    for example in re.findall(r'```python\n(.*?)```',content,re.S):ast.parse(example)
for name in ['review.png','memory.png']:
    path=ROOT/'docs'/'screenshots'/name;data=path.read_bytes()
    assert data[:8]==b'\x89PNG\r\n\x1a\n'
    width,height=struct.unpack('>II',data[16:24])
    assert width>=1000 and height>=300,(name,width,height)
    assert len(data)>10000
    print(f'{name}: {width}x{height}, {len(data)} bytes')
for path in (ROOT/'docs').glob('*.py'):ast.parse(path.read_text())
assert len((ROOT/'README.md').read_text().splitlines())<=55
print(f'{len(files)} Markdown files: links, Python examples and screenshot artifacts verified.')
