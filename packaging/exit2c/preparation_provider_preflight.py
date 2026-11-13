"""Execute the real guard before server import, inside the isolated namespace."""
import importlib.util
import inspect
import os
from pathlib import Path
import sys
from salla_http_simulator import validate_addresses


def check():
    validate_addresses(os.environ)
    root = Path('/opt/mezan/backend')
    if not root.is_dir():
        root = Path(__file__).resolve().parents[2] / 'backend'
    spec = importlib.util.spec_from_file_location('preflight_runtime', root / 'independent_runtime.py')
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    if os.environ.get('MEZAN_ACCEPTANCE_PROFILE') != runtime.SALLA_SIMULATOR_PROFILE:
        raise RuntimeError('profile required')
    runtime.validate_before_import('web')
    from preparation_lifecycle_acceptance import verify_login_fixture_schema
    verify_login_fixture_schema()
    # The real Salla client uses the installed HTTPX default. Fail if a later
    # dependency changes it to following redirects. No network call is made.
    import httpx
    for client in (httpx.Client, httpx.AsyncClient):
        if inspect.signature(client).parameters['follow_redirects'].default is not False:
            raise RuntimeError('redirect policy rejected')
    sys.path.insert(0, str(root))
    from salla_integration.crypto import encrypt_token, decrypt_token
    sample = 'exit2d-preflight-synthetic-token'
    if decrypt_token(encrypt_token(sample)) != sample:
        raise RuntimeError('crypto round trip failed')


if __name__ == '__main__':
    try:
        check()
    except Exception:
        print('FAIL SIMULATOR_RUNTIME_PREFLIGHT')
        raise SystemExit(1)
    print('PASS SIMULATOR_RUNTIME_PREFLIGHT')
