"""Linux root only; create NEW staging secrets outside the repository."""
import argparse
import base64
import os
from pathlib import Path
import secrets

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('--ci-fixture', action='store_true')
    args = parser.parse_args()
    assert os.name == 'posix' and os.geteuid() == 0, 'Linux root required for restrictive ownership'
    target = args.directory.resolve()
    assert not target.is_relative_to(Path.cwd().resolve()), 'secrets must be outside checkout'
    target.mkdir(mode=0o700, parents=True, exist_ok=False)
    username = 'fixture' if args.ci_fixture else 'staging'
    password = 'synthetic-only-password' if args.ci_fixture else secrets.token_urlsafe(36)
    values = {'mongo_username': username, 'mongo_password': password,
        'mongo_uri': f'mongodb://{username}:{password}@staging-mongo:27017',
        'mongo_replica_key': 'syntheticOnlyReplicaKey1234567890' if args.ci_fixture else base64.b64encode(secrets.token_bytes(48)).decode('ascii'),
        'jwt_secret': 'synthetic-hosting-public-key-not-for-production' if args.ci_fixture else secrets.token_urlsafe(48),
        'admin_password': 'Synthetic-hosting-password-only!' if args.ci_fixture else secrets.token_urlsafe(36),
        'mfa_bootstrap_code': secrets.token_urlsafe(24)}
    for name, value in values.items():
        file = target / name
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(fd, 'w') as stream:
            stream.write(value)
        os.chown(file, 65532, 65532)
    print('PASS staging-only secret files created; values withheld')

if __name__ == '__main__':
    main()
