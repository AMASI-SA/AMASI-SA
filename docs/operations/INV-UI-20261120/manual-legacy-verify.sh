bash <<'MEZAN_VERIFY' 2>&1 | tee /tmp/mezan-legacy-location-verify.log || printf '%s\n' 'STOP: verification failed; preserve the log.'
set -euo pipefail
cd /app
python scripts/production_release_guard.py verify --url https://mezansalla.com
python scripts/production_release_guard.py status
MEZAN_VERIFY
