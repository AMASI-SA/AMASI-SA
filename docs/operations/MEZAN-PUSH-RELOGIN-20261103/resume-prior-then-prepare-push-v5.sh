#!/usr/bin/env bash
set -Eeuo pipefail

cd /app
PROD=hotfix/prod-snap-meta-final
PREVIOUS_J=7d1be020b9559ea294b6ea3f7511f37ee5e6a847
PREVIOUS_RID=rg5-b3ee097a92d53974c278005cdf322c01be4b03d669f687326e2c7d61e0bd38ad

test "$(git branch --show-current)" = "$PROD" || {
  echo "STOP: the Emergent host is not on the Production branch"; exit 2;
}
test -z "$(git status --porcelain --untracked-files=no)" || {
  echo "STOP: preserve existing tracked changes on this host"; exit 2;
}
test "$(git rev-parse HEAD)" = "$PREVIOUS_J" || {
  echo "STOP: host checkout differs from pinned previous release"; exit 2;
}

STATE="$(mktemp /tmp/mezan-push-prev-status-XXXXXXXX.json)"
python3 -B scripts/production_release_guard.py status > "$STATE"
IS_ACTIVE="$(python3 - "$STATE" "$PREVIOUS_J" "$PREVIOUS_RID" <<'PY'
import json,sys
path,expected_sha,expected_id=sys.argv[1:]
with open(path,encoding="utf-8") as f: s=json.load(f)
if s.get("incomplete_lease_directory"):
    raise SystemExit("STOP: an incomplete release lease needs its owner")
if s.get("active") is True:
    if s.get("deployment_git_sha") != expected_sha or s.get("release_id") != expected_id:
        raise SystemExit("STOP: an unrelated active release must not be touched")
    print("yes")
elif s.get("active") is False:
    print("no")
else:
    raise SystemExit("STOP: unknown release lease state")
PY
)"

if test "$IS_ACTIVE" = yes; then
  echo "=== OLD RELEASE LEASE FOUND: VERIFY LIVE CLOUD PUBLISH FIRST ==="
  # The official guard does three read-only live health/frontend probes and
  # closes only this exact, verified previous lease.
  python3 -B scripts/production_release_guard.py verify --url https://mezansalla.com
fi

echo "=== VERIFY PUBLIC PREVIOUS RELEASE IDENTITY ==="
python3 - "$PREVIOUS_J" "$PREVIOUS_RID" <<'PY'
import json,sys,urllib.request
sha,rid=sys.argv[1:]
req=urllib.request.Request("https://mezansalla.com/api/health",
    headers={"Accept":"application/json","Cache-Control":"no-cache",
             "User-Agent":"MezanPushV5PreviousReleaseProbe/1.0"})
with urllib.request.urlopen(req,timeout=20) as response: health=json.load(response)
release=health.get("release") or {}
assert health.get("ok") is True
assert release.get("verified_identity_available") is True
assert release.get("deployment_git_sha") == sha
assert release.get("release_id") == rid
assert release.get("backend_runtime_source_verified") is True
print("PREVIOUS_V5_DEPLOYMENT: VERIFIED")
PY

echo "=== DOWNLOAD REVIEWED NEXT INTENT SCRIPT ==="
NEXT="$(mktemp /tmp/mezan-push-intent-script-XXXXXXXX.sh)"
curl -fLsS --retry 3 \
  https://raw.githubusercontent.com/AMASI-SA/AMASI-SA/9de5e92cd947160c3d1cbe27e38d526d26f13301/docs/operations/MEZAN-PUSH-RELOGIN-20261103/prepare-v5-push-intent.sh \
  -o "$NEXT"
test "$(git hash-object "$NEXT")" = 90686c6e83d8f803f12a35cadf1237f5a30fbe71 || {
  echo "STOP: downloaded source blob does not match reviewed script"; exit 3;
}
bash -n "$NEXT"
bash "$NEXT"
