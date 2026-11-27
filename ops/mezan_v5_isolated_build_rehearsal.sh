#!/usr/bin/env bash
# Isolated Mezan V5 build rehearsal. Does not modify /app, release lease or live deployment.
set -euo pipefail
APP="/app"
BRANCH="hotfix/prod-snap-meta-final"
EXPECTED_M="53e52767a1bf2b4efd4ec796e8c59de9425c8839"
EXPECTED_P="d4a623657fd3cb47c769095e5886b2f52dabaa3c"
EXPECTED_RELEASE="rg5-e039d675b02789f0164e5595e19de3b63e49ab1815970a631edd620f8d239d82"
fail() { printf 'ISOLATED_BUILD_STOP: %s\n' "$*" >&2; exit 2; }

test -d "$APP/.git" || fail "repository /app/.git not found"
test "$(git -C "$APP" branch --show-current)" = "$BRANCH" || fail "incorrect /app branch"
test "$(git -C "$APP" rev-parse HEAD)" = "$EXPECTED_M" || fail "incorrect /app HEAD"
test -z "$(git -C "$APP" status --porcelain --untracked-files=no)" || fail "/app tracked worktree is dirty"
test "${AMASI_WHATSAPP_CATALOG_ENABLED:-0}" = "0" || fail "WhatsApp catalog feature flag must remain OFF"
test "${AMASI_WHATSAPP_ORDER_STAGE_ENABLED:-0}" = "0" || fail "WhatsApp order-stage feature flag must remain OFF"
ORIGIN="$(git -C "$APP" remote get-url origin)"
case "$ORIGIN" in
  https://github.com/AMASI-SA/AMASI-SA|https://github.com/AMASI-SA/AMASI-SA.git|git@github.com:AMASI-SA/AMASI-SA.git) ;;
  *) fail "GitHub origin does not match official repository" ;;
esac
GUARD="$(cd "$APP" && python3 -B scripts/production_release_guard.py status)"
export GUARD
python3 -B - <<'PY'
import json, os
guard=json.loads(os.environ["GUARD"])
if guard.get("active") is not False or guard.get("incomplete_lease_directory"):
    raise SystemExit("ISOLATED_BUILD_STOP: release lease active or incomplete")
print("RELEASE_LEASE_AVAILABLE_NOT_ACQUIRED")
PY
FREE_KIB="$(df -Pk /tmp | awk 'END {print $4}')"
[[ "$FREE_KIB" =~ ^[0-9]+$ ]] || fail "cannot measure /tmp free disk"
test "$FREE_KIB" -ge 2097152 || fail "less than 2 GiB available in /tmp"
WORK="$(mktemp -d /tmp/mezan-v5-rehearsal-XXXXXXXX)"
test -d "$WORK" || fail "cannot create isolated workspace"
mkdir -p "$WORK/cache"
export EXPECTED_M EXPECTED_P EXPECTED_RELEASE WORK APP

python3 -B - <<'PY'
import hashlib,json,os,pathlib,subprocess
root=pathlib.Path(os.environ["APP"])
work=pathlib.Path(os.environ["WORK"])
paths=["backend/release_identity.json","frontend/build/build-meta.json","frontend/.release/reproducible-build.json"]
untracked=subprocess.check_output(["git","-C",str(root),"ls-files","--others","--exclude-standard","-z"])
evidence={}
for name in paths:
    path=root/name
    if path.is_symlink() or not path.is_file():
        raise SystemExit("ISOLATED_BUILD_STOP: old output missing or symlink: "+name)
    evidence[name]=hashlib.sha256(path.read_bytes()).hexdigest()
data={"untracked_sha256":hashlib.sha256(untracked).hexdigest(),
      "untracked_count":len([p for p in untracked.split(b"\0") if p]),
      "generated_sha256":evidence}
(work/"app-before.json").write_text(json.dumps(data,sort_keys=True))
print("APP_SNAPSHOT_SAVED",data["untracked_count"],"untracked paths")
PY

