#!/usr/bin/env bash
# Disposable CI only. Reuses accepted images, Compose, fixtures and benchmarks.
set -euo pipefail
umask 077
test "${GITHUB_ACTIONS:-}" = true
project="exit2dhost${GITHUB_RUN_ID}"
scratch="$RUNNER_TEMP/$project"
mkdir -p "$scratch/disk" host-app-evidence
export MEZAN_SOURCE_SHA="$GITHUB_SHA" MEZAN_SECRETS_DIR="$scratch/secrets" EXIT2D_ECONOMY_CI=1
export MEZAN_CI_DISK="$scratch/disk" MEZAN_CGROUP_PARENT="$project.slice"
sudo systemctl start "$MEZAN_CGROUP_PARENT"
sudo systemctl set-property --runtime "$MEZAN_CGROUP_PARENT" CPUQuota=400% MemoryMax=8G MemorySwapMax=0
compose() {
  sudo --preserve-env=MEZAN_SOURCE_SHA,MEZAN_SECRETS_DIR,MEZAN_CI_DISK,MEZAN_CGROUP_PARENT,EXIT2D_ECONOMY_CI \
    docker compose -p "$project" -f packaging/exit2d/vps/compose.yaml -f packaging/exit2d/vps/ci.override.yaml "$@"
}
cleanup() {
  result=$?
  touch host-app-evidence/resources.json.stop
  test -z "${monitor:-}" || wait "$monitor" || true
  docker rm -f "$project-tls" >/dev/null 2>&1 || true
  compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  printf '{"exit_code":%s}\n' "$result" > host-app-evidence/outcome.json
}
trap cleanup EXIT
sudo python packaging/exit2d/vps/init_secrets.py "$MEZAN_SECRETS_DIR" --ci-fixture
compose build mongo web
compose up -d --no-build --wait --wait-timeout 240
web=$(compose ps -q web); mongo=$(compose ps -q mongo)
reports=$(compose ps -q reports); sync=$(compose ps -q sync); accounting=$(compose ps -q accounting)
python packaging/exit2d/render/economy_monitor.py host-app-evidence/resources.json "$web" "$mongo" "$reports" "$sync" "$accounting" & monitor=$!
compose exec -T web python /opt/vps/entry.py test /evidence/tools/render/economy_acceptance.py setup > host-app-evidence/setup.jsonl
python - "$scratch" "$project" <<'PY'
import json,os,sys
from pathlib import Path
sys.path.insert(0,'packaging/exit2d/ovh_host')
from generate import caddy
from config import validate
c=json.loads(Path('packaging/exit2d/ovh_host/host.example.json').read_text())
c.update(source_sha=os.environ['GITHUB_SHA'], project=sys.argv[2],state_dir=sys.argv[1]+'/state')
Path(sys.argv[1]+'/host.json').write_text(json.dumps(c))
Path(sys.argv[1]+'/Caddyfile').write_text(caddy(validate(c),ci=True))
PY
docker run -d --name "$project-tls" --network "container:$web" --memory 192m --cpus .2 \
  --cgroup-parent "$MEZAN_CGROUP_PARENT" -v "$scratch/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.10.2 >/dev/null
sleep 5
docker exec "$project-tls" cat /data/caddy/pki/authorities/local/root.crt > "$scratch/ca.pem"
chmod 644 "$scratch/ca.pem"
compose exec -T sync python /opt/vps/entry.py test - < packaging/exit2d/render/economy_snap.py > host-app-evidence/snap.jsonl 2>&1 & snap=$!
# External load generator shares only network namespace, outside service resource budget.
docker run --rm -i --network "container:$web" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  --memory 1g --memory-swap 1g --cpus 1 \
  -e APP_ENV=staging -e MEZAN_SYNTHETIC_HOSTING=1 -e DB_NAME=mezan_exit2d_staging \
  -e MEZAN_STAGING_MONGO_HOST=staging-mongo -e EXIT2D_ECONOMY_CI=1 \
  -v "$MEZAN_SECRETS_DIR/jwt_secret:/run/secrets/jwt_secret:ro" \
  -v "$MEZAN_SECRETS_DIR/admin_password:/run/secrets/admin_password:ro" \
  -v "$MEZAN_SECRETS_DIR/mfa_bootstrap_code:/run/secrets/mfa_bootstrap_code:ro" \
  -v "$MEZAN_SECRETS_DIR/mongo_uri:/run/secrets/mongo_uri:ro" \
  -v "$PWD/packaging/exit2d/vps/entry.py:/opt/vps/entry.py:ro" \
  -v "$PWD/packaging/exit2d:/evidence/tools:ro" -v "$PWD/packaging/exit2c:/evidence/acceptance:ro" \
  -v "$scratch/ca.pem:/evidence/ca.pem:ro" --entrypoint python "mezan-vps:$GITHUB_SHA" \
  /opt/vps/entry.py test - < packaging/exit2d/ovh_host/ci_tls_benchmark.py | tee host-app-evidence/https-operations.jsonl
wait "$snap"
compose exec -T accounting python /opt/vps/entry.py test - < packaging/exit2d/render/economy_accounting.py > host-app-evidence/accounting-before.jsonl
sudo --preserve-env=GITHUB_ACTIONS python packaging/exit2d/ovh_host/ci_backup.py "$scratch/host.json" | tee host-app-evidence/mongo-restic.jsonl
compose up -d --no-build --wait --wait-timeout 180
compose exec -T accounting python /opt/vps/entry.py test - < packaging/exit2d/render/economy_accounting.py > host-app-evidence/accounting-after.jsonl
sudo python packaging/exit2d/ovh_host/monitor.py --config "$scratch/host.json" > host-app-evidence/monitor.json
