#!/usr/bin/env bash
set -euo pipefail
# Explicit preflight blocks the current runtime contract BEFORE image builds,
# credentials, Mongo or the application. Never bypass via APP_ENV or key alias.
export SALLA_API_BASE=http://127.0.0.1:8093/admin/v2
export SALLA_AUTH_BASE=http://127.0.0.1:8093
python packaging/exit2c/preparation_provider_preflight.py
# This continuation is prepared, not currently accepted/executable past guard.
# Each scenario gets a fresh network-none Mongo instance and fresh credentials;
# DB_NAME stays mezan_exit2c because the immutable runtime requires that name.
for scenario in deny unavailable success; do
  export EXIT2D_SIM_MODE="$scenario"
  export EXIT2D_SIM_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  export SALLA_TOKEN_ENC_KEY="$(python -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
  if test "$scenario" = success; then
    bash packaging/exit2c/run_linux.sh --preparation-only
  else
    bash packaging/exit2c/run_linux.sh --preparation-denied
  fi
  unset EXIT2D_SIM_TOKEN SALLA_TOKEN_ENC_KEY EXIT2D_SIM_MODE
done
