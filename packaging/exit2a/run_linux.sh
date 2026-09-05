#!/usr/bin/env bash
set -euo pipefail
# Existing standard GitHub Ubuntu runner only. Never run on a Production host.
docker build --no-cache -f packaging/exit2a/Dockerfile -t mezan-exit2a:rehearsal .
docker pull mongo:7.0.16
mongo_name="exit2a-mongo-${GITHUB_RUN_ID}"
web_name="exit2a-web-${GITHUB_RUN_ID}"
cleanup() { docker rm -f "$web_name" "$mongo_name" >/dev/null 2>&1 || true; }
trap cleanup EXIT
docker run -d --name "$mongo_name" --network none --tmpfs /data/db --tmpfs /data/configdb mongo:7.0.16 --bind_ip 127.0.0.1 --quiet
test "$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$mongo_name")" = none
for attempt in $(seq 1 30); do
  if docker exec "$mongo_name" mongosh --quiet --eval 'quit(db.adminCommand({ping:1}).ok ? 0 : 1)' >/dev/null 2>&1; then break; fi
  sleep 1
done
docker exec "$mongo_name" mongosh --quiet --eval 'quit(db.adminCommand({ping:1}).ok ? 0 : 1)'
runtime=(--network "container:$mongo_name" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --memory 3g --cpus 2)
docker run --rm "${runtime[@]}" --entrypoint python mezan-exit2a:rehearsal /opt/rehearsal/acceptance.py
for role in worker migration; do
  if docker run --rm "${runtime[@]}" mezan-exit2a:rehearsal "$role"; then
    echo 'FAIL: forbidden role accepted'; exit 1
  fi
done
# Real process startup/TERM/restart. Profile snapshots cover both fresh imports
# and lifecycle; only health/readiness requests occur during these windows.
for cycle in 1 2; do
  docker exec "$mongo_name" mongosh --quiet --eval 'const d=db.getSiblingDB("mezan_exit2a"); d.runCommand({profile:0}); d.system.profile.drop(); d.runCommand({profile:2});'
  docker run -d --name "$web_name" "${runtime[@]}" mezan-exit2a:rehearsal
  mode="$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$web_name")"
  test "$mode" = "container:$mongo_name" || test "$mode" = "container:$(docker inspect -f '{{.Id}}' "$mongo_name")"
  healthy=false
  for attempt in $(seq 1 30); do
    if docker exec "$web_name" python -c 'import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:8001/api/ready",timeout=1).status==200' >/dev/null 2>&1; then healthy=true; break; fi
    sleep 1
  done
  if test "$healthy" != true; then docker logs "$web_name"; exit 1; fi
  docker stop --time 10 "$web_name" >/dev/null
  test "$(docker inspect -f '{{.State.ExitCode}}' "$web_name")" = 0
  docker logs "$web_name" 2>&1 | tail -n 12
  docker rm "$web_name" >/dev/null
  docker exec "$mongo_name" mongosh --quiet --eval 'const d=db.getSiblingDB("mezan_exit2a"); const writes=d.system.profile.countDocuments({$or:[{op:{$in:["insert","update","remove"]}},... ["insert","update","delete","create","createIndexes","drop","collMod","findAndModify"].map(k=>({["command."+k]:{$exists:true}}))]}); if(writes) throw Error("startup/shutdown wrote to Mongo"); print("PASS: restart cycle, zero application lifecycle writes");'
done
echo 'PASS: real process restart/TERM; disposable tmpfs Mongo; no external interface; cleanup follows'
