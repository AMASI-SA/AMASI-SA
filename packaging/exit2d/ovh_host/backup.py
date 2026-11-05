"""Opt-in synthetic backup: clean writer stop, completed dump, encrypted restic check.

Defaults to a plan. Never creates a bucket, initializes a repository or prunes it.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from common import containers, docker, private_file, run, write_json
from config import load

AUTH_JS = """const fs=require('fs');
const u=fs.readFileSync('/run/secrets/mongo_username','utf8').trim();
const p=fs.readFileSync('/run/secrets/mongo_password','utf8').trim();
if(!db.getSiblingDB('admin').auth(u,p))throw Error('authentication refused');
"""

def mongo(container, script):
    return docker('exec', container, 'mongosh', '--quiet', '--eval', AUTH_JS + script)

def authorize(config, path):
    if os.geteuid() != 0:
        raise ValueError('host root operator required')
    approval = json.loads(private_file(path).read_text())
    if approval.get('approved') is not True or approval.get('scope') != 'synthetic-vps-backup':
        raise ValueError('explicit host operations approval required')
    if approval.get('source_sha') != config['source_sha'] or approval.get('project') != config['project']:
        raise ValueError('approval does not match reviewed source/project')
    expires = datetime.fromisoformat(approval['expires_at'])
    if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
        raise ValueError('approval expired or missing timezone')

def restic_environment(config):
    backup = config['backup']
    repository = backup['repository']
    if not repository.startswith('s3:https://') or '@' in repository or '?' in repository:
        raise ValueError('explicit credential-free HTTPS S3 repository required')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('AWS_', 'RESTIC_'))}
    env.update(RESTIC_REPOSITORY=repository,
               RESTIC_PASSWORD_FILE=str(private_file(backup['password_file'])),
               AWS_ACCESS_KEY_ID=private_file(backup['access_key_file']).read_text().strip(),
               AWS_SECRET_ACCESS_KEY=private_file(backup['secret_key_file']).read_text().strip())
    return env

def verified_upload(archive, metadata, env):
    # Complete local file with checked producer exit; never a blind stdin pipeline.
    run(['restic', 'backup', '--json', '--tag', 'mezan-synthetic', str(archive), str(metadata)], env=env, timeout=600)
    run(['restic', 'check', '--read-data'], env=env, timeout=600)
    with tempfile.TemporaryDirectory(prefix='mezan-restore-') as directory:
        run(['restic', 'restore', 'latest', '--tag', 'mezan-synthetic', '--target', directory], env=env, timeout=600)
        restored = Path(directory)/str(archive).lstrip('/')
        if hashlib.file_digest(restored.open('rb'), 'sha256').hexdigest() != hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest():
            raise ValueError('restored archive checksum mismatch')

def execute(config, approval):
    authorize(config, approval)
    env = restic_environment(config)
    state = Path(config['state_dir']); state.mkdir(parents=True, exist_ok=True, mode=0o700)
    import fcntl
    with (state/'backup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        usage = json.loads(run(['restic', 'stats', '--mode', 'raw-data', '--json'], env=env, timeout=120))
        if usage['total_size'] + config['backup']['max_archive_bytes'] >= config['backup']['max_repository_bytes']:
            raise ValueError('reviewed repository soft budget reached; retention review required')
        rows = containers(config)
        for name, row in rows.items():
            if not row['State']['Running'] or row['State'].get('OOMKilled') or row['State'].get('Health', {}).get('Status') == 'unhealthy':
                raise ValueError('backup requires healthy source services')
        # Verify application source manifest and fixed synthetic Mongo address inside image.
        code = "import os,sys;sys.path.insert(0,'/opt/mezan/backend');os.environ['MONGO_URL']=open('/run/secrets/mongo_uri').read().strip();from independent_staging import validate;v=validate();sys.exit(0 if v=='staging:" + config['source_sha'] + "' else 1)"
        docker('exec', rows['web']['Id'], 'python', '-c', code)
        mid = rows['mongo']['Id']; writers = [rows[k]['Id'] for k in ('web', 'reports', 'sync', 'accounting')]
        stopped = False; ttl = None; safe_resume = False
        try:
            docker('stop', '--time', '30', *writers, timeout=150)
            stopped = True
            states = json.loads(docker('inspect', *writers))
            if any(x['State']['Running'] or x['State']['ExitCode'] != 0 for x in states):
                raise ValueError('unclean writer drain: leave stopped for operator review')
            ttl = json.loads(mongo(mid, 'print(JSON.stringify(db.adminCommand({getParameter:1,ttlMonitorEnabled:1}).ttlMonitorEnabled))').decode().splitlines()[-1])
            mongo(mid, 'if(!db.adminCommand({setParameter:1,ttlMonitorEnabled:false}).ok)throw Error("TTL freeze failed")')
            with tempfile.TemporaryDirectory(prefix='mezan-backup-', dir=state) as directory:
                archive = Path(directory)/'synthetic.archive.gz'
                # Credentials remain inside Mongo tmpfs; never shell/argv/log values.
                prepare = AUTH_JS + "fs.writeFileSync('/tmp/mezan-dump-config.yaml',JSON.stringify({uri:'mongodb://'+encodeURIComponent(u)+':'+encodeURIComponent(p)+'@127.0.0.1:27017/?authSource=admin'}),{mode:0o600});"
                mongo(mid, prepare)
                import subprocess
                with archive.open('wb') as output:
                    result = subprocess.run(['docker', '-H', 'unix:///var/run/docker.sock', 'exec', mid,
                                             'mongodump', '--config=/tmp/mezan-dump-config.yaml',
                                             '--db=mezan_exit2d_staging', '--archive', '--gzip'],
                                            stdout=output, stderr=subprocess.DEVNULL, timeout=600)
                if result.returncode or not 0 < archive.stat().st_size <= config['backup']['max_archive_bytes']:
                    raise ValueError('dump failed or archive exceeds reviewed budget')
                mongo(mid, 'fs.unlinkSync("/tmp/mezan-dump-config.yaml")')
                mongo(mid, 'if(!db.adminCommand({setParameter:1,ttlMonitorEnabled:' + str(ttl).lower() + '}).ok)throw Error("TTL restore failed")')
                ttl = None; safe_resume = True
                docker('start', *writers); stopped = False
                meta = Path(directory)/'manifest.json'
                write_json(meta, {'classification': 'synthetic-only', 'source_sha': config['source_sha'],
                                 'archive_sha256': hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest(),
                                 'bytes': archive.stat().st_size, 'consistency': 'clean-all-writers-stopped-ttl-frozen'})
                verified_upload(archive, meta, env)
                receipt = {'success': True, 'verified_at': datetime.now(timezone.utc).isoformat(),
                           'source_sha': config['source_sha'], 'archive_bytes': archive.stat().st_size,
                           'encrypted_repository_checked': True, 'restored_archive_hash_equal': True}
                write_json(state/'backup-latest.json', receipt)
                return receipt
        finally:
            # Fail closed after an unclean stop or failed TTL restoration. Never clear claims.
            if ttl is not None:
                mongo(mid, 'if(!db.adminCommand({setParameter:1,ttlMonitorEnabled:' + str(ttl).lower() + '}).ok)throw Error("TTL restore failed")')
                safe_resume = True
            if stopped and safe_resume:
                docker('start', *writers)

def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--config', required=True)
    parser.add_argument('--execute', action='store_true'); parser.add_argument('--approval')
    args = parser.parse_args(); config = load(args.config)
    if not args.execute:
        print(json.dumps({'plan_only': True, 'resources_created': False, 'classification': 'synthetic-only',
                          'steps': ['validate source/private approval', 'clean stop four writers', 'freeze TTL',
                                    'completed bounded dump', 'restore TTL/start previous writers',
                                    'restic encrypted upload/check/restore checksum'],
                          'retention': 'manual reviewed forget --dry-run; no automatic prune'}))
        return
    if not args.approval:
        parser.error('--execute requires a private source-bound approval receipt')
    try:
        print(json.dumps(execute(config, args.approval)))
    except Exception as error:
        write_json(Path(config['state_dir'])/'backup-latest.json', {'success': False, 'error_type': type(error).__name__})
        raise SystemExit('Backup failed; inspect private operator journal. No credentials logged.') from None

if __name__ == '__main__': main()
