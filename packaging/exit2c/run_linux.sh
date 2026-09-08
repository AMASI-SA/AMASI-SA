#!/usr/bin/env bash
set -euo pipefail
mode="${1:-runtime}"
case "$mode" in runtime|--preparation-only|--preparation-denied) ;; *) echo "FAIL controller PHASE_ORDER"; exit 2;; esac
accept_args=()
if test "$mode" != runtime; then accept_args=("$mode"); fi
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
simulator="exit2d-simulator-$suffix"
collect_simulator_evidence() {
  local evidence_status
  evidence_collected=1
  # Both transport and process have deadlines; never dump docker logs on error.
  if timeout --kill-after=1s 6s docker exec "$probe" python /opt/acceptance/simulator_evidence.py 2>/dev/null; then
    return 0
  else
    evidence_status=$?
  fi
  # Exit 2 already emitted measured counters and a fixed unexpected-request FAIL.
  if test "$evidence_status" != 2; then echo 'SIMULATOR_EVIDENCE unavailable'; fi
  return 1
}
cleanup() {
  result=$?
  if test "$result" != 0 && test "$mode" != runtime && test "${evidence_collected:-0}" = 0; then
    collect_simulator_evidence || true
  fi
  if test "$result" != 0 && test "$mode" = runtime; then
    for name in "$web1" "$web2" "$worker"; do docker logs "$name" 2>&1 | tail -n 45 || true; done
  fi
  if test -n "${accept_in:-}"; then exec {accept_in}>&-; fi
  docker unpause "$mongo" >/dev/null 2>&1 || true
  docker rm -f "$web1" "$web2" "$probe" "$worker" "$simulator" "$mongo" >/dev/null 2>&1 || true
  if test -n "${accept_pid:-}"; then wait "$accept_pid" 2>/dev/null || true; fi
  if test -n "${accept_out:-}"; then exec {accept_out}<&-; fi
}
trap cleanup EXIT
accept_phase=controller
trap 'printf "FAIL %s CANCELLED\n" "${accept_phase:-controller}"; exit 130' INT
trap 'printf "FAIL %s CANCELLED\n" "${accept_phase:-controller}"; exit 143' TERM
docker run -d --name "$mongo" --network none --tmpfs /data/db --tmpfs /data/configdb \
  mongo:7.0.16@sha256:c630c59342c1493d50345136df2af14a76b9e827dd5316bfabee07a0880a5f3a --bind_ip 127.0.0.1 --quiet
test "$(docker inspect -f '{{.HostConfig.NetworkMode}}' "$mongo")" = none
for attempt in $(seq 1 30); do
  if docker exec "$mongo" mongosh --quiet --eval 'quit(db.adminCommand({ping:1}).ok?0:1)' >/dev/null 2>&1; then break; fi
  sleep 1
done
runtime=(--network "container:$mongo" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --memory 3g --cpus 2)
migration_runtime=("${runtime[@]}")
if test "$mode" != runtime; then
  runtime+=(-e MEZAN_ACCEPTANCE_PROFILE -e SALLA_API_BASE -e SALLA_AUTH_BASE -e SALLA_TOKEN_ENC_KEY -e EXIT2D_SIM_TOKEN -e EXIT2D_SIM_MODE)
  docker run --rm "${runtime[@]}" --entrypoint python mezan-exit2c:candidate /opt/acceptance/preparation_provider_preflight.py
  docker run -d --name "$simulator" "${runtime[@]}" --entrypoint python mezan-exit2c:candidate /opt/acceptance/salla_http_simulator.py
  docker exec "$simulator" python -c 'import time; time.sleep(0.2)'
