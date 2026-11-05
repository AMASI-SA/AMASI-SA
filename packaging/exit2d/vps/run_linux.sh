#!/usr/bin/env bash
# Private synthetic Compose stack. No host/cloud/Production credentials accepted.
set -euo pipefail
umask 077
suffix="${GITHUB_RUN_ID:-local}"
project="exit2dvps$suffix"
root="$PWD/vps-evidence"
scratch="${RUNNER_TEMP:?isolated runner temp required}/$project"
mkdir -p "$root" "$scratch/disk" "$scratch/restore-disk"
export MEZAN_SOURCE_SHA="$GITHUB_SHA" MEZAN_SECRETS_DIR="$scratch/secrets" EXIT2D_ECONOMY_CI=1
export MEZAN_CI_DISK="$scratch/disk" MEZAN_LOCAL_PORT=10000
monitor=''
driver=$(docker info --format '{{.CgroupDriver}}')
export MEZAN_CGROUP_PARENT="${project}.slice"
if test "$driver" = systemd; then
  sudo systemctl start "$MEZAN_CGROUP_PARENT"
  sudo systemctl set-property --runtime "$MEZAN_CGROUP_PARENT" CPUQuota=400% MemoryMax=8G MemorySwapMax=0
  parent_path="/sys/fs/cgroup$(systemctl show "$MEZAN_CGROUP_PARENT" -p ControlGroup --value)"
elif test "$driver" = cgroupfs; then
  parent_path="/sys/fs/cgroup/$MEZAN_CGROUP_PARENT"
  sudo mkdir "$parent_path"
  echo '400000 100000' | sudo tee "$parent_path/cpu.max" >/dev/null
  echo 8589934592 | sudo tee "$parent_path/memory.max" >/dev/null
  echo 0 | sudo tee "$parent_path/memory.swap.max" >/dev/null
else
  echo 'FAIL unverified cgroup driver'; exit 1
fi
compose() {
  sudo --preserve-env=MEZAN_SOURCE_SHA,MEZAN_SECRETS_DIR,MEZAN_CGROUP_PARENT,MEZAN_CI_DISK,MEZAN_LOCAL_PORT,EXIT2D_ECONOMY_CI \
    docker compose -p "$project" -f packaging/exit2d/vps/compose.yaml -f packaging/exit2d/vps/ci.override.yaml "$@"
}
cleanup() {
  result=$?
  touch "$root/resources.json.stop"
  if test -n "$monitor"; then wait "$monitor" || true; fi
  compose logs --no-color --tail 80 > "$root/compose.log" 2>&1 || true
  compose ps --all --format json > "$root/final-state.json" 2>/dev/null || true
  for file in memory.current memory.peak memory.events cpu.max cpu.stat memory.max memory.swap.max; do
    cat "$parent_path/$file" > "$root/parent-$file.txt" 2>/dev/null || true
  done
  docker rm -f "$project-snapshot" "$project-verify" >/dev/null 2>&1 || true
  compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  sudo umount "$scratch/disk" 2>/dev/null || true
  sudo umount "$scratch/restore-disk" 2>/dev/null || true
  # Exact, newly-created runner files only; do not upload archives/secret files.
  rm -f "$scratch/mongo.img" "$scratch/restore.img" "$scratch/synthetic.archive"
  printf '{"exit_code":%s}\n' "$result" > "$root/outcome.json"
}
trap cleanup EXIT
sudo python packaging/exit2d/vps/init_secrets.py "$MEZAN_SECRETS_DIR" --ci-fixture
for name in mongo restore; do
  truncate -s 5000000000 "$scratch/$name.img"
  mkfs.ext4 -q -F -m 0 "$scratch/$name.img"
