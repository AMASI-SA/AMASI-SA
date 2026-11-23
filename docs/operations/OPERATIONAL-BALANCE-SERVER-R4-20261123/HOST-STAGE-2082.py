#!/usr/bin/env python3
"""Stage the already rehearsed PR2082 release to /app, without publishing.

Must be run in the owner's Emergent terminal immediately before publication.
Never changes GitHub refs; never deletes unrelated data or resets Git history.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone

APP = Path('/app')
REHEARSAL = Path('/tmp/mezan-pr2082-rehearsal-6210m7fq')
BRANCH = 'hotfix/prod-snap-meta-final'
N = '44122bcc986bd1dff1532b9ad62c0268e899a179'
A = 'aec203382b387c3812509084e77c2922aa860877'
B = '0bd6a3fadd33d6354ebdc8dec814bcb80b0d7218'
D = 'c2d244a3349013c06f3fb7824070bfa12bd0bfe0'
TREE = '4fa54935f420e2a546c2237c0fb7b0ef9ef8367d'
RELEASE_ID = 'rg5-12e0f132b3ee688815cd2ec401bd34c43706d193492edacc7be6c5ca6e6ade8a'
INTENT_HASH = '59da5ee6a86f3bfa5a1b8446c6aacb8e180144bc15ccaca3e414e1ec23b151ab'
ARTIFACT = '27b7717df8a2dcc2b46868da4d103b44d33e2ad07c879da6563ecaef7e545b9a'
OUTPUTS = (
    ('frontend/build', 'dir', 'build'),
    ('frontend/.release/reproducible-build.json', 'file', 'proof'),
    ('backend/release_identity.json', 'file', 'identity'),
)
VERIFIED_HASHES = {
    'frontend/build/build-meta.json': 'c13f1ac0a7d28dbefca1fd234314521482bc3b34def126d719dc24672bd39368',
    'frontend/.release/reproducible-build.json': 'd25651371324610544e3f2de69f0bbaba41c0d9ae1f71c60e3dc4ac2b2d2469a',
    'backend/release_identity.json': '5a6f00b2b63f02d486490ea57a59ff9440cf0189b629770b71166611b2c21bd4',
}
class Stop(Exception): pass

def require(ok, why):
    if not ok: raise Stop(why)

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def shell(*args, cwd=APP, timeout=120):
    env = dict(os.environ)
    env.pop('GIT_ASKPASS', None)
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GIT_OPTIONAL_LOCKS'] = '0'
    try:
        p = subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True,
                           stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Stop('command failed to execute: ' + args[0]) from exc
    require(p.returncode == 0, f'command failed: {args[0]} {args[1] if len(args)>1 else ""}; exit={p.returncode}')
    return p.stdout.strip()

def git(*args, cwd=APP): return shell('git','--no-optional-locks',*args,cwd=cwd)

def guard_status():
    value = json.loads(shell(sys.executable,'-B','scripts/production_release_guard.py','status'))
    require(value == {'active': False}, 'original release guard is active or incomplete')

def reject_symlink(path):
    if path.is_symlink(): raise Stop('symlink source or destination: ' + str(path))

def measure_tree(path):
    reject_symlink(path)
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        for name in dirs + files:
            obj = Path(root) / name
            reject_symlink(obj)
            require(obj.is_dir() or obj.is_file(), 'unsupported generated filesystem node')
            if obj.is_file(): total += obj.stat().st_size
    return total

def status_report(status, **details):
    print('PR2082_STAGE', json.dumps({'status':status,'deployment_sha':D,'release_id':RELEASE_ID,**details},sort_keys=True), flush=True)

def main():
    require(APP.resolve(strict=True) == APP and (APP/'.git').is_dir(), 'invalid shared /app checkout')
    require(REHEARSAL.resolve(strict=True) == REHEARSAL, 'rehearsal folder absent or redirected')
    result = json.loads((REHEARSAL/'result.json').read_text())
    required = {'status':'isolated_rehearsal_passed','built':True,'adapter_exit':0,
                'package_boundaries_verified':True,'lease_created':False,
                'merge_or_publish_performed':False,'shared_source_or_outputs_changed_by_helper':False,
                'N':N,'A':A,'B':B,'candidate_tree':TREE,'intent_sha256':INTENT_HASH,
                'release_id':RELEASE_ID,'frontend_artifact_tree_sha256':ARTIFACT}
    require(all(result.get(k) == v for k,v in required.items()), 'rehearsal receipt does not match frozen approved candidate')
    src = REHEARSAL/'repo'
    require(src.is_dir() and not src.is_symlink(), 'isolated source checkout missing')
    require(git('rev-parse','HEAD',cwd=src)==B and git('rev-parse','HEAD^{tree}',cwd=src)==TREE,
            'isolated repo is not exact reviewed B')
    require(not git('status','--porcelain=v1','--untracked-files=no',cwd=src), 'isolated tracked source changed')
    require(digest(src/'release/release-intent-v5.json')==INTENT_HASH, 'candidate intent changed')
    for relative, expected in VERIFIED_HASHES.items():
        p=src/relative
        require(p.is_file() and not p.is_symlink() and digest(p)==expected,
                'candidate generated file verification failed: '+relative)
    src_identity=json.loads((src/'backend/release_identity.json').read_text())
    require(src_identity.get('release_id')==RELEASE_ID and src_identity.get('source_git_sha')==A,
            'materialized runtime identity differs')
    source_size=measure_tree(src/'frontend/build')
    print('CANDIDATE_OUTPUT_BYTES',source_size,flush=True)
    guard_status()
    require(git('branch','--show-current')==BRANCH, 'shared branch differs')
    require(git('rev-parse','HEAD')==N, 'shared checkout advanced or changed')
    require(not git('status','--porcelain=v1','--untracked-files=no'), 'shared tracked source is dirty')
    remote=git('ls-remote','--exit-code','origin','refs/heads/'+BRANCH,
               'refs/heads/codex/operational-balance-server-r4-20261123')
    refs = dict((ref,sha) for sha,ref in (line.split() for line in remote.splitlines()))
    require(refs == {'refs/heads/'+BRANCH:D,
                     'refs/heads/codex/operational-balance-server-r4-20261123':B},
            'remote production or reviewed candidate changed')
    require(shutil.disk_usage(APP).free >= source_size*2 + 40*1024*1024,
            'insufficient free /app space to safely stage generated outputs')

    # Retain a full original-output backup on /tmp, before touching any live path.
    backup=REHEARSAL/'host-stage-backup'
    require(not backup.exists(), 'a prior stage backup exists; do not repeat staging')
    backup.mkdir(mode=0o700)
    stamp=datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    staging=[]
    for relative,kind,label in OUTPUTS:
        current=APP/relative
        generated=src/relative
        require(generated.exists() and not generated.is_symlink(), 'missing isolated output: '+relative)
        require(current.parent.is_dir() and not current.parent.is_symlink(), 'invalid /app output parent: '+relative)
        if current.exists() or current.is_symlink():
            reject_symlink(current)
            old=backup/label
            if kind=='dir':
                require(current.is_dir(), 'current output path has wrong kind')
                measure_tree(current)
                shutil.copytree(current,old)
            else:
                require(current.is_file(), 'current output path has wrong kind')
                shutil.copy2(current,old)
            print('BACKUP_SAVED',relative,flush=True)
        new=current.with_name('.'+current.name+'.pr2082-staged-'+stamp)
        previous=current.with_name('.'+current.name+'.pr2082-previous-'+stamp)
        require(not new.exists() and not previous.exists(), 'staging or previous path collision')
        if kind=='dir': shutil.copytree(generated,new)
        else: shutil.copy2(generated,new)
        staging.append((current,new,previous))

    # Safely fast-forward only the already-merged, tree-equivalent D.
    guard_status()
    require(git('rev-parse','HEAD')==N and not git('status','--porcelain=v1','--untracked-files=no'),
            'shared state changed while saving backups')
    git('fetch','--no-tags','origin',BRANCH)
    require(git('rev-parse','FETCH_HEAD')==D, 'fetched production no longer matches approved merge D')
    git('merge','--ff-only',D)
    require(git('rev-parse','HEAD')==D and git('rev-parse','HEAD^{tree}')==TREE,
            'fast-forward resulted in an unexpected commit or tree')
    print('TRACKED_SOURCE_STAGED',D,flush=True)
    for current,new,previous in staging:
        if current.exists():
            current.rename(previous)  # preserve the original live output on /app
        new.rename(current)
        print('OUTPUT_STAGED',str(current.relative_to(APP)),flush=True)
    for relative,expected in VERIFIED_HASHES.items():
        require(digest(APP/relative)==expected,'staged output hash mismatch: '+relative)
    guard_status()
    require(not git('status','--porcelain=v1','--untracked-files=no'), 'unexpected tracked modification after staging')
    require(git('rev-parse','HEAD')==D, 'deployment commit changed during staging')

    # Original guard owns the lease. Never bypass it or synthesize its receipt.
    prepared=json.loads(shell(sys.executable,'-B','scripts/production_release_guard.py',
                              'prepare','--actor','owner-pr2082-manual'))
    require(prepared.get('deployment_git_sha')==D and prepared.get('source_git_sha')==A
            and prepared.get('release_id')==RELEASE_ID,
            'original prepare returned unexpected release identity')
    readiness=json.loads(shell(sys.executable,'-B','scripts/production_release_guard.py','prepublish'))
    require(readiness.get('ready_to_publish') is True and readiness.get('deployment_git_sha')==D
            and readiness.get('release_id')==RELEASE_ID,
            'original prepublish did not accept approved identity')
    status_report('READY_TO_PUBLISH',backup=str(backup),verified=True,
                  guard_lease_prepared=True,original_precheck=True)
    print('NEXT: publish once in Emergent UI; preserve lease; then use original verify --url https://mezansalla.com',flush=True)

if __name__=='__main__':
    try: main()
    except (Stop,OSError,ValueError,KeyError,subprocess.SubprocessError) as exc:
        print('STAGE_STOP',type(exc).__name__,str(exc),file=sys.stderr,flush=True)
        raise SystemExit(2)