fi
docker run -d --name "$probe" "${runtime[@]}" --entrypoint python mezan-exit2c:candidate -c 'import time; time.sleep(1200)'
life() { docker exec "$probe" python /opt/acceptance/lifecycle.py "$1"; }
# A single controller outlives web restarts. Pipes carry phase names/status only.
start_acceptance() {
  coproc ACCEPTANCE { docker exec -i "$probe" python /opt/acceptance/acceptance.py "${accept_args[@]}"; }
  accept_pid=$ACCEPTANCE_PID
  exec {accept_in}>&"${ACCEPTANCE[1]}"
  exec {accept_out}<&"${ACCEPTANCE[0]}"
}
accept() {
  local reply status reason check_id expected actual checkpoint tail count category method group flag frames=0
  allowed_check() {
    case "$accept_phase:$1" in
      prep-review:OWNER_LOGIN|prep-review:EMPLOYEE_LOGIN|prep-review:VIEWER_LOGIN|prep-review:OUTSIDER_LOGIN|prep-review:OWNER_SESSION|prep-review:EMPLOYEE_SESSION|prep-review:VIEWER_SESSION|prep-review:OUTSIDER_SESSION|prep-review:REVIEW_INVARIANTS|prep-review:TENANT_SNAPSHOT|prep-review:ORDER_READ|prep-review:PRODUCT_IDENTITIES|prep-review:QUANTITIES_OPTIONS|prep-review:IMAGE_UPLOAD|prep-review:IMAGE_CHOICE|prep-review:ITEM_NOTE|prep-review:IMAGE_GALLERY_LINK_SET|prep-review:IMAGE_RESOURCE_KNOWN|prep-review:IMAGE_HTTP_RESPONSE|prep-review:IMAGE_CONTENT_MATCH|prep-review:IMAGE_TENANT_DENIAL|prep-review:REVIEW_ROLE_DENIAL|prep-review:REVIEW_COMPLETE|prep-review:REVIEW_ERROR_CODE|prep-review:REVIEW_STORED_STATE|prep-review:NO_PREPARATION_ENTITIES|prep-review:PROVIDER_COUNTERS) return 0;;
      prep-create:EMPLOYEE_CATALOG|prep-create:INITIAL_CATALOG|prep-create:SAFE_DRAFT|prep-create:FILE_CREATE|prep-create:FINALIZE_FALLBACK|prep-create:FILE_CREATE_REPEAT|prep-create:EARLY_START_DENIAL|prep-create:DRAFT_ROLE_DENIAL|prep-create:IMAGE_PERSISTENCE|prep-create:FILE_REGISTRY_READ|prep-create:PDF_HTTP|prep-create:PDF_CONTENT|prep-create:PDF_TENANT_DENIAL|prep-create:INCOMPLETE_DRAFT|prep-create:INCOMPLETE_FINALIZE_DENIAL|prep-create:CHECKPOINT_CAPTURE) return 0;;
      prep-resume:OWNER_SESSION|prep-resume:EMPLOYEE_SESSION|prep-resume:VIEWER_SESSION|prep-resume:OUTSIDER_SESSION|prep-resume:RESUME_INVARIANTS|prep-resume:SNAPSHOT_IDENTITY|prep-resume:SNAPSHOT_MATCH|prep-resume:OTHER_TENANT_MATCH|prep-resume:IMAGE_PERSISTENCE|prep-resume:FILE_REGISTRY_READ|prep-resume:PDF_HTTP|prep-resume:PDF_CONTENT|prep-resume:PDF_TENANT_DENIAL|prep-resume:INCOMPLETE_RELEASE|prep-resume:COMPLETED_RELEASE_DENIAL|prep-resume:REALLOCATION_DRAFT|prep-resume:REALLOCATION_DENIAL|prep-resume:REALLOCATION_RELEASE|prep-resume:REMAINING_CATALOG|prep-resume:SAFE_DRAFT|prep-resume:FILE_CREATE|prep-resume:FINALIZE_FALLBACK|prep-resume:FILE_CREATE_REPEAT|prep-resume:ASSIGNMENT_STATES|prep-resume:UNIT_ALLOCATION_IDENTITIES|prep-resume:UNIT_PIECE_IDENTITIES|prep-resume:UNIT_ALLOCATION_STATUS|prep-resume:UNIT_EMPLOYEE_ASSIGNMENT|prep-resume:UNIT_BATCH_LINK|prep-resume:UNIT_SPECIFICATIONS|prep-resume:UNIT_PROJECTED_OPTIONS|prep-resume:EMPLOYEE_START|prep-resume:START_REPEAT|prep-resume:SUPPLIER_WORKSPACE|prep-resume:SUPPLIER_DISPATCH|prep-resume:SUPPLIER_DISPATCH_REPEAT|prep-resume:SUPPLIER_READY|prep-resume:SUPPLIER_PIECE_IDENTITY|prep-resume:RECEIVING_SEARCH|prep-resume:PIECE_RECEIVE|prep-resume:RECEIVE_REPEAT|prep-resume:FILE_COMPLETED_STATE|prep-resume:ASSEMBLY_SEARCH|prep-resume:PIECE_ASSEMBLY|prep-resume:SIMULATED_LABEL_FAILURE|prep-resume:ASSEMBLY_STATES|prep-resume:RESUME_PROVIDER_COUNTERS|prep-resume:CHECKPOINT_CAPTURE) return 0;;
      prep-finish:OWNER_SESSION|prep-finish:EMPLOYEE_SESSION|prep-finish:VIEWER_SESSION|prep-finish:OUTSIDER_SESSION|prep-finish:RESUME_INVARIANTS|prep-finish:SNAPSHOT_IDENTITY|prep-finish:SNAPSHOT_MATCH|prep-finish:OTHER_TENANT_MATCH|prep-finish:IMAGE_PERSISTENCE|prep-finish:FILE_REGISTRY_READ|prep-finish:PDF_HTTP|prep-finish:PDF_CONTENT|prep-finish:PDF_TENANT_DENIAL|prep-finish:FINAL_PROVIDER_COUNTERS|prep-finish:FINAL_LABEL_COUNTS) return 0;;
      *) return 1;;
    esac
  }

  # Reject untrusted phase names without printing them.
  case "$1" in
    setup|http|mongo-down|after-restart|prep-setup|prep-review|prep-create|prep-resume|prep-finish|finish) accept_phase="$1" ;;
    *) echo 'FAIL controller PHASE_ORDER'; return 1 ;;
  esac
  # Ignore SIGPIPE only for this write; report its status without shell noise.
  if (trap '' PIPE; printf '%s\n' "$accept_phase" >&"$accept_in") 2>/dev/null; then
    :
  else
    printf 'FAIL %s CHANNEL_CLOSED\n' "$accept_phase"
    return 1
  fi
  while test "$frames" -lt 4096; do
  frames=$((frames + 1))
  if IFS= read -r -t 120 reply <&"$accept_out"; then
    if [[ "$reply" =~ ^EVIDENCE\ ([A-Z_]+)\ (.+)$ ]]; then
      checkpoint="${BASH_REMATCH[1]}"; tail="${BASH_REMATCH[2]}"
      case "$accept_phase:$checkpoint" in
        prep-review:BEFORE_REVIEW|prep-review:AFTER_LOGIN|prep-review:BEFORE_IMAGES|prep-review:AFTER_IMAGES|prep-review:BEFORE_COMPLETE|prep-review:AFTER_COMPLETE|prep-review:AFTER_REVIEW) ;;
        prep-create:AFTER_CREATE) ;;
        prep-resume:BEFORE_RESUME|prep-resume:AFTER_PERSISTENCE|prep-resume:AFTER_RECOVERY|prep-resume:AFTER_REALLOCATION|prep-resume:AFTER_SECOND_FILE|prep-resume:AFTER_START|prep-resume:AFTER_DISPATCH|prep-resume:AFTER_RECEIVE|prep-resume:AFTER_ASSEMBLY|prep-resume:AFTER_RESUME) ;;
        prep-finish:BEFORE_FINISH|prep-finish:AFTER_FINAL_PERSISTENCE|prep-finish:AFTER_FINISH) ;;
        *) echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1;; esac
      if test "$tail" = unavailable; then
        printf 'EVIDENCE %s unavailable\n' "$checkpoint"; continue
      fi
      if [[ "$tail" =~ ^([a-z_]+)\ (0|[1-9][0-9]{0,3})$ ]]; then
        category="${BASH_REMATCH[1]}"; count="${BASH_REMATCH[2]}"
        case "$category" in simulated_provider_calls|unexpected|status_writes|denied|shipping_attempted|shipping_failed|status_write_denied|status_discovery_unavailable|auth_rejected) ;;
          *) echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1;; esac
        if test "$count" -gt 1000; then echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1; fi
        printf 'EVIDENCE %s %s %s\n' "$checkpoint" "$category" "$count"; continue
      fi
      if [[ "$tail" =~ ^([A-Z_]+)\ ([A-Z_]+)\ ([A-Z_]+)\ ([1-9][0-9]{0,3})$ ]]; then
        category="${BASH_REMATCH[1]}"; method="${BASH_REMATCH[2]}"; reason="${BASH_REMATCH[3]}"; count="${BASH_REMATCH[4]}"
        case "$category" in PRODUCT_DETAIL|PRODUCT_SEARCH|ORDER_DETAIL|ORDER_ITEMS|ORDER_STATUS_LIST|ORDER_STATUS_WRITE|OAUTH|OTHER) ;; *) echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1;; esac
        case "$method" in GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD|OTHER) ;; *) echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1;; esac
        case "$reason" in UNKNOWN_ROUTE|QUERY_SHAPE|BODY_SHAPE|ID_NOT_IN_FIXTURE|AUTH_REJECTED) ;; *) echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1;; esac
        if test "$count" -gt 1000; then echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1; fi
        printf 'EVIDENCE %s %s %s %s %s\n' "$checkpoint" "$category" "$method" "$reason" "$count"; continue
      fi
      echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1
    fi
    if test "$reply" = EVIDENCE_UNAVAILABLE; then echo EVIDENCE_UNAVAILABLE; continue; fi
    if [[ "$reply" =~ ^CHECK\ ([a-z-]+)\ ([A-Z_]+)\ PASS$ ]] && test "${BASH_REMATCH[1]}" = "$accept_phase"; then
      check_id="${BASH_REMATCH[2]}"
      if ! allowed_check "$check_id"; then echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1; fi
      printf 'CHECK %s %s PASS\n' "$accept_phase" "$check_id"; continue
    fi
    if [[ "$reply" == UNITS* ]]; then
      if test "$accept_phase" != prep-resume; then echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1; fi
      if test "$reply" = 'UNITS unavailable'; then echo 'UNITS unavailable'; continue; fi
      if [[ "$reply" =~ ^UNITS\ (ALLOCATIONS|PIECES|STATUS|EMPLOYEE|BATCH_LINK|SPECIFICATIONS|PROJECTED_OPTIONS)\ (EXPECTED|ACTUAL|MISSING|DUPLICATE|UNEXPECTED|MISMATCH|MALFORMED)\ (0|[1-9][0-9]{0,4})$ ]]; then
        group="${BASH_REMATCH[1]}"; category="${BASH_REMATCH[2]}"; count="${BASH_REMATCH[3]}"
        if test "$count" -gt 10000; then echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1; fi
        printf 'UNITS %s %s %s\n' "$group" "$category" "$count"; continue
      fi
      echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1
    fi
    if [[ "$reply" == SNAPSHOT* ]]; then
      case "$accept_phase" in prep-resume|prep-finish) ;; *) echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1;; esac
      if test "$reply" = 'SNAPSHOT unavailable'; then echo 'SNAPSHOT unavailable'; continue; fi
      if [[ "$reply" =~ ^SNAPSHOT\ IDENTITY_CHANGED\ (true|false)$ ]]; then
        printf 'SNAPSHOT IDENTITY_CHANGED %s\n' "${BASH_REMATCH[1]}"; continue
      fi
      if [[ "$reply" =~ ^SNAPSHOT\ ([A-Z]+)\ (.+)$ ]]; then
        group="${BASH_REMATCH[1]}"; tail="${BASH_REMATCH[2]}"
        case "$group" in REGISTRY|BATCHES|ALLOCATIONS|PIECES|EVENTS|WORKFLOWS) ;; *) echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1;; esac
        if [[ "$tail" =~ ^COUNT\ (0|[1-9][0-9]{0,4})\ (0|[1-9][0-9]{0,4})$ ]]; then
          expected="${BASH_REMATCH[1]}"; actual="${BASH_REMATCH[2]}"
          if test "$expected" -gt 10000 || test "$actual" -gt 10000; then echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1; fi
          printf 'SNAPSHOT %s COUNT %s %s\n' "$group" "$expected" "$actual"; continue
        fi
        if [[ "$tail" =~ ^(IDENTITIES_CHANGED|QUANTITIES_CHANGED|STATUSES_CHANGED|ORDER_CHANGED|UNKNOWN_FIELDS_CHANGED)\ (true|false)$ ]]; then
          printf 'SNAPSHOT %s %s %s\n' "$group" "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"; continue
        fi
        if [[ "$tail" =~ ^FIELD\ ([a-z_]+)$ ]]; then
          flag="${BASH_REMATCH[1]}"
          case "$flag" in allocated_quantity|allocation_id|assembled_at|assembly_completed_at|assembly_ready_at|assembly_status|assembly_updated_at|assigned_at|batch_id|branch_handoff_at|branch_handoff_status|carrier_label|carrier_label_ready|carrier_label_status|claimed_at|client_request_id|completed_at|created_at|estimated_due_at|event_type|execution_status|expected_quantity|expires_at|file_date|file_number|finalized_at|history|id|image_url|images|in_progress_at|items|last_synced_at|lines|mezan_only|occurred_at|operational_items|order_count|order_item_id|order_number|piece_id|piece_registry_materialized_at|piece_registry_status|preparation_assignment_status|preparation_completed_at|preparation_employee_custody_status|preparation_fully_allocated_at|preparation_progress|preparation_receipt_status|preparation_receipt_updated_at|preparation_received_at|preparation_status|product_id|product_image_snapshot|product_options_snapshot|qoyod_updated|quantity|ready_to_ship_at|receipt_status|received_at|received_quantity|registered_at|registry_status|remaining_quantity|required_due_at|responsible_employee_id|review_status|reviewed_at|revision|salla_status_sync|salla_status_sync_state|salla_status_synced_at|salla_sync_status|salla_updated|schedule_updated_at|selected_product_count|service_plan_status|stage|started_at|status|supplier_id|total_quantity|unit_index|updated_at|user_id) ;; *) echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1;; esac
          printf 'SNAPSHOT %s FIELD %s\n' "$group" "$flag"; continue
        fi
      fi
      echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1
    fi
    if test "$reply" = "PASS $accept_phase"; then
      printf 'PASS acceptance phase: %s\n' "$accept_phase"
      return 0
    fi
    for reason in TIMEOUT CHANNEL_CLOSED CANCELLED PHASE_ORDER ASSERTION_FAILED UNCLASSIFIED_FAILURE; do
      if test "$reply" = "FAIL $accept_phase $reason"; then
        printf 'FAIL %s %s\n' "$accept_phase" "$reason"
        return 1
      fi
    done
    # Exact grammar and independent allowlists; never echo the received line.
    if [[ "$reply" =~ ^FAIL\ ([a-z-]+)\ ([A-Z_]+)\ ([A-Z_]+)(\ ([1-5][0-9][0-9])\ ([1-5][0-9][0-9]))?$ ]] && test "${BASH_REMATCH[1]}" = "$accept_phase"; then
      reason="${BASH_REMATCH[2]}"; check_id="${BASH_REMATCH[3]}"
      expected="${BASH_REMATCH[5]}"; actual="${BASH_REMATCH[6]}"
      if ! allowed_check "$check_id"; then reason=UNCLASSIFIED_FAILURE; check_id=; fi
      if test -n "$check_id"; then
        if test "$reason" = HTTP_STATUS_MISMATCH && test -n "$expected" && test -n "$actual"; then
          printf 'FAIL %s %s %s expected=%s actual=%s\n' "$accept_phase" "$reason" "$check_id" "$expected" "$actual"
          return 1
        fi
        case "$reason" in
          TIMEOUT|CHANNEL_CLOSED|CANCELLED|PHASE_ORDER|ASSERTION_FAILED|UNCLASSIFIED_FAILURE)
            if test -z "$expected" && test -z "$actual"; then
              printf 'FAIL %s %s %s\n' "$accept_phase" "$reason" "$check_id"
              return 1
            fi ;;
        esac
      fi
    fi
    reason=UNCLASSIFIED_FAILURE
  else
    status=$?
    # Bash read returns 1 at EOF and >128 on timeout. INT/TERM traps exit.
    if test "$status" -gt 128; then reason=TIMEOUT
    elif test "$status" -eq 1; then reason=CHANNEL_CLOSED
    else reason=UNCLASSIFIED_FAILURE; fi
  fi
  printf 'FAIL %s %s\n' "$accept_phase" "$reason"
  return 1
  done
  echo 'FAIL controller UNCLASSIFIED_FAILURE'; return 1
}

