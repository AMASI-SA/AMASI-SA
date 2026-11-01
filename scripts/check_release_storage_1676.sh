#!/usr/bin/env bash
# Only Yarn/pip download caches are cleaned. No merge, publish or package install.
if bash <<'MEZAN_SAFE_SPACE'
set -Eeuo pipefail
trap 'code=$?; printf "\nSPACE CHECK STOPPED at line %s. No deployment performed.\n" "$LINENO"; exit "$code"' ERR
export GIT_PAGER=cat PAGER=cat GIT_OPTIONAL_LOCKS=0
export TMPDIR=/tmp TMP=/tmp TEMP=/tmp PYTHONDONTWRITEBYTECODE=1
export YARN_IGNORE_PATH=1
unset YARN_PATH NODE_OPTIONS NODE_PATH PYTHONHOME PYTHONPATH
cd /app

python3 -B - <<'CHECK'
import json, os, subprocess
from pathlib import Path

def require(ok, message):
    if not ok:
        raise SystemExit(message)

app = Path('/app')
head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
require(head == '3bac875cfbcbc7f66849edae83c478dd194d9c12',
        'APP source changed. Cleanup stopped for review.')
state = json.loads(subprocess.check_output(
    ['python3', '-B', 'scripts/production_release_guard.py', 'status'], text=True))
require(state.get('active') is False, 'Active release lease. Cleanup stopped.')

for path in (Path('/root/.cache/yarn'), Path('/root/.cache/pip')):
    require(path.is_dir() and path.resolve() == path,
            'Cache directory missing or redirected: ' + str(path))
    require(path.stat().st_dev == app.stat().st_dev,
            'Cache is not on the full APP filesystem: ' + str(path))
    # Native cleaners must not be redirected through nested symlinks/mounts.
    for current, directories, files in os.walk(path, followlinks=False):
        for name in directories + files:
            child = Path(current) / name
            require(not child.is_symlink() and child.stat().st_dev == app.stat().st_dev,
                    'Cache contains redirected entries; preserved for review.')
        require(not os.path.ismount(current), 'Cache contains a mount; preserved.')

managers = {'yarn', 'yarn.js', 'npm', 'npm-cli.js', 'pnpm', 'pip', 'pip3',
            'pip3.11', 'corepack', 'corepack.js'}
operations = {'install', 'add', 'upgrade', 'update', 'ci', 'download', 'wheel',
              'uninstall', 'remove', 'cache', 'prepare'}
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit() or int(proc.name) == os.getpid():
        continue
    try:
        args = (proc / 'cmdline').read_bytes().decode('utf-8', 'replace').split('\0')
    except (FileNotFoundError, ProcessLookupError):
        continue
    except PermissionError:
        try:
            require(proc.stat().st_uid != os.geteuid(),
                    'Cannot inspect a same-user process. Cleanup stopped.')
        except (FileNotFoundError, ProcessLookupError):
            pass
        continue
    if any(Path(arg).name in managers for arg in args[:5]) and operations.intersection(args):
        raise SystemExit('Package operation active (PID ' + proc.name + '). No cleanup started.')
print('CACHE_SCOPE_AND_IDLE_CHECK_PASS')
CHECK

echo '=== FREE SPACE BEFORE ==='
df -h /app /root /tmp
cd /tmp

[ "$(yarn --version)" = '1.22.22' ]
ACTUAL_CACHE="$(yarn --silent cache dir --cache-folder /root/.cache/yarn)"
[ "$ACTUAL_CACHE" = '/root/.cache/yarn/v6' ]
PIP_CACHE="$(python3 -B -m pip --isolated --disable-pip-version-check --cache-dir /root/.cache/pip cache dir)"
[ "$PIP_CACHE" = '/root/.cache/pip' ]

echo '=== CLEANING ONLY YARN AND PIP DOWNLOAD CACHES ==='
yarn cache clean --cache-folder /root/.cache/yarn --non-interactive
python3 -B -m pip --isolated --disable-pip-version-check --cache-dir /root/.cache/pip cache purge

echo '=== FREE SPACE AFTER ==='
df -h /app /root /tmp

echo '=== LARGEST DIRECTORIES — READ ONLY ==='
du -xh --max-depth=2 /app /root | sort -hr | sed -n '1,35p'

echo '=== CACHE_CLEAN_AND_STORAGE_READ_COMPLETE ==='
echo 'No installed packages, source files, Git history, or financial data were removed.'
MEZAN_SAFE_SPACE
then
  echo 'Cache cleanup and storage read completed. No deployment performed.'
else
  echo 'Stopped. Some cache cleanup may have completed; send the output. No deployment.'
  exit 1
fi
