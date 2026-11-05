#!/usr/bin/env bash
set -euo pipefail
export MONGO_INITDB_ROOT_USERNAME="$(cat /run/secrets/mongo_username)"
export MONGO_INITDB_ROOT_PASSWORD="$(cat /run/secrets/mongo_password)"
export MONGO_REPLICA_KEY="$(cat /run/secrets/mongo_replica_key)"
exec bash /opt/mongo-start.sh
