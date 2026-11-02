"""One-shot plaintext source checkpoint. No app/runtime/financial execution."""
from pathlib import Path
import hashlib
import subprocess

STAGE = Path('docs/operations/OP-UNIFIED-BALANCES-20261102/external-source-stage')
FILES = [
('backend/operational_external_advances.py', None, '5df5b315c2337dbfa6998088fcec6bd6011b9aba'),
('backend/operational_balance_service.py', 'be346d980cbbcecdb5f5d0e3863214b8a13d706a', '89e45890682920dd0fa978dbc8680864bd607633'),
('backend/operational_balance_routes.py', '28e96faf069107b99d1dba92899c702bd22287a0', 'a0b1d62a01921989a72586519bed1f2266f07fbd'),
('backend/tests/test_operational_balance_external_advances.py', None, 'e5f55f703b9751094c8a160799a0a9344511475e'),
('frontend/src/pages/operational/DailyMovements.jsx', 'a7121fad1240724a32346e79fb7b6abb26b2706b', '0e2956f2a509c9ccf71d2dae05db50738131a3cb'),
('frontend/src/pages/operational/ExternalOperationControls.jsx', None, 'e89c0340dd307defd4f647b75ba4d0cb8f832fec'),
('frontend/src/pages/operational/ExternalOperationControls.test.jsx', None, '2648fede14666cb1a8b2b99cd3620c63c49c4422'),
('frontend/src/pages/operational/OperationalBalances.test.jsx', '3c0c5fc2aa51dedf82aea2aace3bf79612694f7d', 'fb82b8c24cd2ebb8f63129888a1897bd50e7cc32'),
]
def git(*args, data=None):
    return subprocess.check_output(['git', *args], input=data)
def blob(path):
    p=Path(path)
    if p.is_symlink():
        raise RuntimeError('symlink forbidden')
    return git('hash-object', str(p)).decode().strip() if p.exists() else None
if git('status', '--porcelain').strip():
    raise RuntimeError('worktree is not clean')
parts=[STAGE/f'{i:02d}.patch' for i in range(1,9)]
patch=b''.join(p.read_bytes() for p in parts)
if hashlib.sha256(patch).hexdigest() != '53d512004cfb1c6c090cdf1d8445d3dda7605065be5c2bcf64bd1c1e38194dc5':
    raise RuntimeError('patch digest mismatch')
paths={p for p,_,_ in FILES}
changed={line.split('\t')[-1] for line in git('apply','--numstat','--unidiff-zero','-',data=patch).decode().splitlines()}
if changed != paths:
    raise RuntimeError('patch scope mismatch')
for path,before,_ in FILES:
    if blob(path)!=before:
        raise RuntimeError('base bytes changed: '+path)
git('apply','--check','--unidiff-zero','--whitespace=error','-',data=patch)
git('apply','--unidiff-zero','--whitespace=error','-',data=patch)
for path,_,after in FILES:
    if blob(path)!=after:
        raise RuntimeError('result differs from tested bytes: '+path)
for part in parts:
    part.unlink()
(STAGE/'apply.py').unlink()
STAGE.rmdir()
git('add','-A','--',*sorted(paths),str(STAGE))
actual=set(git('diff','--cached','--name-only').decode().splitlines())
expected=paths|{str(p) for p in parts}|{str(STAGE/'apply.py')}
if actual != expected:
    raise RuntimeError('staged file scope mismatch')
print('Verified eight exact tested source files; only own staging payload removed.')
