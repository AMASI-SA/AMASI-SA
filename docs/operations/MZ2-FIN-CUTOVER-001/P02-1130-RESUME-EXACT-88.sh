#!/usr/bin/env bash
set -uo pipefail
export LC_ALL=C

APP=/app
ADMIN=/app/.git/worktrees/worktree
WT=/tmp/mz2-p02-stage1.ZMgPW8/worktree
HEAD_EXPECTED=20400fffb03594af8a38d6b4750c170233bd5f37
BRANCH_EXPECTED=refs/heads/local/p02-stage1-ZMgPW8
APP_HEAD_EXPECTED=6365a042dfcb125e81e5e198ea1ff1537373ce51
APP_BRANCH_EXPECTED=refs/heads/hotfix/prod-snap-meta-final
APP_STATUS_EXPECTED='?? .worktrees_p02_runtime.py'

fail() {
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

semantic_index_ok() {
  [ -z "$(gitwt diff --cached --name-status HEAD)" ] || return 1
  [ -z "$(gitwt ls-files --unmerged)" ] || return 1
}

echo '=== VERIFY EXISTING EXACT COMBINED TARGET ==='
[ -d "$WT" ] || fail BLOCKED_WORKTREE_MISSING
[ -f "$WT/.git" ] || fail BLOCKED_WORKTREE_GITFILE_MISSING
[ -d "$ADMIN" ] || fail BLOCKED_ADMIN_MISSING
[ "$(gitwt rev-parse HEAD)" = "$HEAD_EXPECTED" ] || fail BLOCKED_HEAD
[ "$(gitwt symbolic-ref -q HEAD)" = "$BRANCH_EXPECTED" ] || fail BLOCKED_BRANCH
[ "$(git -C "$APP" rev-parse HEAD)" = "$APP_HEAD_EXPECTED" ] || fail BLOCKED_APP_HEAD
[ "$(git -C "$APP" symbolic-ref -q HEAD)" = "$APP_BRANCH_EXPECTED" ] || fail BLOCKED_APP_BRANCH
[ "$(git -C "$APP" status --short --untracked-files=all)" = "$APP_STATUS_EXPECTED" ] || fail BLOCKED_APP_STATUS
semantic_index_ok || fail BLOCKED_SEMANTIC_INDEX

echo "RAW_INDEX_SHA=$(sha256sum "$ADMIN/index" | awk '{print $1}')"
INDEX_MANIFEST_PRE="$(gitwt ls-files --stage | sha256sum | awk '{print $1}')"
echo "SEMANTIC_INDEX_MANIFEST=$INDEX_MANIFEST_PRE"

EXPECTED_STATUS="$(cat <<'EOF'
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
ACTUAL_STATUS="$(gitwt status --short --untracked-files=all)"
[ "$ACTUAL_STATUS" = "$EXPECTED_STATUS" ] || { printf '%s\n' "$ACTUAL_STATUS"; fail BLOCKED_STATUS_SCOPE; }

RUN="$(mktemp -d /tmp/mz2-p02-resume88.XXXXXX)" || fail BLOCKED_RUN_DIR
mkdir -p "$RUN/home" "$RUN/tmp" "$RUN/cache" "$RUN/python"
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

cd "$WT" || fail BLOCKED_CD
sha256sum -c "$RUN/sha256.expected" || fail BLOCKED_HASHES
semantic_index_ok || fail BLOCKED_SEMANTIC_INDEX_PRETEST
gitwt diff --check || fail BLOCKED_DIFF_CHECK_PRETEST

gitwt status --short --untracked-files=all > "$RUN/status.before"
git -C "$APP" status --short --untracked-files=all > "$RUN/app.before"

UV=/opt/bin/uv
[ -x "$UV" ] || fail BLOCKED_UV_MISSING
export UV_CACHE_DIR="$RUN/cache"
export UV_PYTHON_INSTALL_DIR="$RUN/python"
echo '=== CREATE CPYTHON 3.13.15 ==='
"$UV" venv --python 3.13.15 "$RUN/venv" || fail BLOCKED_UV_PYTHON_31315
PY="$RUN/venv/bin/python"
VER="$("$PY" -c 'import sys; print(".".join(map(str,sys.version_info[:3])))')" || fail BLOCKED_PYTHON_EXEC
[ "$VER" = 3.13.15 ] || fail BLOCKED_WRONG_PYTHON "$VER"
echo "PYTHON_VERSION=$VER"

"$UV" pip install --python "$PY" --only-binary :all: \
  mongomock-motor==0.0.36 pymongo==4.18.1 fastapi==0.141.1 pydantic==2.13.5 \
  httpx==0.28.1 bcrypt==4.1.3 PyJWT==2.13.0 openpyxl==3.1.5 \
  python-multipart==0.0.20 python-dotenv==1.2.3 cryptography==50.0.0 \
  pytest==9.0.3 pytest-asyncio==1.4.0 email-validator==2.3.0 \
  >"$RUN/install.log" 2>&1 || { cat "$RUN/install.log"; fail BLOCKED_DEPENDENCY_INSTALL; }

BASE_ENV=(
  PATH="$RUN/venv/bin:/usr/local/bin:/usr/bin:/bin"
  HOME="$RUN/home" TMPDIR="$RUN/tmp" LANG=C.UTF-8 PYTHONPATH="$WT/backend"
  PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONHASHSEED=0 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
)

run_unit() {
  local label="$1" pattern="$2" count="$3" log="$RUN/$1.log"
  echo "=== $label / EXPECT $count ==="
  ( cd "$WT/backend" && env -i "${BASE_ENV[@]}" "$PY" -m unittest discover -s tests -p "$pattern" -v ) >"$log" 2>&1
  local rc=$?
  cat "$log"
  printf '%s\n' "$rc" > "$RUN/$label.rc"
  if [ "$rc" -eq 0 ] && grep -Eq "^Ran ${count} tests " "$log" && grep -Eq '^OK$' "$log"; then
    printf PASS > "$RUN/$label.result"
  else
    printf FAIL > "$RUN/$label.result"
  fi
}

run_unit V4_ISOLATION test_mz2_shipping_contract_isolation.py 32
run_unit V4_PAYMENT test_mz2_shipping_payment_evidence.py 13
run_unit CALCULATOR test_mz2_shipping_calculator.py 13
run_unit CONTRACTS test_mz2_shipping_contracts.py 25

echo '=== LEGACY / EXPECT 5 ==='
( cd "$WT/backend" && env -i "${BASE_ENV[@]}" "$PY" -m pytest --noconftest -c /dev/null \
  --rootdir="$WT/backend" --basetemp="$RUN/pytest-tmp" -p no:cacheprovider -q tests/test_courier_cod_fee_tiers_v2.py \
) >"$RUN/LEGACY.log" 2>&1
LEGACY_RC=$?
cat "$RUN/LEGACY.log"
LEGACY_RESULT=FAIL
if [ "$LEGACY_RC" -eq 0 ] && grep -Eq '5 passed' "$RUN/LEGACY.log" && ! grep -Eq '[1-9][0-9]* skipped|skipped=' "$RUN/LEGACY.log"; then
  LEGACY_RESULT=PASS
fi

gitwt status --short --untracked-files=all > "$RUN/status.after"
git -C "$APP" status --short --untracked-files=all > "$RUN/app.after"
STATUS_OK=FAIL; FILES_OK=FAIL; INDEX_OK=FAIL; APP_OK=FAIL; DIFF_OK=FAIL
cmp -s "$RUN/status.before" "$RUN/status.after" && STATUS_OK=PASS
sha256sum -c "$RUN/sha256.expected" >"$RUN/hash.after" 2>&1 && FILES_OK=PASS
semantic_index_ok && [ "$(gitwt ls-files --stage | sha256sum | awk '{print $1}')" = "$INDEX_MANIFEST_PRE" ] && INDEX_OK=PASS
cmp -s "$RUN/app.before" "$RUN/app.after" && APP_OK=PASS
gitwt diff --check >"$RUN/diff.after" 2>&1 && DIFF_OK=PASS

V4I_RC="$(cat "$RUN/V4_ISOLATION.rc")"; V4I="$(cat "$RUN/V4_ISOLATION.result")"
V4P_RC="$(cat "$RUN/V4_PAYMENT.rc")"; V4P="$(cat "$RUN/V4_PAYMENT.result")"
CALC_RC="$(cat "$RUN/CALCULATOR.rc")"; CALC="$(cat "$RUN/CALCULATOR.result")"
CONTRACT_RC="$(cat "$RUN/CONTRACTS.rc")"; CONTRACT="$(cat "$RUN/CONTRACTS.result")"

echo '=== P02_1130_RESUME_88_RESULT ==='
echo "PYTHON=$VER"
echo "V4_ISOLATION_EXIT=$V4I_RC"; echo "V4_ISOLATION_32=$V4I"
echo "V4_PAYMENT_EXIT=$V4P_RC"; echo "V4_PAYMENT_13=$V4P"
echo "CALCULATOR_EXIT=$CALC_RC"; echo "CALCULATOR_13=$CALC"
echo "CONTRACTS_EXIT=$CONTRACT_RC"; echo "CONTRACTS_25=$CONTRACT"
echo "LEGACY_EXIT=$LEGACY_RC"; echo "LEGACY_5=$LEGACY_RESULT"
echo "STATUS_UNCHANGED=$STATUS_OK"; echo "FILES_UNCHANGED=$FILES_OK"
echo "INDEX_SEMANTIC_UNCHANGED=$INDEX_OK"; echo "APP_UNCHANGED=$APP_OK"; echo "DIFF_CHECK=$DIFF_OK"
echo "RAW_INDEX_SHA_FINAL=$(sha256sum "$ADMIN/index" | awk '{print $1}')"
echo "TEST_RUN_DIR=$RUN"
echo 'COMMIT=NONE'; echo 'PUSH=NONE'; echo 'P02=LOCKED'

if [ "$V4I_RC" -eq 0 ] && [ "$V4I" = PASS ] && \
   [ "$V4P_RC" -eq 0 ] && [ "$V4P" = PASS ] && \
   [ "$CALC_RC" -eq 0 ] && [ "$CALC" = PASS ] && \
   [ "$CONTRACT_RC" -eq 0 ] && [ "$CONTRACT" = PASS ] && \
   [ "$LEGACY_RC" -eq 0 ] && [ "$LEGACY_RESULT" = PASS ] && \
   [ "$STATUS_OK" = PASS ] && [ "$FILES_OK" = PASS ] && \
   [ "$INDEX_OK" = PASS ] && [ "$APP_OK" = PASS ] && [ "$DIFF_OK" = PASS ]; then
  echo 'RESULT=EXACT_COMBINED_88_OF_88_PASS_ONLY'
  exit 0
fi

echo 'RESULT=EXACT_COMBINED_88_GATE_FAILED'
exit 1
