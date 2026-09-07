#!/usr/bin/env bash
set -euo pipefail
# The real guard is executed inside network=none before any application import.
export SALLA_API_BASE=http://127.0.0.1:8093/admin/v2
export SALLA_AUTH_BASE=http://127.0.0.1:8093
# Host validates addresses only; Linux namespace validation runs in the container.
python -c 'import sys,os; sys.path.insert(0,"packaging/exit2c"); from salla_http_simulator import validate_addresses; validate_addresses(os.environ)'
# Full application acceptance remains pending Linux execution.
# Each scenario gets a fresh network-none Mongo instance and fresh credentials;
# DB_NAME stays mezan_exit2c because the immutable runtime requires that name.
for scenario in deny unavailable success; do
  export EXIT2D_SIM_MODE="$scenario"
  export EXIT2D_SIM_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  export MEZAN_ACCEPTANCE_PROFILE=salla_http_simulator_v1
  export SALLA_TOKEN_ENC_KEY="$(python -c 'import base64; print(base64.urlsafe_b64encode(bytes(range(32))).decode("ascii"))')"
  if test "$scenario" = success; then
    bash packaging/exit2c/run_linux.sh --preparation-only
  else
    bash packaging/exit2c/run_linux.sh --preparation-denied
  fi
  unset EXIT2D_SIM_TOKEN SALLA_TOKEN_ENC_KEY EXIT2D_SIM_MODE MEZAN_ACCEPTANCE_PROFILE
done
