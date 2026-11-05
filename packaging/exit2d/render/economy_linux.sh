#!/usr/bin/env bash
# Actual five-service images, no swap, hard CPU/RAM and real bounded5GB disk.
set -euo pipefail
web_memory="${1:?web memory required}"
test "$web_memory" = 2g || test "$web_memory" = 512m
web_cpu=1; if test "$web_memory" = 512m; then web_cpu=.5; fi
suffix="${GITHUB_RUN_ID:-local}-${web_memory}"
network="exit2d-econ-$suffix"
mongo="exit2d-econ-mongo-$suffix"; web="exit2d-econ-web-$suffix"
reports="exit2d-econ-reports-$suffix"; sync="exit2d-econ-sync-$suffix"; accounting="exit2d-econ-accounting-$suffix"
root="$PWD/economy-evidence/$web_memory"
mkdir -p "$root" "$root/disk"
monitor=''
cleanup() {
  result=$?
  touch "$root/resources.json.stop"
  if test -n "$monitor"; then wait "$monitor" || true; fi
  docker inspect "$mongo" "$web" "$reports" "$sync" "$accounting" > "$root/final-inspect.json" 2>/dev/null || true
  for service in "$mongo" "$web" "$reports" "$sync" "$accounting"; do
    docker logs "$service" > "$root/$service.log" 2>&1 || true
  done
  docker rm -f "$mongo" "$web" "$reports" "$sync" "$accounting" >/dev/null 2>&1 || true
  docker network rm "$network" >/dev/null 2>&1 || true
  sudo umount "$root/disk" || true
  # Sparse disk image is disposable; do not upload database files.
  rm -f "$root/mongo.img"
  printf '{"exit_code":%s,"web_memory":"%s"}\n' "$result" "$web_memory" > "$root/outcome.json"
}
trap cleanup EXIT
docker build -f packaging/exit2d/render/Dockerfile --build-arg "RENDER_GIT_COMMIT=$GITHUB_SHA" -t mezan-econ:staging .
docker build -f packaging/exit2d/render/Mongo.Dockerfile -t mezan-econ:mongo .
truncate -s 5000000000 "$root/mongo.img"
mkfs.ext4 -q -F -m 0 "$root/mongo.img"
sudo mount -o loop "$root/mongo.img" "$root/disk"
docker network create --internal "$network" >/dev/null
docker run -d --name "$mongo" --network "$network" --network-alias staging-mongo \
  --memory 512m --memory-swap 512m --cpus .5 -v "$root/disk:/data/db" --tmpfs /data/configdb \
  -e MONGO_INITDB_ROOT_USERNAME=fixture -e MONGO_INITDB_ROOT_PASSWORD=synthetic-only-password \
  -e MONGO_REPLICA_KEY=syntheticOnlyReplicaKey1234567890 mezan-econ:mongo
