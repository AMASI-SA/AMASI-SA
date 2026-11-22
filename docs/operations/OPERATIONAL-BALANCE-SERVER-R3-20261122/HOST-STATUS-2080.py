#!/usr/bin/env python3
"""Print bounded host release status; never stage, prepare, verify or publish."""
import json
from pathlib import Path
import subprocess
import sys

APP = Path('/app')
PRODUCTION = 'refs/heads/hotfix/prod-snap-meta-final'
CANDIDATE = 'refs/heads/codex/operational-balance-server-r3-20261122'


def read(args):
    try:
        result = subprocess.run(args, cwd=APP, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        raise SystemExit('STOP: host status command unavailable or busy; no release action performed')
    if result.returncode:
        raise SystemExit('STOP: host status command failed; no release action performed')
    return result.stdout.strip()


def main():
    status = json.loads(read([sys.executable, '-B', 'scripts/production_release_guard.py', 'status']))
    if not isinstance(status, dict) or type(status.get('active')) is not bool:
        raise SystemExit('STOP: original guard returned an unexpected status')
    keep = ('active', 'incomplete_lease_directory', 'actor', 'source_git_sha',
            'deployment_git_sha', 'release_id', 'protocol_version')
    report = {
        'kind': 'pr2080_informational_host_status',
        'guard': {key: status[key] for key in keep if key in status},
        'host_head': read(['git', 'rev-parse', 'HEAD']),
        'host_branch': read(['git', 'branch', '--show-current']),
        'tracked_clean': not bool(read(['git', 'status', '--porcelain', '--untracked-files=no'])),
        'remote_refs': read(['git', 'ls-remote', '--exit-code', 'origin', PRODUCTION, CANDIDATE]),
        'lease_prepared_or_removed': False,
        'shared_source_or_outputs_changed': False,
        'publication_performed': False,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