# An independent checkout; shared Git objects are only read, never changed.
git clone --quiet --shared --single-branch --branch "$BRANCH" "$APP" "$WORK/repo"
git -C "$WORK/repo" remote set-url origin "$ORIGIN"
test "$(git -C "$WORK/repo" rev-parse HEAD)" = "$EXPECTED_M" || fail "clone HEAD changed"
test "$(git -C "$WORK/repo" branch --show-current)" = "$BRANCH" || fail "clone branch changed"
test -z "$(git -C "$WORK/repo" status --porcelain --untracked-files=all)" || fail "clone not clean"
test "$(git -C "$WORK/repo" rev-parse "$EXPECTED_P^{commit}")" = "$EXPECTED_P" || fail "reviewed source unavailable"
printf 'ISOLATED_BUILD_WORKDIR=%s\n' "$WORK/repo"
printf '%s\n' "RUNNING_GOVERNED_BUILD_IN_TEMP_CLONE_ONLY"
(
  cd "$WORK/repo"
  export PYTHONDONTWRITEBYTECODE=1
  export PYTHON_DOTENV_DISABLED=1
  export XDG_CACHE_HOME="$WORK/cache"
  export REACT_APP_BACKEND_URL="https://mezansalla.com"
  python3 -B scripts/emergent_deployment_adapter.py build \
    >"$WORK/build.log" 2>&1 || {
      tail -n 65 "$WORK/build.log"
      fail "isolated build refused; details retained at $WORK/build.log"
    }
)

python3 -B - <<'PY'
import hashlib,json,os,pathlib,subprocess
root=pathlib.Path(os.environ["APP"])
work=pathlib.Path(os.environ["WORK"])
repo=work/"repo"
intent=json.loads((repo/"release/release-intent-v5.json").read_text())
identity=json.loads((repo/"backend/release_identity.json").read_text())
meta=json.loads((repo/"frontend/build/build-meta.json").read_text())
proof=json.loads((repo/"frontend/.release/reproducible-build.json").read_text())
if intent["source_git_sha"] != os.environ["EXPECTED_P"]:
    raise SystemExit("ISOLATED_BUILD_STOP: intent source mismatch")
if identity != intent["runtime_identity"]:
    raise SystemExit("ISOLATED_BUILD_STOP: built backend identity differs from reviewed release")
if identity["release_id"] != os.environ["EXPECTED_RELEASE"]:
    raise SystemExit("ISOLATED_BUILD_STOP: release ID differs")
if meta["git_sha"] != os.environ["EXPECTED_P"] or proof["git_sha"] != os.environ["EXPECTED_P"]:
    raise SystemExit("ISOLATED_BUILD_STOP: frontend source mismatch")
if subprocess.check_output(["git","-C",str(repo),"status","--porcelain","--untracked-files=no"]).strip():
    raise SystemExit("ISOLATED_BUILD_STOP: clone tracked source changed")
paths=["backend/release_identity.json","frontend/build/build-meta.json","frontend/.release/reproducible-build.json"]
after_untracked=subprocess.check_output(["git","-C",str(root),"ls-files","--others","--exclude-standard","-z"])
before=json.loads((work/"app-before.json").read_text())
after={"untracked_sha256":hashlib.sha256(after_untracked).hexdigest(),"generated_sha256":{}}
for name in paths:
    path=root/name
    if path.is_symlink() or not path.is_file():
        raise SystemExit("ISOLATED_BUILD_STOP: /app output missing or unsafe after build")
    after["generated_sha256"][name]=hashlib.sha256(path.read_bytes()).hexdigest()
if before["untracked_sha256"] != after["untracked_sha256"]:
    raise SystemExit("ISOLATED_BUILD_STOP: /app untracked paths changed")
if before["generated_sha256"] != after["generated_sha256"]:
    raise SystemExit("ISOLATED_BUILD_STOP: /app generated outputs changed")
if subprocess.check_output(["git","-C",str(root),"rev-parse","HEAD"],text=True).strip() != os.environ["EXPECTED_M"]:
    raise SystemExit("ISOLATED_BUILD_STOP: /app Git HEAD changed")
if subprocess.check_output(["git","-C",str(root),"status","--porcelain","--untracked-files=no"]).strip():
    raise SystemExit("ISOLATED_BUILD_STOP: /app tracked source changed")
print("ISOLATED_GOVERNED_BUILD_PASS")
print("RELEASE_ID="+identity["release_id"])
print("SOURCE_GIT_SHA="+intent["source_git_sha"])
print("DEPLOYMENT_GIT_SHA="+os.environ["EXPECTED_M"])
print("APP_UNTRACKED_PRESERVED="+str(before["untracked_count"]))
print("APP_STALE_BUILD_OUTPUTS_UNTOUCHED")
print("PRODUCTION_NOT_DEPLOYED")
print("REHEARSAL_DIRECTORY="+str(work))
PY