runtime=(--network "$network" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges
  -e APP_ENV=staging -e MEZAN_SYNTHETIC_HOSTING=1 -e DB_NAME=mezan_exit2d_staging
  -e MONGO_URL=mongodb://fixture:synthetic-only-password@staging-mongo:27017
  -e MEZAN_STAGING_MONGO_HOST=staging-mongo -e JWT_SECRET=synthetic-hosting-public-key-not-for-production
  -e EMAIL_OTP_SMTP_HOST=127.0.0.1 -e EMAIL_OTP_SMTP_PORT=8025 -e EMAIL_OTP_SMTP_STARTTLS=false -e EMAIL_OTP_FROM_EMAIL=otp@example.test
  -e ADMIN_PASSWORD=Synthetic-hosting-password-only! -e FRONTEND_URL=https://staging.example.test
  -e WEBAUTHN_ORIGIN=https://staging.example.test -e WEBAUTHN_RP_ID=staging.example.test)
small=(--memory 512m --memory-swap 512m --cpus .5)
for attempt in $(seq 1 90); do
  if docker exec "$mongo" mongosh --quiet -u fixture -p synthetic-only-password --authenticationDatabase admin --eval 'quit(db.adminCommand({ping:1}).ok?0:1)' >/dev/null 2>&1; then break; fi
  sleep 1
done
docker run --rm "${runtime[@]}" --memory 2g --memory-swap 2g --cpus 1 mezan-econ:staging python /opt/render/start.py migration
docker create --name "$reports" --network-alias staging-reports "${runtime[@]}" "${small[@]}" mezan-econ:staging python /opt/render/start.py worker-reports >/dev/null
docker create --name "$web" "${runtime[@]}" --memory "$web_memory" --memory-swap "$web_memory" --cpus "$web_cpu" -e MEZAN_REPORT_WORKER_ADDRESS=staging-reports:8000 mezan-econ:staging python /opt/render/start.py web >/dev/null
for role in sync accounting; do
  name="$sync"; if test "$role" = accounting; then name="$accounting"; fi
  docker create --name "$name" "${runtime[@]}" "${small[@]}" -e MEZAN_WORKER_ENABLED=1 -e MEZAN_LEGACY_WORKERS_STOPPED=1 mezan-econ:staging python /opt/render/start.py "worker-$role" >/dev/null
done
python packaging/exit2d/render/economy_monitor.py "$root/resources.json" "$mongo" "$web" "$reports" "$sync" "$accounting" & monitor=$!
docker start "$reports" "$web" "$sync" "$accounting" >/dev/null
probe() {
  docker exec "$web" python -c "import httpx; assert httpx.get('http://127.0.0.1:10000/api/ready',timeout=3).status_code==200"
}
for attempt in $(seq 1 150); do
  if probe >/dev/null 2>&1; then break; fi
  sleep 1
done
probe
for attempt in $(seq 1 150); do
  if docker exec "$web" python -c "import httpx; assert httpx.get('http://staging-reports:8000/api/ready',timeout=3).status_code==200" >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$web" python -c "import httpx; assert httpx.get('http://staging-reports:8000/api/ready',timeout=3).status_code==200"
fixture() {
  docker run --rm "${runtime[@]:2}" --network "container:$web" --memory 2g --memory-swap 2g --cpus 1 \
    -e EXIT2D_ECONOMY_CI=1 -v "$PWD/packaging/exit2c:/evidence/acceptance:ro" \
    -v "$PWD/packaging/exit2d:/evidence/tools:ro" --entrypoint python mezan-econ:staging \
    /evidence/tools/render/economy_acceptance.py "$1"
}
for role in sync accounting; do
  if docker run --rm "${runtime[@]}" "${small[@]}" -e MEZAN_WORKER_ENABLED=1 -e MEZAN_LEGACY_WORKERS_STOPPED=1 mezan-econ:staging python /opt/render/start.py "worker-$role" > "$root/duplicate-$role.log" 2>&1; then
    echo 'FAIL duplicate role accepted'; exit 1
  fi
  grep -q 'another worker holds the fenced claim' "$root/duplicate-$role.log"
done
fixture setup | tee "$root/setup.jsonl"
docker exec -i "$sync" python - < packaging/exit2d/render/economy_snap.py > "$root/snap.jsonl" 2>&1 & snap_pid=$!
fixture load | tee "$root/operations.jsonl"
wait "$snap_pid"
docker exec -i "$accounting" python - < packaging/exit2d/render/economy_accounting.py | tee "$root/accounting-before.jsonl"
fixture growth | tee "$root/growth.jsonl"
df -B1 "$root/disk" > "$root/disk-usage.txt"
for service in "$reports" "$sync" "$accounting"; do
  test "$(docker inspect -f '{{.State.Running}}' "$service")" = true
  docker kill "$service" >/dev/null
  fixture outage
  sleep 18
  docker start "$service" >/dev/null
  sleep 20
  test "$(docker inspect -f '{{.State.Running}}' "$service")" = true
  probe
done
docker exec -i "$accounting" python - < packaging/exit2d/render/economy_accounting.py | tee "$root/accounting-after.jsonl"
docker kill "$web" >/dev/null
docker start "$web" >/dev/null
for attempt in $(seq 1 150); do
  if probe >/dev/null 2>&1; then break; fi
  sleep 1
done
probe
fixture outage
python - "$root/disk" <<'PY'
import os,sys,json
s=os.statvfs(sys.argv[1]); total=s.f_blocks*s.f_frsize; free=s.f_bavail*s.f_frsize
assert total <= 5_000_000_000 and free > 1_000_000_000, (total,free)
print(json.dumps({'kind':'bounded_five_gb_volume','total_bytes':total,'available_bytes':free}),flush=True)
PY
sleep 30
for service in "$web" "$reports" "$sync" "$accounting"; do
  test "$(docker inspect -f '{{.State.Running}}' "$service")" = true
  docker stop --time 30 "$service" >/dev/null
  test "$(docker inspect -f '{{.State.ExitCode}}' "$service")" = 0
done
touch "$root/resources.json.stop"; wait "$monitor"; monitor=''
python - "$root/resources.json" <<'PY'
import json,sys
data=json.load(open(sys.argv[1]))
for name,row in data['services'].items():
    assert row['samples']>0, name
    assert not row['oom'] and not row['oom_kill'] and not row['docker_oom_seen'], (name,row)
    assert row['restarts_max']==0, (name,row)
print(json.dumps(data),flush=True)
PY
echo 'PASS bounded five-service economy acceptance'
