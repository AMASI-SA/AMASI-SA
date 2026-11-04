#!/usr/bin/env bash
# Actual hosting images; no published ports, credentials or Production data.
set -euo pipefail
suffix="${GITHUB_RUN_ID:-local}-$$"
network="exit2d-host-$suffix"
mongo="exit2d-host-mongo-$suffix"
web="exit2d-host-web-$suffix"
reports="exit2d-host-reports-$suffix"
sync="exit2d-host-sync-$suffix"
accounting="exit2d-host-accounting-$suffix"
cleanup() {
  result=$?
  if test "$result" != 0; then
    for service in "$mongo" "$web" "$reports" "$sync" "$accounting"; do docker logs "$service" 2>&1 | tail -n 25 || true; done
  fi
  docker rm -f "$mongo" "$web" "$reports" "$sync" "$accounting" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker build -f packaging/exit2d/render/Dockerfile --build-arg "RENDER_GIT_COMMIT=$GITHUB_SHA" -t mezan-host:staging .
docker build -f packaging/exit2d/render/Mongo.Dockerfile -t mezan-host:mongo .
docker network create --internal "$network" >/dev/null
docker run -d --name "$mongo" --network "$network" --network-alias staging-mongo --tmpfs /data/db --tmpfs /data/configdb \
  -e MONGO_INITDB_ROOT_USERNAME=fixture -e MONGO_INITDB_ROOT_PASSWORD=synthetic-only-password \
  -e MONGO_REPLICA_KEY=syntheticOnlyReplicaKey1234567890 mezan-host:mongo
runtime=(--network "$network" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --memory 3g --cpus 2
  -e APP_ENV=staging -e MEZAN_SYNTHETIC_HOSTING=1 -e DB_NAME=mezan_exit2d_staging
  -e MONGO_URL=mongodb://fixture:synthetic-only-password@staging-mongo:27017
  -e MEZAN_STAGING_MONGO_HOST=staging-mongo -e JWT_SECRET=synthetic-hosting-public-key-not-for-production
  -e EMAIL_OTP_SMTP_HOST=127.0.0.1 -e EMAIL_OTP_SMTP_PORT=8025 -e EMAIL_OTP_SMTP_STARTTLS=false -e EMAIL_OTP_FROM_EMAIL=otp@example.test
  -e ADMIN_PASSWORD=Synthetic-hosting-password-only! -e FRONTEND_URL=https://staging.example.test
  -e WEBAUTHN_ORIGIN=https://staging.example.test -e WEBAUTHN_RP_ID=staging.example.test)
for attempt in $(seq 1 60); do
  if docker exec "$mongo" mongosh --quiet -u fixture -p synthetic-only-password --authenticationDatabase admin --eval 'quit(db.adminCommand({ping:1}).ok?0:1)' >/dev/null 2>&1; then break; fi
  sleep 1
done
docker run --rm "${runtime[@]}" mezan-host:staging migration
docker run -d --name "$reports" --network-alias staging-reports "${runtime[@]}" mezan-host:staging worker-reports
docker run -d --name "$web" --network-alias staging-web "${runtime[@]}" -e MEZAN_REPORT_WORKER_ADDRESS=staging-reports:8000 mezan-host:staging web
for role in sync accounting; do
  name="$sync"; if test "$role" = accounting; then name="$accounting"; fi
  docker run -d --name "$name" "${runtime[@]}" -e MEZAN_WORKER_ENABLED=1 -e MEZAN_LEGACY_WORKERS_STOPPED=1 mezan-host:staging "worker-$role"
done
probe() {
  docker run --rm "${runtime[@]}" --entrypoint python mezan-host:staging -c \
    "import httpx; r=httpx.get('http://staging-web:10000$1', timeout=5); assert r.status_code==$2, r.status_code"
}
for attempt in $(seq 1 90); do
  if probe /api/ready 200 >/dev/null 2>&1; then break; fi
  sleep 1
done
probe /api/ready 200
probe / 200
probe /api/auth/me 401
docker run --rm "${runtime[@]}" --entrypoint python mezan-host:staging -c \
  "import httpx; r=httpx.get('http://staging-web:10000/build-meta.json'); assert r.headers['content-type'].startswith('application/json'); assert r.json()['source_sha']=='$GITHUB_SHA'; assert httpx.get('http://staging-reports:8000/api/auth/me').status_code==404"
for service in "$reports" "$sync" "$accounting"; do
  sleep 5
  test "$(docker inspect -f '{{.State.Running}}' "$service")" = true
  docker kill "$service" >/dev/null
  probe /api/ready 200
  probe /api/auth/me 401
  docker start "$service" >/dev/null
  # The killed worker's fenced claim expires after 15 seconds. A supervisor
  # retry is necessary if the immediate restart correctly refuses old ownership.
  sleep 18
  if test "$(docker inspect -f '{{.State.Running}}' "$service")" = false; then docker start "$service" >/dev/null; fi
  sleep 8
  test "$(docker inspect -f '{{.State.Running}}' "$service")" = true
  probe /api/ready 200
done
docker kill "$web" >/dev/null
docker start "$web" >/dev/null
for attempt in $(seq 1 60); do
  if probe /api/ready 200 >/dev/null 2>&1; then break; fi
  sleep 1
done
probe /api/ready 200
for service in "$web" "$reports" "$sync" "$accounting"; do
  docker stop --time 30 "$service" >/dev/null
  test "$(docker inspect -f '{{.State.ExitCode}}' "$service")" = 0
done
echo 'PASS actual staging images: frontend/proxy, authenticated Mongo migration, private reports, four independent crash/restart and drain scenarios'
