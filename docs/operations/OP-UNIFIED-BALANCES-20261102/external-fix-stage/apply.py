"""One-shot exact three-file source delta, not an application/runtime command."""
from pathlib import Path
import subprocess, hashlib
STAGE=Path('docs/operations/OP-UNIFIED-BALANCES-20261102/external-fix-stage')
FILES=[
('backend/operational_balance_routes.py','a0b1d62a01921989a72586519bed1f2266f07fbd','230f82fb904b0fa72e091df4c7177a85cb1b87f4'),
('backend/tests/test_operational_balance_external_advances.py','e5f55f703b9751094c8a160799a0a9344511475e','b86bdf9eef75a0c5f879c2558aedace2ac9d6b53'),
('frontend/src/pages/operational/ExternalOperationControls.test.jsx','2648fede14666cb1a8b2b99cd3620c63c49c4422','744c4eea744c3ddf8b1a5b33258770b055ff7d58')]
def git(*a,data=None):return subprocess.check_output(['git',*a],input=data)
def blob(path):
    if Path(path).is_symlink():raise RuntimeError('symlink forbidden')
    return git('hash-object',path).decode().strip()
if git('status','--porcelain').strip():raise RuntimeError('dirty worktree')
patch=(STAGE/'source.patch').read_bytes()
if hashlib.sha256(patch).hexdigest()!='4faa0214ed90e2ac622eb48373e81c6dbeb50f450c45e317052522ddf8362bd6':raise RuntimeError('patch digest differs')
paths={p for p,_,_ in FILES}
if {s.split('\t')[-1] for s in git('apply','--numstat','--unidiff-zero','-',data=patch).decode().splitlines()}!=paths:raise RuntimeError('unexpected scope')
for p,b,_ in FILES:
    if blob(p)!=b:raise RuntimeError('base changed:'+p)
git('apply','--check','--unidiff-zero','--whitespace=error','-',data=patch)
git('apply','--unidiff-zero','--whitespace=error','-',data=patch)
for p,_,a in FILES:
    if blob(p)!=a:raise RuntimeError('result mismatch:'+p)
(STAGE/'source.patch').unlink();(STAGE/'apply.py').unlink();STAGE.rmdir()
git('add','-A','--',*sorted(paths),str(STAGE))
if set(git('diff','--cached','--name-only').decode().splitlines())!=paths|{str(STAGE/'source.patch'),str(STAGE/'apply.py')}:raise RuntimeError('staged scope mismatch')
print('Exact three-file delta verified; own plaintext staging payload removed.')
