bash <<'MEZAN_RELEASE'
set -euo pipefail
cd /app
export PYTHONDONTWRITEBYTECODE=1
MEZAN_DEPLOYMENT_SHA=a56399edf46f97f8b8e4793dc2619bb2a1b7ac2f
MEZAN_PREVIOUS_SHA=049fba51c13b902cd12808e03e87aca203649fb7

mezan_require_free_lease() {
python - <<'PY'
import json, subprocess
state = json.loads(subprocess.check_output(
    ['python', 'scripts/production_release_guard.py', 'status'], text=True))
print(json.dumps(state, indent=2), flush=True)
if state.get('active') is not False or state.get('incomplete_lease_directory'):
    raise SystemExit('STOP: a release lease requires its owner; no workspace update.')
PY
}

mezan_require_free_lease
test "$(git rev-parse --show-toplevel)" = /app
test "$(git branch --show-current)" = hotfix/prod-snap-meta-final
git diff --quiet
git diff --cached --quiet
MEZAN_CURRENT_SHA="$(git rev-parse HEAD)"
if test "$MEZAN_CURRENT_SHA" != "$MEZAN_PREVIOUS_SHA" && test "$MEZAN_CURRENT_SHA" != "$MEZAN_DEPLOYMENT_SHA"; then
  echo 'STOP: /app commit changed; inspect the current workspace before continuing.'
  exit 1
fi
git fetch origin hotfix/prod-snap-meta-final
test "$(git rev-parse origin/hotfix/prod-snap-meta-final)" = "$MEZAN_DEPLOYMENT_SHA"
git merge-base --is-ancestor HEAD "$MEZAN_DEPLOYMENT_SHA"
mezan_require_free_lease

python - <<'PY'
import subprocess
for name in ('backend', 'frontend'):
    probe = subprocess.run(['supervisorctl', 'status', name], capture_output=True, text=True)
    fields = probe.stdout.strip().split()
    if len(fields) < 2 or fields[0] != name:
        raise SystemExit('STOP: cannot confirm preview service state: ' + name)
    if fields[1] in {'RUNNING', 'STARTING', 'BACKOFF'}:
        subprocess.run(['supervisorctl', 'stop', name], check=True)
    elif fields[1] not in {'STOPPED', 'EXITED', 'FATAL'}:
        raise SystemExit('STOP: preview service is changing state: ' + name)
PY

git merge --ff-only origin/hotfix/prod-snap-meta-final
test "$(git rev-parse HEAD)" = "$MEZAN_DEPLOYMENT_SHA"
git diff --quiet
git diff --cached --quiet

python - <<'PY'
from pathlib import Path
from datetime import datetime, timezone
import shutil, subprocess
root = Path('/app').resolve(strict=True)
backend = (root / 'backend').resolve(strict=True)
assert backend.parent == root
roots = []
for item in sorted(backend.rglob('__pycache__'), key=lambda p: len(p.parts)):
    if any(parent in item.parents for parent in roots):
        continue
    if item.is_symlink() or not item.is_dir():
        raise SystemExit('STOP: unexpected Python cache path.')
    item.resolve(strict=True).relative_to(backend)
    tracked = subprocess.check_output(['git', 'ls-files', '--', str(item.relative_to(root))])
    if tracked.strip():
        raise SystemExit('STOP: tracked files found in a cache directory.')
    roots.append(item)
backup = root / '.release-preserved' / ('qoyod-two-hour-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
for item in roots:
    target = backup / item.relative_to(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(item), str(target))
print('Preserved Python cache directories:', len(roots))
PY

python - <<'PY'
import json
from pathlib import Path
p = json.loads(Path('release/release-intent-v5.json').read_text())
assert p['protocol_version'] == 5 and p['schema_version'] == 2
assert p['source_git_sha'] == '9e22418cf2350fe09a4bb61acfabaefb7748dede'
assert p['runtime_identity']['release_id'] == 'rg5-a3231edbd640b42b45d45cd0254bc0c0099de868b2722a97bd707495e5bb3231'
PY
(cd frontend && REACT_APP_BACKEND_URL=https://mezansalla.com yarn build)
python scripts/production_release_guard.py prepare --actor 'BACKGROUND-WORKERS-QOYOD-2H-20261106'
python scripts/production_release_guard.py prepublish
MEZAN_RELEASE