done
sudo mount -o loop "$scratch/mongo.img" "$scratch/disk"
compose config --format json > "$root/compose-config.json"
python packaging/exit2d/vps/test_compose.py "$root/compose-config.json"
compose build mongo web
compose up -d --no-build --wait --wait-timeout 240
mongo=$(compose ps -q mongo); web=$(compose ps -q web); reports=$(compose ps -q reports)
sync=$(compose ps -q sync); accounting=$(compose ps -q accounting)
docker inspect "$mongo" "$web" "$reports" "$sync" "$accounting" > "$root/container-constraints.json"
python packaging/exit2d/render/economy_monitor.py "$root/resources.json" "$mongo" "$web" "$reports" "$sync" "$accounting" & monitor=$!
# Check actual nested cgroups: caps must apply collectively, not only on paper.
for id in "$mongo" "$web" "$reports" "$sync" "$accounting"; do
  pid=$(docker inspect -f '{{.State.Pid}}' "$id")
  grep -q "$MEZAN_CGROUP_PARENT" "/proc/$pid/cgroup"
done
fixture() {
  # An external load generator shares only Web's network namespace. Its resource
  # use is outside the simulated VPS service budget, like an external browser.
  docker run --rm --network "container:$web" --read-only --tmpfs /tmp --cap-drop ALL \
    --security-opt no-new-privileges --memory 1g --memory-swap 1g --cpus 1 \
    -e APP_ENV=staging -e MEZAN_SYNTHETIC_HOSTING=1 -e DB_NAME=mezan_exit2d_staging \
    -e MEZAN_STAGING_MONGO_HOST=staging-mongo -e EXIT2D_ECONOMY_CI=1 \
    -e FRONTEND_URL=https://staging.example.test -e WEBAUTHN_ORIGIN=https://staging.example.test -e WEBAUTHN_RP_ID=staging.example.test \
    -v "$MEZAN_SECRETS_DIR/jwt_secret:/run/secrets/jwt_secret:ro" \
    -v "$MEZAN_SECRETS_DIR/admin_password:/run/secrets/admin_password:ro" \
    -v "$MEZAN_SECRETS_DIR/mfa_bootstrap_code:/run/secrets/mfa_bootstrap_code:ro" \
    -v "$MEZAN_SECRETS_DIR/mongo_uri:/run/secrets/mongo_uri:ro" \
    -v "$PWD/packaging/exit2d/vps/entry.py:/opt/vps/entry.py:ro" \
    -v "$PWD/packaging/exit2d:/evidence/tools:ro" -v "$PWD/packaging/exit2c:/evidence/acceptance:ro" \
    --entrypoint python "mezan-vps:$GITHUB_SHA" /opt/vps/entry.py test /evidence/tools/render/economy_acceptance.py "$1"
}
fixture setup | tee "$root/setup.jsonl"
compose exec -T sync python /opt/vps/entry.py test - < packaging/exit2d/render/economy_snap.py > "$root/snap.jsonl" 2>&1 & snap_pid=$!
fixture load | tee "$root/operations.jsonl"
wait "$snap_pid"
compose exec -T accounting python /opt/vps/entry.py test - < packaging/exit2d/render/economy_accounting.py | tee "$root/accounting-before.jsonl"
fixture growth | tee "$root/growth.jsonl"
for role in sync accounting; do
  if compose run --rm --no-deps -T "$role" > "$root/duplicate-$role.log" 2>&1; then
    echo 'FAIL duplicate worker accepted'; exit 1
  fi
  grep -q 'another worker holds the fenced claim' "$root/duplicate-$role.log"
done
for role in reports sync accounting; do
  compose kill -s SIGKILL "$role"
  fixture outage
  sleep 18
  compose start "$role"
  sleep 25
  test "$(docker inspect -f '{{.State.Running}}' "$(compose ps -q "$role")")" = true
done
compose exec -T accounting python /opt/vps/entry.py test - < packaging/exit2d/render/economy_accounting.py | tee "$root/accounting-after.jsonl"
compose restart web
compose up -d --no-build --wait --wait-timeout 180 web
fixture outage
# Mongo daemon crash/restart, retaining the SAME volume and credentials.
compose restart mongo
compose up -d --no-build --wait --wait-timeout 180
fixture outage
df -B1 "$scratch/disk" > "$root/disk-usage.txt"
touch "$root/resources.json.stop"; wait "$monitor"; monitor=''
python - "$root/resources.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
for name,row in d['services'].items():
    assert row['samples']>0 and not row['oom'] and not row['oom_kill'] and not row['docker_oom_seen'], (name,row)
