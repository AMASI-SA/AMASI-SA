bash <<'MEZAN_VERIFY'
set -euo pipefail
cd /app
export PYTHONDONTWRITEBYTECODE=1
test "$(git rev-parse HEAD)" = a56399edf46f97f8b8e4793dc2619bb2a1b7ac2f
python scripts/production_release_guard.py verify --url https://mezansalla.com
python - <<'PY'
import json, subprocess
state = json.loads(subprocess.check_output(
    ['python', 'scripts/production_release_guard.py', 'status'], text=True))
print(json.dumps(state, indent=2))
if state.get('active') is not False or state.get('incomplete_lease_directory'):
    raise SystemExit('STOP: release closure is not confirmed.')
for name in ('backend', 'frontend'):
    probe = subprocess.run(['supervisorctl', 'status', name], capture_output=True, text=True)
    fields = probe.stdout.strip().split()
    if len(fields) < 2 or fields[0] != name:
        raise SystemExit('Cannot confirm preview service state: ' + name)
    if fields[1] in {'STOPPED', 'EXITED', 'FATAL'}:
        subprocess.run(['supervisorctl', 'start', name], check=True)
    elif fields[1] not in {'RUNNING', 'STARTING'}:
        raise SystemExit('Preview service requires inspection: ' + name)
PY
MEZAN_VERIFY
