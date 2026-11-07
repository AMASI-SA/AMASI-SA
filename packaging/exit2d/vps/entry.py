"""Load ONLY synthetic application secrets from Compose mounts, never dotenv."""
import os
from pathlib import Path
import sys

NAMES = {'JWT_SECRET': 'jwt_secret', 'ADMIN_PASSWORD': 'admin_password',
         'MFA_BOOTSTRAP_CODE': 'mfa_bootstrap_code', 'MONGO_URL': 'mongo_uri'}

def main():
    for key, name in NAMES.items():
        os.environ[key] = Path('/run/secrets', name).read_text().strip()
    sys.path.insert(0, '/opt/mezan/backend')
    from independent_staging import validate
    validate()  # Includes fixed synthetic DB, host and source integrity checks.
    args = sys.argv[1:]
    if len(args) == 1 and args[0] in {'web', 'worker-reports', 'worker-sync', 'worker-accounting', 'migration'}:
        command = ['/opt/render/start.py', args[0]]
    elif args and args[0] == 'test' and os.environ.get('EXIT2D_ECONOMY_CI') == '1':
        if args[1] not in {'-', '/evidence/tools/render/economy_acceptance.py', '/opt/vps/restore.py'}:
            raise RuntimeError('unknown synthetic CI fixture')
        command = args[1:]
    else:
        raise RuntimeError('explicit synthetic role required')
    os.execv(sys.executable, [sys.executable, *command])

if __name__ == '__main__':
    main()
