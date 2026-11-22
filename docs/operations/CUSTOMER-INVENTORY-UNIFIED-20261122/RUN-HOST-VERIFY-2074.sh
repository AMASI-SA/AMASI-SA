#!/usr/bin/env bash
# Run ONLY AFTER the NEW Emergent deployment for PR2074 explicitly reports
# Deployment Succeeded. A previous success message is not sufficient.
# This executes the unchanged original three-probe verifier for this release.
# It never prepares, aborts, manually clears a lease, or publishes.
set -euo pipefail

cd /app
PYTHONDONTWRITEBYTECODE=1 GIT_TERMINAL_PROMPT=0 python -B - <<'PY'
import json
import os
import sys
from pathlib import Path

APP = Path('/app')
URL = 'https://mezansalla.com'
ACTOR = 'codex-customer-inventory-unified-2074-20261122'
EXPECTED = {
    'deployment_git_sha': '24270ebdfdebb83282389308a2349cdb27e2b8e4',
    'source_git_sha': '4e6c8a61bd1566e061a4f312e797ce239b582475',
    'source_base_git_sha': '06763ffd6e3c3f7c861769b61d2c4e6b9b2d5f35',
    'git_sha': '4e6c8a61bd1566e061a4f312e797ce239b582475',
    'release_id': 'rg5-2e0e3797d6ff3b955a3a23fd31487bd92721e70f316f3bdf34cab0d94c5d630f',
    'protocol_version': 5,
}
ARTIFACT = '68278a1d40fc16b2852359eeee963b12bb1820e675e691d74410ce4f47f5123b'


def matches(value):
    return (
        isinstance(value, dict)
        and type(value.get('protocol_version')) is int
        and all(value.get(key) == expected for key, expected in EXPECTED.items())
    )


try:
    if any(os.environ.get(key) for key in (
        'GITHUB_ACTIONS', 'GITHUB_SHA', 'GITHUB_WORKSPACE', 'GIT_DIR',
        'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
        'GIT_ALTERNATE_OBJECT_DIRECTORIES', 'GIT_NAMESPACE', 'GIT_CONFIG_PARAMETERS',
        'GIT_CONFIG_COUNT', 'GIT_CONFIG', 'GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL',
        'GIT_TRACE', 'GIT_TRACE_PACKET', 'GIT_TRACE_CURL', 'GIT_CURL_VERBOSE',
        'GIT_TRACE2_EVENT', 'GIT_TRACE2_PERF',
    )):
        raise RuntimeError('Unexpected CI or Git routing environment')
    if Path.cwd().resolve() != APP:
        raise RuntimeError('Original /app working directory required')
    sys.path.insert(0, str(APP))
    from scripts import production_release_guard as guard
    if (
        guard.REPO_ROOT.resolve() != APP
        or Path(guard.__file__).resolve() != APP / 'scripts/production_release_guard.py'
        or guard.PROTOCOL_VERSION != 5
        or guard.PRODUCTION_ORIGIN != URL
    ):
        raise RuntimeError('Original /app protocol-v5 guard required')

    # The public CLI verify --url URL calls guard.verify(URL), whose complete
    # body is this same original operation lock plus _verify_locked(URL).
    # Put the additional exact-own-lease fence inside that lock to prevent a
    # replacement lease being verified/closed between a status call and CLI.
    # Do not call status() or verify() while holding this non-reentrant lock.
    with guard._release_operation_lock():
        if not guard.LEASE_PATH.is_file():
            raise RuntimeError('No active own lease; no verification was attempted')
        lease = guard._read_json(guard.LEASE_PATH)
        if (
            not matches(lease)
            or lease.get('actor') != ACTOR
            or lease.get('branch') != 'hotfix/prod-snap-meta-final'
            or not isinstance(lease.get('frontend_build'), dict)
            or lease['frontend_build'].get('artifact_tree_sha256') != ARTIFACT
        ):
            raise RuntimeError('Active lease belongs to another release or actor; preserved')

        result = guard._verify_locked(URL)
        # Preserve the complete JSON emitted by the original CLI, including
        # its three-probe count and frontend MIME/cache/asset observations.
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        if (
            not matches(result)
            or result.get('verified') is not True
            or type(result.get('checks')) is not int
            or result.get('checks') != 3
            or result.get('url') != URL
            or result.get('frontend_artifact_tree_sha256') != ARTIFACT
        ):
            raise RuntimeError('Original verification result differs from this release')
        if guard.LEASE_PATH.exists() or guard.LEASE_DIR.exists():
            raise RuntimeError('Original verifier did not completely close its own lease')

    final_status = guard.status()
    print(json.dumps(final_status, ensure_ascii=False, indent=2), flush=True)
    if final_status != {'active': False}:
        raise RuntimeError('Final guard is not exactly inactive; no further action was taken')
except Exception as error:
    reason = str(error)
    if reason.startswith('git '):
        reason = 'Original Git check failed; remote details omitted'
    print('HOST_VERIFY_REFUSED:', type(error).__name__, reason, file=sys.stderr, flush=True)
    print('Stop and retain the output. No lease was manually aborted or cleared.',
          file=sys.stderr, flush=True)
    raise SystemExit(1)
PY

