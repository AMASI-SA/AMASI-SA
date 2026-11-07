#!/usr/bin/env bash
set -euo pipefail
export MONGO_INITDB_ROOT_USERNAME="$(cat /run/secrets/mongo_username)"
export MONGO_INITDB_ROOT_PASSWORD="$(cat /run/secrets/mongo_password)"
export MONGO_REPLICA_KEY="$(cat /run/secrets/mongo_replica_key)"
# Mongo logs invalid key content itself. Reject malformed files here without
# exposing their value, before mongod can consume them.
if ! [[ "$MONGO_REPLICA_KEY" =~ ^[A-Za-z0-9+/]+$ ]] || test "${#MONGO_REPLICA_KEY}" -lt 6 || test "${#MONGO_REPLICA_KEY}" -gt 1024; then
  echo 'invalid synthetic replica-key format' >&2
  exit 2
fi
exec bash /opt/mongo-start.sh