start_webs() {
  docker run -d --name "$web1" "${runtime[@]}" mezan-exit2c:candidate web --port 8001
  docker run -d --name "$web2" "${runtime[@]}" mezan-exit2c:candidate web --port 8002
  for port in 8001 8002; do
    ready=false
    for attempt in $(seq 1 40); do
      if docker exec "$probe" python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:$port/api/ready',timeout=1).status==200" >/dev/null 2>&1; then ready=true; break; fi
      sleep 1
    done
    if test "$ready" != true; then echo "FAIL controller WEB_NOT_READY"; exit 1; fi
  done
}
stop_webs() {
  docker stop --time 10 "$web1" "$web2" >/dev/null
  for name in "$web1" "$web2"; do
    test "$(docker inspect -f '{{.State.ExitCode}}' "$name")" = 0
    if test "$mode" = runtime; then docker logs "$name" 2>&1 | tail -n 10; fi
    docker rm "$name" >/dev/null
  done
}

if test "$mode" != runtime; then
  # No legacy ready-batch fixture and no armed worker/supervisor acceptance.
  docker run --rm "${migration_runtime[@]}" mezan-exit2c:candidate migration
  docker run --rm "${runtime[@]}" --entrypoint python mezan-exit2c:candidate /opt/acceptance/test_acceptance_controller.py
  docker run --rm "${runtime[@]}" --entrypoint python mezan-exit2c:candidate /opt/acceptance/test_preparation_lifecycle_acceptance.py
  start_acceptance
  accept prep-setup
  life profile
  start_webs
  life no-writes
  accept prep-review
  if test "$mode" = --preparation-only; then
  accept prep-create
  stop_webs
  start_webs
  accept prep-resume
  stop_webs
  start_webs
  accept prep-finish
  fi
  accept finish
  exec {accept_in}>&-
  wait "$accept_pid"
  exec {accept_out}<&-
  unset accept_in accept_out accept_pid
  collect_simulator_evidence
  if test "$mode" = --preparation-only; then
    echo 'PASS application lifecycle with simulated HTTP provider; stale revision remains unaccepted'
  else
    echo 'PASS review rejection without provider confirmation; NOT a complete lifecycle'
  fi
  exit 0
fi

docker run --rm "${runtime[@]}" --entrypoint python mezan-exit2c:tests /opt/acceptance/regressions.py
docker run --rm "${runtime[@]}" --entrypoint python mezan-exit2c:candidate /opt/acceptance/test_acceptance_controller.py
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
start_acceptance
accept setup
life profile

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
accept finish
exec {accept_in}>&-
wait "$accept_pid"
exec {accept_out}<&-
unset accept_in accept_out accept_pid
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
