"""Exercise real stopped-writer Mongo dump + local encrypted restic only in CI."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import secrets
import sys
from unittest.mock import patch
from backup import execute
from common import run, write_json
from config import load
from monitor import snapshot

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0:
    raise ValueError('root disposable CI only')
config = load(sys.argv[1])
if not config['project'].startswith('exit2dhost'):
    raise ValueError('CI project boundary')
state = Path(config['state_dir']); state.mkdir(parents=True, exist_ok=True, mode=0o700)
password = state/'local-repository-password'; password.write_text(secrets.token_urlsafe(32)); password.chmod(0o600)
env = {k:v for k,v in os.environ.items() if not k.startswith(('AWS_', 'RESTIC_'))}
env.update(RESTIC_REPOSITORY=str(state/'local-restic'), RESTIC_PASSWORD_FILE=str(password))
run(['restic', 'init'], env=env)
approval = state/'ci-approval.json'
write_json(approval, {'approved':True,'scope':'synthetic-vps-backup','source_sha':config['source_sha'],
                     'project':config['project'],'expires_at':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()})
before = snapshot(config)
if 'backup:missing-or-older-than-26h' not in before['alerts']: raise ValueError('missing backup alert not triggered')
# Only the destination adapter is local: all actual Docker stop/manifest/Mongo/dump/resume calls remain real.
with patch('backup.restic_environment', return_value=env):
    result = execute(config, str(approval))
print(json.dumps({'kind':'actual_synthetic_mongo_restic_backup', **result, 'external_s3_tested':False}))
