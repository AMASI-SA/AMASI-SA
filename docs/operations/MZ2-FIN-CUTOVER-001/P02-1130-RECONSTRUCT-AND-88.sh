#!/usr/bin/env bash
set -uo pipefail
export LC_ALL=C

APP=/app
ADMIN=/app/.git/worktrees/worktree
PARENT=/tmp/mz2-p02-stage1.ZMgPW8
WT="$PARENT/worktree"
HEAD_EXPECTED=20400fffb03594af8a38d6b4750c170233bd5f37
BRANCH_EXPECTED=refs/heads/local/p02-stage1-ZMgPW8
INDEX_EXPECTED=faed3c238a093b90ff7a29e741a55b89abd344693820ed098ee02c98a808d0f5
APP_HEAD_EXPECTED=6365a042dfcb125e81e5e198ea1ff1537373ce51
APP_BRANCH_EXPECTED=refs/heads/hotfix/prod-snap-meta-final
APP_STATUS_EXPECTED='?? .worktrees_p02_runtime.py'
CONTRACT_COMMENT=5783487472
PATCH_COMMENTS=(5782817671 5782818225 5782818706)
PATCH_SHA=14156ef2a45ae489df1d4afcda4c65f4b0804a737837ffbe93cc86f5e7b21d4a

die() {
  echo
  echo "RESULT=$1"
  [ -n "${2:-}" ] && echo "DETAIL=$2"
  echo 'COMMIT=NONE'
  echo 'PUSH=NONE'
  echo 'P02=LOCKED'
  exit 1
}

gitwt() {
  GIT_OPTIONAL_LOCKS=0 git -c core.fsmonitor=false -c core.hooksPath=/dev/null \
    -c gc.auto=0 -c maintenance.auto=false -C "$WT" "$@"
}

echo '=== PRECHECK PRESERVED GIT STATE ==='
[ -d "$ADMIN" ] || die BLOCKED_ADMIN_MISSING
[ ! -e "$PARENT" ] || die BLOCKED_TARGET_PATH_ALREADY_EXISTS "$PARENT"
[ "$(cat "$ADMIN/HEAD")" = "ref: $BRANCH_EXPECTED" ] || die BLOCKED_ADMIN_HEAD
[ "$(cat "$ADMIN/gitdir")" = "$WT/.git" ] || die BLOCKED_ADMIN_GITDIR
[ "$(cat "$ADMIN/commondir")" = '../..' ] || die BLOCKED_ADMIN_COMMONDIR
[ "$(cat "$ADMIN/locked")" = 'P02 PR1130 isolated stage1; preserve' ] || die BLOCKED_LOCK
[ "$(sha256sum "$ADMIN/index" | awk '{print $1}')" = "$INDEX_EXPECTED" ] || die BLOCKED_INDEX_SHA
[ "$(git -C "$APP" rev-parse "$BRANCH_EXPECTED")" = "$HEAD_EXPECTED" ] || die BLOCKED_LOCAL_BRANCH
[ "$(git -C "$APP" rev-parse HEAD)" = "$APP_HEAD_EXPECTED" ] || die BLOCKED_APP_HEAD
[ "$(git -C "$APP" symbolic-ref -q HEAD)" = "$APP_BRANCH_EXPECTED" ] || die BLOCKED_APP_BRANCH
[ "$(git -C "$APP" status --short --untracked-files=all)" = "$APP_STATUS_EXPECTED" ] || die BLOCKED_APP_STATUS
for f in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD REBASE_HEAD BISECT_START; do
  [ ! -e "$ADMIN/$f" ] || die BLOCKED_GIT_OPERATION "$f"
done
for d in rebase-merge rebase-apply sequencer; do
  [ ! -d "$ADMIN/$d" ] || die BLOCKED_GIT_OPERATION "$d"
done

RUN="$(mktemp -d /tmp/mz2-p02-rebuild88.XXXXXX)" || die BLOCKED_RUN_DIR
mkdir -p "$PARENT" "$WT" "$RUN/home" "$RUN/tmp" "$RUN/cache" "$RUN/python"
printf 'gitdir: %s\n' "$ADMIN" > "$WT/.git"
chmod 600 "$WT/.git"

