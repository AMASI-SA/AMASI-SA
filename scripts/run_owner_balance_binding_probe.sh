#!/usr/bin/env bash
# Isolated read-only owner financial identity diagnostic; no application writes.
set -euo pipefail

if [[ "$#" -ne 1 || "$1" != *"@"* ]]; then
  printf '%s\n' '{"read_only":true,"result":"OWNER_EMAIL_INVALID"}'
  exit 2
fi
if [[ ! -f /tmp/amasi-owner-balance-binding.py ]]; then
  printf '%s\n' '{"read_only":true,"result":"DIAGNOSTIC_SCRIPT_NOT_FOUND"}'
  exit 2
fi
if [[ ! -f /app/backend/.env && ( -z "${MONGO_URL:-}" || -z "${DB_NAME:-}" ) ]]; then
  printf '%s\n' '{"read_only":true,"result":"DB_CONFIGURATION_UNAVAILABLE"}'
  exit 0
fi

# Use a Python already provisioned for the service, if present.
# Never install into /app, system Python, or the running backend environment.
selected=""
for python in \
    "${VIRTUAL_ENV:-/nonexistent}/bin/python" \
    /app/backend/.venv/bin/python \
    /app/backend/venv/bin/python \
    /app/.venv/bin/python \
    /app/venv/bin/python \
    /opt/venv/bin/python \
    /root/.venv/bin/python \
    /usr/local/bin/python \
    /usr/bin/python3
do
  if [[ -x "$python" ]] && "$python" -B -c 'from motor.motor_asyncio import AsyncIOMotorClient' >/dev/null 2>&1; then
    selected="$python"
    break
  fi
done

if [[ -z "$selected" ]]; then
  # Backend's approved versions only, isolated under /tmp. No changes
  # to production dependencies or version-pinned application packages.
  selected=/tmp/amasi-owner-diagnostic-venv/bin/python
  if [[ ! -x "$selected" ]]; then
    if ! python3 -m venv /tmp/amasi-owner-diagnostic-venv >/dev/null 2>&1; then
      printf '%s\n' '{"read_only":true,"result":"ISOLATED_VENV_UNAVAILABLE"}'
      exit 0
    fi
  fi
  if ! "$selected" -B -c 'from motor.motor_asyncio import AsyncIOMotorClient' >/dev/null 2>&1; then
    if ! "$selected" -m pip --isolated install --disable-pip-version-check --no-input --quiet \
          'motor==3.7.1' 'pymongo==4.18.3' 'python-dotenv==1.2.2' >/dev/null 2>&1; then
      printf '%s\n' '{"read_only":true,"result":"ISOLATED_DRIVER_SETUP_UNAVAILABLE"}'
      exit 0
    fi
  fi
fi

exec "$selected" -B /tmp/amasi-owner-balance-binding.py --owner-email "$1"
