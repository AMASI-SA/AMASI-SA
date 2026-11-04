#!/usr/bin/env bash
set -euo pipefail
test -n "${MONGO_REPLICA_KEY:-}"
umask 077
printf '%s' "$MONGO_REPLICA_KEY" > /tmp/mezan-mongo-key
chown mongodb:mongodb /tmp/mezan-mongo-key
chmod 400 /tmp/mezan-mongo-key
unset MONGO_REPLICA_KEY
exec /usr/local/bin/docker-entrypoint.sh mongod --bind_ip_all --auth --replSet rs0 --keyFile /tmp/mezan-mongo-key
