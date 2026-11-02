"""Hash-locked source delta only; never execute application or financial code."""
from pathlib import Path
import hashlib,subprocess
S=Path('docs/operations/OP-UNIFIED-BALANCES-20261102/policy-boundary-stage')
F=[('backend/operational_bundle_cost_projection.py','c4dc9dad8cc88f95dac7629b7766d227e75cdb8b','b29fdaa8222ef0c1601ddfece09219ead30eaee0'),('backend/tests/test_operational_balance_bundle_financial_boundary.py',None,'65c18d60821c1daff915d50b144f3eb3b532fb58')]
def git(*a,data=None):return subprocess.check_output(['git',*a],input=data)
def blob(p):
    if Path(p).is_symlink():raise RuntimeError('symlink')
    return git('hash-object',p).decode().strip() if Path(p).exists() else None
if git('status','--porcelain').strip():raise RuntimeError('dirty worktree')
p=(S/'source.patch').read_bytes()
if hashlib.sha256(p).hexdigest()!='d2616e1376be3d13452e1504e50398a68b64c0d761ef06785040d2bfad68b8de':raise RuntimeError('wrong patch')
paths={f for f,_,_ in F}
if {s.split('\t')[-1] for s in git('apply','--numstat','--unidiff-zero','-',data=p).decode().splitlines()}!=paths:raise RuntimeError('wrong paths')
for f,b,a in F:
    if blob(f)!=b:raise RuntimeError('base differs:'+f)
git('apply','--check','--unidiff-zero','--whitespace=error','-',data=p)
git('apply','--unidiff-zero','--whitespace=error','-',data=p)
for f,b,a in F:
    if blob(f)!=a:raise RuntimeError('result differs:'+f)
(S/'source.patch').unlink();(S/'apply.py').unlink();S.rmdir()
git('add','-A','--',*sorted(paths),str(S))
if set(git('diff','--cached','--name-only').decode().splitlines())!=paths|{str(S/'source.patch'),str(S/'apply.py')}:raise RuntimeError('staged paths differ')
print('Exact two-file read-only boundary source checkpoint; no financial action.')