echo '=== RESTORE TRACKED HEAD TREE ==='
git -C "$APP" archive "$HEAD_EXPECTED" | tar -x -C "$WT" || die BLOCKED_HEAD_ARCHIVE
[ "$(gitwt rev-parse HEAD)" = "$HEAD_EXPECTED" ] || die BLOCKED_RESTORED_HEAD
[ "$(gitwt symbolic-ref -q HEAD)" = "$BRANCH_EXPECTED" ] || die BLOCKED_RESTORED_BRANCH
[ -z "$(gitwt diff --cached --name-status HEAD)" ] || die BLOCKED_STAGED_AFTER_RESTORE
[ -z "$(gitwt status --short --untracked-files=all)" ] || die BLOCKED_BASELINE_NOT_CLEAN

echo '=== RESTORE EXACT CONTRACT_SLICE ==='
python3 - "$WT" "$CONTRACT_COMMENT" <<'PY' || die BLOCKED_CONTRACT_TRANSFER
import base64, hashlib, json, re, sys, urllib.request
from pathlib import Path

wt = Path(sys.argv[1])
cid = int(sys.argv[2])
req = urllib.request.Request(
    f"https://api.github.com/repos/AMASI-SA/AMASI-SA/issues/comments/{cid}",
    headers={"Accept":"application/vnd.github+json","User-Agent":"MZ2-P02-contract-recovery"},
)
with urllib.request.urlopen(req, timeout=30) as r:
    body = json.load(r)["body"]

items = [
 ("MODULE", "backend/accounting_shipping_contracts.py",
  "9f25d896d9835dc36a29e91f2dab90de2cf8f1a4c2cec18f01d9fb097863b7c8", 10380),
 ("TESTS", "backend/tests/test_mz2_shipping_contracts.py",
  "61168e55eb400da907f8d0fb795f7c28155e968d22ff4f5b45eae14b8b83f0b2", 12961),
 ("LEGACY", "backend/tests/test_courier_cod_fee_tiers_v2.py",
  "ff35204aaaecfb897869341c82b9c86b82d7580ce70fb397c617edfd76933f9c", 3211),
]
for tag, rel, expected, size in items:
    m = re.search(rf"BEGIN_{tag}_B64\n([A-Za-z0-9+/=\n]+)\nEND_{tag}_B64", body)
    if not m:
        raise SystemExit(f"missing {tag}")
    raw = base64.b64decode("".join(m.group(1).split()), validate=True)
    got = hashlib.sha256(raw).hexdigest()
    if len(raw) != size or got != expected:
        raise SystemExit(f"{tag} mismatch size={len(raw)} sha={got}")
    p = wt / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(raw)
    print(f"{tag}=OK bytes={len(raw)} sha256={got}")
PY

EXPECTED_CONTRACT_STATUS="$(cat <<'EOF'
 M backend/tests/test_courier_cod_fee_tiers_v2.py
?? backend/accounting_shipping_contracts.py
?? backend/tests/test_mz2_shipping_contracts.py
EOF
)"
ACTUAL="$(gitwt status --short --untracked-files=all)"
[ "$ACTUAL" = "$EXPECTED_CONTRACT_STATUS" ] || {
  printf '%s\n' "$ACTUAL"
  die BLOCKED_CONTRACT_STATUS
}

