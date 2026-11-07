"""Targeted real-container checks; no Production connection or scan."""
import errno
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, '/opt/mezan/backend')
from independent_staging import validate
validate()
import httpx
from pymongo import MongoClient
from pymongo.errors import OperationFailure

assert os.geteuid() == 65532
assert not Path('/var/run/docker.sock').exists()
try:
    Path('/opt/mezan/read-only-security-probe').write_text('must fail')
except OSError as error:
    assert error.errno in {errno.EROFS, errno.EACCES}
else:
    raise AssertionError('application filesystem writable')
for name in ('jwt_secret', 'admin_password', 'mfa_bootstrap_code', 'mongo_uri'):
    assert Path('/run/secrets', name).stat().st_mode & 0o077 == 0
for changes in ({'DB_NAME': 'production'}, {'QOYOD_API_KEY': 'public-negative-fixture'},
                {'MONGO_URL': 'mongodb://fixture:public@production.invalid:27017'}):
    try:
        validate({**os.environ, **changes})
    except RuntimeError:
        pass
    else:
        raise AssertionError('synthetic boundary did not reject unsafe config')
with httpx.Client(trust_env=False, timeout=8) as client:
    assert client.get('http://reports:8000/api/auth/me').status_code == 404
    assert client.get('http://reports:8000/api/dashboard').status_code == 401
    assert client.get('http://127.0.0.1:10000/api/auth/me').status_code == 401
unauthenticated = MongoClient('mongodb://staging-mongo:27017', serverSelectionTimeoutMS=3000)
try:
    unauthenticated.mezan_exit2d_staging.users.find_one({})
except OperationFailure as error:
    assert error.code == 13
else:
    raise AssertionError('Mongo accepted unauthenticated business read')
finally:
    unauthenticated.close()
print(json.dumps({'kind': 'actual_compose_security_boundary', 'nonroot_readonly_no_docker_socket': True,
    'secret_files_not_group_or_world_readable': True, 'unsafe_database_and_provider_config_rejected': True,
    'reports_route_allowlist_and_auth_enforced': True, 'mongo_unauthenticated_read_rejected': True}), flush=True)