print('PASS bounded Compose memory and OOM evidence')
PY
# Freeze EVERY app writer before a consistent dump; TTL is test-only disabled.
compose stop web reports sync accounting
compose exec -T web true >/dev/null 2>&1 && exit 1 || true
mongo_eval="const fs=require('fs'); db.getSiblingDB('admin').auth(fs.readFileSync('/run/secrets/mongo_username','utf8').trim(),fs.readFileSync('/run/secrets/mongo_password','utf8').trim());"
compose exec -T mongo mongosh --quiet --eval "$mongo_eval db.adminCommand({setParameter:1,ttlMonitorEnabled:false})" >/dev/null
# A short-lived copy of Web is only a private CI transport for snapshot helpers.
compose run --no-deps -d --name "$project-snapshot" --entrypoint python web -c 'import time; time.sleep(600)' >/dev/null
snapshot_id=$(docker inspect -f '{{.Id}}' "$project-snapshot")
docker exec "$snapshot_id" python /opt/vps/entry.py test /opt/vps/restore.py seed /tmp/receipt.json
docker exec "$snapshot_id" python /opt/vps/entry.py test /opt/vps/restore.py snapshot /tmp/receipt.json
docker exec "$snapshot_id" cat /tmp/receipt.json > "$root/restore-expected.json"
compose exec -T mongo bash -c 'mongodump --host 127.0.0.1 -u "$(cat /run/secrets/mongo_username)" -p "$(cat /run/secrets/mongo_password)" --authenticationDatabase admin --db mezan_exit2d_staging --archive --quiet' > "$scratch/synthetic.archive"
docker rm -f "$snapshot_id" >/dev/null
compose down --volumes --remove-orphans
sudo umount "$scratch/disk"
# New empty volume on a separate bounded filesystem; archive is the only input.
sudo mount -o loop "$scratch/restore.img" "$scratch/restore-disk"
export MEZAN_CI_DISK="$scratch/restore-disk"
compose up -d --no-build --wait --wait-timeout 180 mongo
compose exec -T mongo mongosh --quiet --eval "$mongo_eval rs.initiate({_id:'rs0',members:[{_id:0,host:'staging-mongo:27017'}]})" >/dev/null
for attempt in $(seq 1 60); do
  if compose exec -T mongo mongosh --quiet --eval "$mongo_eval quit(db.hello().isWritablePrimary?0:1)" >/dev/null 2>&1; then break; fi
  sleep 1
done
compose exec -T mongo mongosh --quiet --eval "$mongo_eval db.adminCommand({setParameter:1,ttlMonitorEnabled:false})" >/dev/null
compose exec -T mongo bash -c 'mongorestore --host 127.0.0.1 -u "$(cat /run/secrets/mongo_username)" -p "$(cat /run/secrets/mongo_password)" --authenticationDatabase admin --archive --quiet' < "$scratch/synthetic.archive"
compose run --no-deps -d --name "$project-verify" --entrypoint python web -c 'import time; time.sleep(600)' >/dev/null
verify_id=$(docker inspect -f '{{.Id}}' "$project-verify")
docker exec -i "$verify_id" sh -c 'cat > /tmp/receipt.json' < "$root/restore-expected.json"
docker exec "$verify_id" python /opt/vps/entry.py test /opt/vps/restore.py verify /tmp/receipt.json | tee "$root/restore.jsonl"
docker rm -f "$verify_id" >/dev/null
compose up -d --no-build --wait --wait-timeout 240
web=$(compose ps -q web)
fixture outage
compose exec -T accounting python /opt/vps/entry.py test - < packaging/exit2d/render/economy_accounting.py | tee "$root/accounting-restored.jsonl"
compose stop web reports sync accounting
for role in web reports sync accounting; do
  test "$(docker inspect -f '{{.State.ExitCode}}' "$(compose ps -a -q "$role")")" = 0
done
echo 'PASS actual Compose VPS2 envelope, employee+reports+Snap, duplicate denial, five-service recovery, fresh-volume full restore and drain'