echo '=== FETCH EXACT V4 PATCH ==='
python3 - "$RUN/v4.patch" "${PATCH_COMMENTS[@]}" <<'PY' || die BLOCKED_PATCH_TRANSFER
import base64, hashlib, json, re, sys, urllib.request
from pathlib import Path
out = Path(sys.argv[1])
ids = [int(x) for x in sys.argv[2:]]
parts = []
for n, cid in enumerate(ids, 1):
    req = urllib.request.Request(
        f"https://api.github.com/repos/AMASI-SA/AMASI-SA/issues/comments/{cid}",
        headers={"Accept":"application/vnd.github+json","User-Agent":"MZ2-P02-v4-recovery"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.load(r)["body"]
    m = re.search(rf"BEGIN_P02_1130_V4_PART_{n}\n([A-Za-z0-9+/=\n]+)\nEND_P02_1130_V4_PART_{n}", body)
    if not m:
        raise SystemExit(f"missing patch part {n}")
    parts.append("".join(m.group(1).split()))
raw = base64.b64decode("".join(parts), validate=True)
sha = hashlib.sha256(raw).hexdigest()
if len(raw) != 89921 or sha != "14156ef2a45ae489df1d4afcda4c65f4b0804a737837ffbe93cc86f5e7b21d4a":
    raise SystemExit(f"patch mismatch size={len(raw)} sha={sha}")
out.write_bytes(raw)
print(f"PATCH=OK bytes={len(raw)} sha256={sha}")
PY

[ "$(sha256sum "$RUN/v4.patch" | awk '{print $1}')" = "$PATCH_SHA" ] || die BLOCKED_PATCH_SHA
gitwt apply --check "$RUN/v4.patch" || die BLOCKED_V4_APPLY_CHECK
gitwt apply "$RUN/v4.patch" || die BLOCKED_V4_APPLY

EXPECTED_FINAL_STATUS="$(cat <<'EOF'
 M backend/accounting_module_contract.py
 M backend/tests/test_courier_cod_fee_tiers_v2.py
?? backend/accounting_shipping_contract_gate.py
?? backend/accounting_shipping_contract_service.py
?? backend/accounting_shipping_contracts.py
?? backend/accounting_shipping_evidence.py
?? backend/accounting_shipping_payment_evidence.py
?? backend/tests/test_mz2_shipping_contract_isolation.py
?? backend/tests/test_mz2_shipping_contracts.py
?? backend/tests/test_mz2_shipping_payment_evidence.py
?? docs/operations/MZ2-FIN-CUTOVER-001/P02-1130-V4-ISOLATION-REVIEW.md
EOF
)"
ACTUAL="$(gitwt status --short --untracked-files=all)"
[ "$ACTUAL" = "$EXPECTED_FINAL_STATUS" ] || {
  printf '%s\n' "$ACTUAL"
  die BLOCKED_FINAL_STATUS_SCOPE
}

echo '=== VERIFY EXACT COMBINED BYTES ==='
cd "$WT" || die BLOCKED_CD
cat > "$RUN/sha256.expected" <<'EOF'
d802ea261a9bc64e624cc309a48c02c9c9fe76ea954fb99f4917a393bd559c8f  backend/accounting_module_contract.py
f78310e35cffc0ba5589d36c4d1fb8d0bd7b0fd6f39e5a2eae274ca40872b79e  backend/accounting_shipping_contract_gate.py
e9d1b88fee748a43271a7e5070c55215c5e9aea2d1634cf8bbb9045134674528  backend/accounting_shipping_contract_service.py
9f25d896d9835dc36a29e91f2dab90de2cf8f1a4c2cec18f01d9fb097863b7c8  backend/accounting_shipping_contracts.py
805cb4eaa8d592d67e1daec9cc9a41a3cbd7789266047a68f97c56e6677a598f  backend/accounting_shipping_evidence.py
904d98f2711913dc763ac3663e9f3e00a4b2640d26c4403dc0ac67b9a1381052  backend/accounting_shipping_payment_evidence.py
ff35204aaaecfb897869341c82b9c86b82d7580ce70fb397c617edfd76933f9c  backend/tests/test_courier_cod_fee_tiers_v2.py
0153e5996cb64388a8085df8fe2664bbce0ff6b2f01a8b23bc9f1dd745ba5012  backend/tests/test_mz2_shipping_contract_isolation.py
61168e55eb400da907f8d0fb795f7c28155e968d22ff4f5b45eae14b8b83f0b2  backend/tests/test_mz2_shipping_contracts.py
abc030ae1544799bdf3721364f5a7d8f7a9bd40091c23b98fb93e2768d2dd224  backend/tests/test_mz2_shipping_payment_evidence.py
cd1495b5f708904d516635fbb3cd7b4e8f0e8b868ba58d6f6f342f76b33ca356  docs/operations/MZ2-FIN-CUTOVER-001/P02-1130-V4-ISOLATION-REVIEW.md
EOF
sha256sum -c "$RUN/sha256.expected" || die BLOCKED_COMBINED_HASH
[ -z "$(gitwt diff --cached --name-status HEAD)" ] || die BLOCKED_STAGED_COMBINED
gitwt diff --check || die BLOCKED_DIFF_CHECK_BEFORE_TESTS
[ "$(sha256sum "$ADMIN/index" | awk '{print $1}')" = "$INDEX_EXPECTED" ] || die BLOCKED_INDEX_CHANGED
[ "$(git -C "$APP" status --short --untracked-files=all)" = "$APP_STATUS_EXPECTED" ] || die BLOCKED_APP_CHANGED

gitwt status --short --untracked-files=all > "$RUN/status.before"
git -C "$APP" status --short --untracked-files=all > "$RUN/app.before"

echo '=== CREATE TEMP CPYTHON 3.13.15 ==='
UV=/opt/bin/uv
[ -x "$UV" ] || die BLOCKED_UV_MISSING
export UV_CACHE_DIR="$RUN/cache"
export UV_PYTHON_INSTALL_DIR="$RUN/python"
"$UV" venv --python 3.13.15 "$RUN/venv" || die BLOCKED_UV_PYTHON_31315
PY="$RUN/venv/bin/python"
VER="$("$PY" -c 'import sys; print(".".join(map(str,sys.version_info[:3])))')" || die BLOCKED_PYTHON_EXEC
[ "$VER" = 3.13.15 ] || die BLOCKED_WRONG_PYTHON "$VER"
echo "PYTHON_VERSION=$VER"

echo '=== INSTALL REFERENCE TEST DEPENDENCIES ==='
"$UV" pip install --python "$PY" --only-binary :all: \
  mongomock-motor==0.0.36 pymongo==4.18.1 \
  fastapi==0.141.1 pydantic==2.13.5 httpx==0.28.1 \
  bcrypt==4.1.3 PyJWT==2.13.0 openpyxl==3.1.5 \
  python-multipart==0.0.20 python-dotenv==1.2.3 cryptography==50.0.0 \
  pytest==9.0.3 pytest-asyncio==1.4.0 email-validator==2.3.0 \
  >"$RUN/install.log" 2>&1 || {
    cat "$RUN/install.log"
    die BLOCKED_DEPENDENCY_INSTALL
  }

BASE_ENV=(
  PATH="$RUN/venv/bin:/usr/local/bin:/usr/bin:/bin"
  HOME="$RUN/home"
  TMPDIR="$RUN/tmp"
  LANG=C.UTF-8
  PYTHONPATH="$WT/backend"
  PYTHONNOUSERSITE=1
  PYTHONDONTWRITEBYTECODE=1
  PYTHONHASHSEED=0
  PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
)

run_unittest() {
  local label="$1" pattern="$2" count="$3" log="$RUN/$1.log"
  echo "=== $label / EXPECT $count ==="
  (
    cd "$WT/backend" || exit 97
    env -i "${BASE_ENV[@]}" "$PY" -m unittest discover -s tests -p "$pattern" -v
  ) >"$log" 2>&1
  local rc=$?
  cat "$log"
  if [ "$rc" -eq 0 ] && grep -Eq "^Ran ${count} tests " "$log" && grep -Eq '^OK$' "$log"; then
    printf '%s\n' "$rc" > "$RUN/$label.rc"
    printf 'PASS\n' > "$RUN/$label.count"
  else
    printf '%s\n' "$rc" > "$RUN/$label.rc"
    printf 'FAIL\n' > "$RUN/$label.count"
  fi
}

run_unittest V4_ISOLATION test_mz2_shipping_contract_isolation.py 32
run_unittest V4_PAYMENT test_mz2_shipping_payment_evidence.py 13
run_unittest REG_CALCULATOR test_mz2_shipping_calculator.py 13
run_unittest REG_CONTRACTS test_mz2_shipping_contracts.py 25

echo '=== REG_LEGACY / EXPECT 5 ==='
(
  cd "$WT/backend" || exit 97
  env -i "${BASE_ENV[@]}" "$PY" -m pytest \
    --noconftest -c /dev/null --rootdir="$WT/backend" \
    --basetemp="$RUN/pytest-tmp" -p no:cacheprovider \
    -q tests/test_courier_cod_fee_tiers_v2.py
) >"$RUN/REG_LEGACY.log" 2>&1
LEGACY_RC=$?
cat "$RUN/REG_LEGACY.log"
LEGACY_COUNT=FAIL
if [ "$LEGACY_RC" -eq 0 ] &&
   grep -Eq '5 passed' "$RUN/REG_LEGACY.log" &&
   ! grep -Eq '[1-9][0-9]* skipped|skipped=' "$RUN/REG_LEGACY.log"; then
  LEGACY_COUNT=PASS
fi

echo '=== POST-TEST INTEGRITY ==='
gitwt status --short --untracked-files=all > "$RUN/status.after"
git -C "$APP" status --short --untracked-files=all > "$RUN/app.after"
STATUS_OK=FAIL
APP_OK=FAIL
HASH_OK=FAIL
INDEX_OK=FAIL
DIFF_OK=FAIL
cmp -s "$RUN/status.before" "$RUN/status.after" && STATUS_OK=PASS
cmp -s "$RUN/app.before" "$RUN/app.after" && APP_OK=PASS
sha256sum -c "$RUN/sha256.expected" >"$RUN/hashcheck.after" 2>&1 && HASH_OK=PASS
[ "$(sha256sum "$ADMIN/index" | awk '{print $1}')" = "$INDEX_EXPECTED" ] && INDEX_OK=PASS
gitwt diff --check >"$RUN/diffcheck.after" 2>&1 && DIFF_OK=PASS

V4I_RC="$(cat "$RUN/V4_ISOLATION.rc")"
V4I_COUNT="$(cat "$RUN/V4_ISOLATION.count")"
V4P_RC="$(cat "$RUN/V4_PAYMENT.rc")"
V4P_COUNT="$(cat "$RUN/V4_PAYMENT.count")"
CALC_RC="$(cat "$RUN/REG_CALCULATOR.rc")"
CALC_COUNT="$(cat "$RUN/REG_CALCULATOR.count")"
CONTRACT_RC="$(cat "$RUN/REG_CONTRACTS.rc")"
CONTRACT_COUNT="$(cat "$RUN/REG_CONTRACTS.count")"

echo '=== FINAL STATUS ==='
cat "$RUN/status.after"
echo '=== P02_1130_RECONSTRUCTED_88_RESULT ==='
echo "HEAD=$HEAD_EXPECTED"
echo "PYTHON=$VER"
echo "V4_ISOLATION_EXIT=$V4I_RC"
echo "V4_ISOLATION_32=$V4I_COUNT"
echo "V4_PAYMENT_EXIT=$V4P_RC"
echo "V4_PAYMENT_13=$V4P_COUNT"
echo "CALCULATOR_EXIT=$CALC_RC"
echo "CALCULATOR_13=$CALC_COUNT"
echo "CONTRACTS_EXIT=$CONTRACT_RC"
echo "CONTRACTS_25=$CONTRACT_COUNT"
echo "LEGACY_EXIT=$LEGACY_RC"
echo "LEGACY_5=$LEGACY_COUNT"
echo "STATUS_UNCHANGED=$STATUS_OK"
echo "FILES_UNCHANGED=$HASH_OK"
echo "INDEX_UNCHANGED=$INDEX_OK"
echo "APP_UNCHANGED=$APP_OK"
echo "DIFF_CHECK=$DIFF_OK"
echo "TEST_RUN_DIR=$RUN"
echo 'COMMIT=NONE'
echo 'PUSH=NONE'
echo 'P02=LOCKED'

if [ "$V4I_RC" -eq 0 ] && [ "$V4I_COUNT" = PASS ] &&
   [ "$V4P_RC" -eq 0 ] && [ "$V4P_COUNT" = PASS ] &&
   [ "$CALC_RC" -eq 0 ] && [ "$CALC_COUNT" = PASS ] &&
   [ "$CONTRACT_RC" -eq 0 ] && [ "$CONTRACT_COUNT" = PASS ] &&
   [ "$LEGACY_RC" -eq 0 ] && [ "$LEGACY_COUNT" = PASS ] &&
   [ "$STATUS_OK" = PASS ] && [ "$HASH_OK" = PASS ] &&
   [ "$INDEX_OK" = PASS ] && [ "$APP_OK" = PASS ] &&
   [ "$DIFF_OK" = PASS ]; then
  echo 'RESULT=RECONSTRUCTED_EXACT_88_OF_88_PASS_ONLY'
  exit 0
fi

echo 'RESULT=RECONSTRUCTED_88_GATE_FAILED'
exit 1
