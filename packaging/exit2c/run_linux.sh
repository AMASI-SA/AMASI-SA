#!/usr/bin/env bash
set -euo pipefail
# One standard Ubuntu job. Runtime cannot route outside the disposable namespace.
docker build --no-cache -f packaging/exit2c/Dockerfile -t mezan-exit2c:candidate .
docker build --no-cache -f packaging/exit2c/tests.Dockerfile -t mezan-exit2c:tests .
docker pull mongo:7.0.16@sha256:c630c59342c1493d50345136df2af14a76b9e827dd5316bfabee07a0880a5f3a
suffix="${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}"
mongo="exit2c-mongo-$suffix"
web1="exit2c-web1-$suffix"
web2="exit2c-web2-$suffix"
probe="exit2c-probe-$suffix"
worker="exit2c-worker-$suffix"
cleanup() {
  result=$?
  if test "$result" != 0; then
    for name in "$web1" "$web2" "$worker"; do docker logs "$name" 2>&1 | tail -n 45 || true; done
  fi
  docker unpause "$mongo" >/dev/null 2>&1 || true
  docker rm -f "$web1" "$web2" "$probe" "$worker" "$mongo" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker run -d --name "$mongo" --network none --tmpfs /data/db --tmpfs /data/configdb \
  mongo:7.0.16@sha256:c630c59342c1493d50345136df2af14a76b9e827dd5316bfabee07a0880a5f3a --bind_ip 127.0.0.1 --quiet
test "$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$mongo")" = none
for attempt in $(seq 1 30); do
  if docker exec "$mongo" mongosh --quiet --eval 'quit(db.adminCommand({ping:1}).ok?0:1)' >/dev/null 2>&1; then break; fi
  sleep 1
done
runtime=(--network "container:$mongo" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --memory 3g --cpus 2)
docker run -d --name "$probe" "${runtime[@]}" --entrypoint python mezan-exit2c:candidate -c 'import time; time.sleep(1200)'
life() { docker exec "$probe" python /opt/acceptance/lifecycle.py "$1"; }
accept() { docker exec "$probe" python /opt/acceptance/acceptance.py "$1"; }
docker run --rm "${runtime[@]}" --entrypoint python mezan-exit2c:tests /opt/acceptance/regressions.py
life duplicate-fixture
if docker run --rm "${runtime[@]}" mezan-exit2c:candidate migration; then
  echo 'FAIL duplicate fixture did not block migration'; exit 1
fi
# Partial migration must not announce readiness despite a healthy Mongo.
life profile
docker run -d --name "$web1" "${runtime[@]}" mezan-exit2c:candidate web --port 8001
blocked=false
for attempt in $(seq 1 30); do
  if docker exec "$probe" python -c "import httpx; assert httpx.get('http://127.0.0.1:8001/api/ready',timeout=1).status_code==503" >/dev/null 2>&1; then blocked=true; break; fi
  sleep 1
done
test "$blocked" = true
life no-writes
docker stop --time 10 "$web1" >/dev/null
test "$(docker inspect -f '{{.State.ExitCode}}' "$web1")" = 0
docker rm "$web1" >/dev/null
echo 'PASS partial migration blocks web readiness without startup writes'
life repair-fixture
docker run --rm "${runtime[@]}" mezan-exit2c:candidate migration
life completed
life profile
docker run --rm "${runtime[@]}" mezan-exit2c:candidate migration
life no-writes
accept setup
life profile
start_webs() {
  docker run -d --name "$web1" "${runtime[@]}" mezan-exit2c:candidate web --port 8001
  docker run -d --name "$web2" "${runtime[@]}" mezan-exit2c:candidate web --port 8002
  for port in 8001 8002; do
    ready=false
    for attempt in $(seq 1 40); do
      if docker exec "$probe" python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:$port/api/ready',timeout=1).status==200" >/dev/null 2>&1; then ready=true; break; fi
      sleep 1
    done
    if test "$ready" != true; then docker logs "$web1"; docker logs "$web2"; exit 1; fi
  done
}
stop_webs() {
  docker stop --time 10 "$web1" "$web2" >/dev/null
  for name in "$web1" "$web2"; do
    test "$(docker inspect -f '{{.State.ExitCode}}' "$name")" = 0
    docker logs "$name" 2>&1 | tail -n 10
    docker rm "$name" >/dev/null
  done
}
start_webs
life no-writes
accept http
docker pause "$mongo" >/dev/null
sleep 6
accept mongo-down
docker unpause "$mongo" >/dev/null
for attempt in $(seq 1 30); do
  if docker exec "$probe" python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8001/api/ready',timeout=1).status==200" >/dev/null 2>&1; then break; fi
  sleep 1
done
life profile
stop_webs
start_webs
life no-writes
accept after-restart
life profile
stop_webs
life no-writes
# Worker refuses unarmed execution, holds one stable cross-version fence,
# stops on fence loss, and can then restart and shut down cleanly.
if docker run --rm "${runtime[@]}" mezan-exit2c:candidate worker; then echo 'FAIL unarmed worker'; exit 1; fi
docker run -d --name "$worker" "${runtime[@]}" -e MEZAN_WORKER_ENABLED=1 mezan-exit2c:candidate worker
held=false
for attempt in $(seq 1 30); do
  if life worker-held >/dev/null 2>&1; then held=true; break; fi
  sleep 1
done
if test "$held" != true; then docker logs "$worker"; exit 1; fi
test "$(docker inspect -f '{{.State.Running}}' "$worker")" = true
if docker run --rm "${runtime[@]}" -e MEZAN_WORKER_ENABLED=1 mezan-exit2c:candidate worker; then echo 'FAIL duplicate worker'; exit 1; fi
life lose-worker-fence
for attempt in $(seq 1 20); do
  if test "$(docker inspect -f '{{.State.Running}}' "$worker")" = false; then break; fi
  sleep 1
done
test "$(docker inspect -f '{{.State.Running}}' "$worker")" = false
test "$(docker inspect -f '{{.State.ExitCode}}' "$worker")" != 0
docker logs "$worker" 2>&1 | tail -n 12
docker rm "$worker" >/dev/null
life expire-worker
docker run -d --name "$worker" "${runtime[@]}" -e MEZAN_WORKER_ENABLED=1 mezan-exit2c:candidate worker
sleep 9
test "$(docker inspect -f '{{.State.Running}}' "$worker")" = true
docker stop --time 10 "$worker" >/dev/null
test "$(docker inspect -f '{{.State.ExitCode}}' "$worker")" = 0
life no-worker
echo 'PASS worker explicit arming, singleton fencing, loss cancellation, restart and TERM'
docker run --rm "${runtime[@]}" -e MEZAN_WORKER_ENABLED=1 --entrypoint python mezan-exit2c:candidate /opt/acceptance/worker_shutdown.py --linux
echo 'PASS EXIT-2C scoped acceptance; temporary containers cleaned next'
