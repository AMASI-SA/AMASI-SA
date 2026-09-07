"""Read-only fail-closed preflight. Never patches or bypasses runtime guards."""
import ast
import os
from pathlib import Path
from salla_http_simulator import validate_addresses


def check(source=None):
    validate_addresses(os.environ)
    if source is None:
        root = Path('/opt/mezan/backend')
        if not root.is_dir():
            root = Path(__file__).resolve().parents[2] / 'backend'
        source = (root / 'independent_runtime.py').read_text(encoding='utf-8')
    # Known exact guard conflicts with the real client's encryption requirement.
    # Deliberately blocks this source, rather than renaming the variable or
    # injecting keys after validation. A changed guard needs independent review.
    tree = ast.parse(source)
    validator = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'validate_before_import')
    if any(isinstance(n, ast.Constant) and n.value == 'TOKEN_ENC_KEY' for n in ast.walk(validator)):
        raise RuntimeError('BLOCKED_SYNTHETIC_PROVIDER_KEY_GUARD')
    raise RuntimeError('BLOCKED_RUNTIME_CONTRACT_REVIEW_REQUIRED')


if __name__ == '__main__':
    try:
        check()
    except (RuntimeError, ValueError) as error:
        code = error.args[0]
        allowed = {'BLOCKED_SYNTHETIC_PROVIDER_KEY_GUARD', 'BLOCKED_RUNTIME_CONTRACT_REVIEW_REQUIRED',
                   'SIMULATOR_ADDRESS_REJECTED', 'SIMULATOR_PROXY_REJECTED'}
        print(code if code in allowed else 'PREFLIGHT_FAILED')
        raise SystemExit(1)
