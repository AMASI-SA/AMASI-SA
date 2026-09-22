#!/usr/bin/env bash
set -uo pipefail
export LC_ALL=C

APP=/app
ADMIN=/app/.git/worktrees/worktree
PARENT=/tmp/mz2-p02-stage1.ZMgPW8
WT="$PARENT/worktree"
HEAD_EXPECTED=20400fffb03594af8a38d6b4750c170233bd5f37
BRANCH_EXPECTED=refs/heads/local/p02-stage1-ZMgPW8
APP_HEAD_EXPECTED=6365a042dfcb125e81e5e198ea1ff1537373ce51
APP_BRANCH_EXPECTED=refs/heads/hotfix/prod-snap-meta-final
APP_STATUS_EXPECTED='?? .worktrees_p02_runtime.py'
CONTRACT_COMMENT=5783487472
PATCH_COMMENTS=(5782817671 5782818225 5782818706)
PATCH_SHA=14156ef2a45ae489df1d4afcda4c65f4b0804a737837ffbe93cc86f5e7b21d4a

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

expected_final_status() {
  cat <<'EOF'
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
}

write_expected_hashes() {
  cat > "$1" <<'EOF'
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
}

echo '=== PRECHECK GIT ADMIN / APP ==='
[ -d "$ADMIN" ] || fail BLOCKED_ADMIN_MISSING
[ "$(cat "$ADMIN/HEAD")" = "ref: $BRANCH_EXPECTED" ] || fail BLOCKED_ADMIN_HEAD
[ "$(cat "$ADMIN/gitdir")" = "$WT/.git" ] || fail BLOCKED_ADMIN_GITDIR
[ "$(cat "$ADMIN/commondir")" = '../..' ] || fail BLOCKED_ADMIN_COMMONDIR
[ "$(cat "$ADMIN/locked")" = 'P02 PR1130 isolated stage1; preserve' ] || fail BLOCKED_LOCK
[ "$(git -C "$APP" rev-parse "$BRANCH_EXPECTED")" = "$HEAD_EXPECTED" ] || fail BLOCKED_LOCAL_BRANCH
[ "$(git -C "$APP" rev-parse HEAD)" = "$APP_HEAD_EXPECTED" ] || fail BLOCKED_APP_HEAD
[ "$(git -C "$APP" symbolic-ref -q HEAD)" = "$APP_BRANCH_EXPECTED" ] || fail BLOCKED_APP_BRANCH
[ "$(git -C "$APP" status --short --untracked-files=all)" = "$APP_STATUS_EXPECTED" ] || fail BLOCKED_APP_STATUS
for f in MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD REBASE_HEAD BISECT_START; do
  [ ! -e "$ADMIN/$f" ] || fail BLOCKED_GIT_OPERATION "$f"
done
for d in rebase-merge rebase-apply sequencer; do
  [ ! -d "$ADMIN/$d" ] || fail BLOCKED_GIT_OPERATION "$d"
done

RUN="$(mktemp -d /tmp/mz2-p02-resume88.XXXXXX)" || fail BLOCKED_RUN_DIR
mkdir -p "$RUN/home" "$RUN/tmp" "$RUN/cache" "$RUN/python"

NEED_REBUILD=0
if [ ! -d "$WT" ]; then
  NEED_REBUILD=1
  mkdir -p "$PARENT" "$WT"
  printf 'gitdir: %s\n' "$ADMIN" > "$WT/.git"
  chmod 600 "$WT/.git"
fi

[ "$(gitwt rev-parse HEAD)" = "$HEAD_EXPECTED" ] || fail BLOCKED_TARGET_HEAD
[ "$(gitwt symbolic-ref -q HEAD)" = "$BRANCH_EXPECTED" ] || fail BLOCKED_TARGET_BRANCH
semantic_index_ok || fail BLOCKED_SEMANTIC_INDEX_BEFORE_REBUILD
INDEX_MANIFEST_BEFORE_REBUILD="$(gitwt ls-files --stage | sha256sum | awk '{print $1}')"
echo "SEMANTIC_INDEX_MANIFEST=$INDEX_MANIFEST_BEFORE_REBUILD"
echo "RAW_INDEX_SHA=$(sha256sum "$ADMIN/index" | awk '{print $1}')"

if [ "$NEED_REBUILD" -eq 1 ]; then
  echo '=== REBUILD TRACKED HEAD ==='
  git -C "$APP" archive "$HEAD_EXPECTED" | tar -x -C "$WT" || fail BLOCKED_HEAD_ARCHIVE
  [ -z "$(gitwt status --short --untracked-files=all)" ] || fail BLOCKED_BASELINE_NOT_CLEAN

  echo '=== RESTORE EXACT CONTRACT_SLICE ==='
  python3 - "$WT" "$CONTRACT_COMMENT" <<'PY' || fail BLOCKED_CONTRACT_TRANSFER
import base64, hashlib, json, re, sys, urllib.request
from pathlib import Path
wt = Path(sys.argv[1]); cid = int(sys.argv[2])
req = urllib.request.Request(
    f"https://api.github.com/repos/AMASI-SA/AMASI-SA/issues/comments/{cid}",
    headers={"Accept":"application/vnd.github+json","User-Agent":"MZ2-P02-contract-recovery-v2"},
)
with urllib.request.urlopen(req, timeout=30) as r:
    body = json.load(r)["body"]
items = [
 ("MODULE", "backend/accounting_shipping_contracts.py", "9f25d896d9835dc36a29e91f2dab90de2cf8f1a4c2cec18f01d9fb097863b7c8", 10380),
 ("TESTS", "backend/tests/test_mz2_shipping_contracts.py", "61168e55eb400da907f8d0fb795f7c28155e968d22ff4f5b45eae14b8b83f0b2", 12961),
 ("LEGACY", "backend/tests/test_courier_cod_fee_tiers_v2.py", "ff35204aaaecfb897869341c82b9c86b82d7580ce70fb397c617edfd76933f9c", 3211),
]
for tag, rel, expected, size in items:
    m = re.search(rf"BEGIN_{tag}_B64\n([A-Za-z0-9+/=\n]+)\nEND_{tag}_B64", body)
    if not m: raise SystemExit(f"missing {tag}")
    raw = base64.b64decode("".join(m.group(1).split()), validate=True)
    sha = hashlib.sha256(raw).hexdigest()
    if len(raw) != size or sha != expected: raise SystemExit(f"{tag} mismatch size={len(raw)} sha={sha}")
    p = wt / rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(raw)
    print(f"{tag}=OK bytes={len(raw)} sha256={sha}")
PY

  echo '=== RESTORE EXACT V4 PATCH ==='
  python3 - "$RUN/v4.patch" "${PATCH_COMMENTS[@]}" <<'PY' || fail BLOCKED_PATCH_TRANSFER
import base64, hashlib, json, re, sys, urllib.request
from pathlib import Path
out = Path(sys.argv[1]); ids = [int(x) for x in sys.argv[2:]]; parts=[]
for n,cid in enumerate(ids,1):
    req=urllib.request.Request(
        f"https://api.github.com/repos/AMASI-SA/AMASI-SA/issues/comments/{cid}",
        headers={"Accept":"application/vnd.github+json","User-Agent":"MZ2-P02-v4-recovery-v2"},
    )
    with urllib.request.urlopen(req,timeout=30) as r: body=json.load(r)["body"]
    m=re.search(rf"BEGIN_P02_1130_V4_PART_{n}\n([A-Za-z0-9+/=\n]+)\nEND_P02_1130_V4_PART_{n}",body)
    if not m: raise SystemExit(f"missing patch part {n}")
    parts.append("".join(m.group(1).split()))
raw=base64.b64decode("".join(parts),validate=True); sha=hashlib.sha256(raw).hexdigest()
if len(raw)!=89921 or sha!="14156ef2a45ae489df1d4afcda4c65f4b0804a737837ffbe93cc86f5e7b21d4a":
    raise SystemExit(f"patch mismatch size={len(raw(} sha={sha}")
out.write_bytes(raw); print(f"PATCH=OK bytes={len(raw)} sha256={sha}")
PY
  [ "$(sha256sum "$RUN/v4.patch" | awk '{print $1}')" = "$PATCH_SHA" ] || fail BLOCKED_PATCH_SHA
  gitwt apply --check "$RUN/v4.patch" || fail BLOCKED_V4_APPLY_CHECK
  gitwt apply "$RUN/v4.patch" || fail BLOCKED_V4_APPLY
else
  echo '=== EXISTING WORKTREE FOUND: VERIFY ONLY ==='
fi

EXPECTED_STATUS="$(expected_final_status)"
ACTUAL_STATUS="$(gitwt status --short --untracked-files=all)"
[ "$ACTUAL_STATUS" = "$EXPECTED_STATUS" ] || {
  printf '%s\n' "$ACTUAL_STATUS"
  fail BLOCKED_FINAL_STATUS_SCOPE
}

write_expected_hashes "$RUN/sha256.expected"
cd "$WT" || fail BLOCKED_CD
sha256sum -c "$RUN/sha256.expected" || fail BLOCKED_COMBINED_HASH
semantic_index_ok || fail BLOCKED_SEMANTIC_INDEX_AFTER_REBUILD
INDEX_MANIFEST_PRETEST="$(gitwt ls-files --stage | sha256sum | awk '{print $1}')"
[ "$INDEX_MANIFEST_PRETEST" = "$INDEX_MANIFEST_BEFORE_REBUILD" ] || fail BLOCKED_INDEX_ENTRIES_CHANGED

gitwt diff --check || fail BLOCKED_DIFF_CHECK_BEFORE_TESTS
[ "$(git -C "$APP" status --short --untracked-files=all)" = "$APP_STATUS_EXPECTED" ] || fail BLOCKED_APP_CHANGED

gitwt status --short --u[˜XÚÙYYš[\ÏX[ˆ‰•S‹ÜÝ]\Ë˜™Y›Ü™H‚™Ú]PÈ‰TˆÝ]\ÈK\ÚÜK][˜XÚÙYYš[\ÏX[ˆ‰•S‹Ø\˜™Y›Ü™H‚‚•UKÛÜØš[‹Ý]‚–È^‰UˆˆH˜Z[“ÐÒÑQÕU—ÓRTÔÒS‘Â™^ÜU—ÐÐPÒWÑTH‰•S‹ØØXÚH‚™^ÜU—ÔUÓ—ÒS”ÕSÑTH‰•S‹Ü]Ûˆ‚™XÚÈ	ÏOOHÔ‘PUHÔUÓˆËŒLËŒMHOOIÂˆ‰Uˆˆ™[ˆK\]ÛˆËŒLËŒMH‰•S‹Ý™[ˆˆ˜Z[“ÐÒÑQÕU—ÔUÓ—ÌÌLÌMB”P’SH‰•S‹Ý™[‹Øš[‹Ü]Ûˆ‚•‘TH‰
‰P’SˆˆXÈ	Ú[\ÜÞ\ÎÈš[
‹ˆ‹š›Ú[ŽX\
Ý‹Þ\Ë™\œÚ[Û—Ú[™›ÖÎŒ×JJJIÊHˆ˜Z[“ÐÒÑQÔUÓ—ÑVPÂ–È‰‘TˆˆHËŒLËŒMHH˜Z[“ÐÒÑQÕÔ“Ó‘×ÔUÓˆ‰‘Tˆ‚™XÚÈ”UÓ—Õ‘T”ÒSÓI‘Tˆ‚‚ˆ‰Uˆˆ\[œÝ[K\]Ûˆ‰P’SˆˆK[Û›KXš[˜\žH˜[ˆˆ[Û™ÛÛ[ØÚË[[ÝÜOLŒŒÍˆ[[Û™ÛÏOMŒNŒH˜\Ý\OOLŒMKŒHY[XÏOL‹ŒLËHˆOLŒŽŒH˜Üž\OMŒKŒÈR•ÕOL‹ŒLËŒÜ[œ^OLËŒKHˆ]Û‹[][\\OLŒŒŒ]Û‹YÝ[OLKŒ‹ŒÈÜž\ÙÜ˜\OOMLŒŒˆ]\ÝONKŒŒÈ]\ÝX\Þ[˜Ú[ÏOLKŒ[XZ[]˜[Y]ÜOL‹ŒËŒˆˆ‰•S‹Ú[œÝ[›ÙÈˆ‰ŒHÈØ]‰•S‹Ú[œÝ[›ÙÈŽÈ˜Z[“ÐÒÑQÑTS‘SÖWÒS”ÕSÈB‚TÑWÑS•JˆUH‰•S‹Ý™[‹Øš[Ž‹Ý\Ü‹ÛØØ[Øš[Ž‹Ý\Ü‹Øš[Ž‹Øš[ˆ‚ˆÓQOH‰•S‹ÚÛYHˆTTH‰•S‹Ý\ˆS‘ÏPË•U‹NUÓ”UH‰ÕØ˜XÚÙ[™‚ˆUÓ““ÕTÑT”ÒUOLHUÓ‘Ó•Ô’UP–UPÓÑOLHUÓ’TÒÑQQLUTÕÑTÐP“WÔQÒS—ÐUUÓÐQLBŠB‚œ[—Ý[š]\Ý

HÂˆØØ[X™[H‰Hˆ]\›H‰ˆˆÛÝ[H‰ÈˆÙÏH‰•S‹ÉK›ÙÈ‚ˆXÚÈOOH	X™[ÈVPÕ	ÛÝ[OOH‚ˆ
ˆÙ‰ÕØ˜XÚÙ[™ˆ^]MÂˆ[ˆZH‰ÐTÑWÑS•–Ð_Hˆ‰P’Sˆˆ[H[š]\Ý\ØÛÝ™\ˆ\È\ÝÈ\‰]\›ˆˆ]‚ˆ
Hˆ‰ÙÈˆ‰ŒBˆØØ[˜ÏIÂˆØ]‰ÙÈ‚ˆš[ˆ	É\×‰È‰˜Èˆˆ‰•S‹ÉX™[œ˜È‚ˆYˆÈ‰˜ÈˆY\HH	‰ˆÜ™\Q\H—”˜[ˆ	ØÛÝ[H\ÝÈˆ‰ÙÈˆ	‰ˆÜ™\Q\H	×“ÒÉ	È‰ÙÈŽÈ[‚ˆš[ˆ	ÔTÔ×‰Èˆ‰•S‹ÉX™[˜ÛÝ[‚ˆ[ÙBˆš[ˆ	ÑRS‰Èˆ‰•S‹ÉX™[˜ÛÝ[‚ˆšBŸB‚œ[—Ý[š]\ÝÒTÓÓUSÓˆ\ÝÛ^Œ—ÜÚ\[™×ØÛÛ˜XÝÚ\ÛÛ][Û‹œHÌ‚œ[—Ý[š]\ÝÔVSQS•\ÝÛ^Œ—ÜÚ\[™×Ü^[Y[Ù]šY[˜ÙKœHLÂœ[—Ý[š]\Ý‘Q×ÐÐSÕSUÔˆ\ÝÛ^Œ—ÜÚ\[™×ØØ[Ý[]Ü‹œHLÂœ[—Ý[š]\Ý‘Q×ÐÓÓ•PÕÈ\ÝÛ^Œ—ÜÚ\[™×ØÛÛ˜XÝËœHB‚™XÚÈ	ÏOOH‘Q×ÓQÐÖHÈVPÕHOOIÂŠˆÙ‰ÕØ˜XÚÙ[™ˆ^]MÂˆ[ˆZH‰ÐTÑWÑS•–Ð_Hˆ‰P’Sˆˆ[H]\ÝK[›ØÛÛ™\ÝXÈÙ]‹Û[ˆK\›ÛÝ\H‰ÕØ˜XÚÙ[™ˆKX˜\Ù][\H‰•S‹Ü]\Ý]\ˆ\›Î˜ØXÚ\›ÝšY\ˆˆ\H\ÝËÝ\ÝØÛÝ\šY\—ØÛÙÙ™YWÝY\œ×ÝŒ‹œBŠHˆ‰•S‹Ô‘Q×ÓQÐPÖK›ÙÈˆ‰ŒB“QÐPÖWÔÏIÂ˜Ø]‰•S‹Ô‘Q×ÓQÐPÖK›ÙÈ‚“QÐPÖWÐÓÕS•QRSšYˆÈ‰QÐPÖWÔÈˆY\HH	‰ˆÜ™\Q\H	ÍH\ÜÙY	È‰•S‹Ô‘Q×ÓQÐPÖK›ÙÈˆ	‰ˆHÜ™\Q\H	ÖÌKNWVÌNWJˆÚÚ\YÚÚ\YIÈ‰•S‹Ô‘Q×ÓQÐPÖK›ÙÈŽÈ[‚ˆQÐPÖWÐÓÕS•TTÔÂ™šB‚™Ú]ÝÝ]\ÈK\ÚÜK][˜XÚÙYYš[\ÏX[ˆ‰•S‹ÜÝ]\Ë˜Y\ˆ‚™Ú]PÈ‰TˆÝ]\ÈK\ÚÜK][˜XÚÙYYš[\ÏX[ˆ‰•S‹Ø\˜Y\ˆ‚”ÕUT×ÓÒÏQRSÈTÓÒÏQRSÈTÒÓÒÏQRSÈS‘VÓÒÏQRSÈQ‘—ÓÒÏQRS˜Û\\È‰•S‹ÜÝ]\Ë˜™Y›Ü™Hˆ‰•S‹ÜÝ]\Ë˜Y\ˆˆ	‰ˆÕUT×ÓÒÏTTÔÂ˜Û\\È‰•S‹Ø\˜™Y›Ü™Hˆ‰•S‹Ø\˜Y\ˆˆ	‰ˆTÓÒÏTTÔÂœÚLMœÝ[HXÈ‰•S‹ÜÚLM‹™^XÝYˆˆ‰•S‹Ú\ÚÚXÚË˜Y\ˆˆ‰ŒH	‰ˆTÒÓÒÏTTÔÂœÙ[X[X×Ú[™^ÛÚÈ	‰ˆÈ‰
Ú]ÝËYš[\ÈK\ÝYÙHÚLMœÝ[H]ÚÈ	ÞÜš[	_IÊHˆH‰S‘VÓPS’Q‘TÕÔ‘UTÕˆH	‰ˆS‘VÓÒÏTTÔÂ™Ú]ÝY™ˆKXÚXÚÈˆ‰•S‹ÙY™˜ÚXÚË˜Y\ˆˆ‰ŒH	‰ˆQ‘—ÓÒÏTTÔÂ‚•WÔÏH‰
Ø]‰•S‹ÕÒTÓÓUSÓ‹œ˜ÈŠHŽÈWÐÓÕS•H‰
Ø]‰•S‹ÕÒTÓÓUSÓ‹˜ÛÝ[ŠH‚•ÔÏH‰
Ø]‰•S‹ÕÔVSQS•œ˜ÈŠHŽÈÐÓÕS•H‰
Ø]‰•S‹ÕÔVSQS•˜ÛÝ[ŠH‚ÐS×ÔÏH‰
Ø]‰•S‹Ô‘Q×ÐÐSÕSUÔ‹œ˜ÈŠHŽÈÐS×ÐÓÕS•H‰
Ø]‰•S‹Ô‘Q×ÐÐSÕSUÔ‹˜ÛÝ[ŠH‚ÓÓ•PÕÔÏH‰
Ø]‰•S‹Ô‘Q×ÐÓÓ•PÕËœ˜ÈŠHŽÈÓÓ•PÕÐÓÕS•H‰
Ø]‰•S‹Ô‘Q×ÐÓÓ•PÕË˜ÛÝ[ŠH‚‚™XÚÈ	ÏOOH’SSÕUTÈOOIÂ˜Ø]‰•S‹ÜÝ]\Ë˜Y\ˆ‚™XÚÈ	ÏOOH—ÌLLÌÔ‘PÓÓ”Õ•PÕQÎÔ‘TÕSOOIÂ™XÚÈ’PQIPQÑVPÕQ‚™XÚÈ”UÓI‘Tˆ‚™XÚÈ•ÒTÓÓUSÓ—ÑVUIWÔÈŽÈXÚÈ•ÒTÓÓUSÓ—ÌÌIWÐÓÕS•‚™XÚÈ•ÔVSQS•ÑVUIÔÈŽÈXÚÈ•ÔVSQS•ÌLÏIÐÓÕS•‚™XÚÈÐSÕSUÔ—ÑVUIÐS×ÔÈŽÈXÚÈÐSÕSUÔ—ÌLÏIÐS×ÐÓÕS•‚™XÚÈÓÓ•PÕ×ÑVUIÓÓ•PÕÔÈŽÈXÚÈÓÓ•PÕ×ÌOIÓÓ•PÕÐÓÕS•‚™XÚÈ“QÐPÖWÑVUIQÐPÖWÔÈŽÈXÚÈ“QÐPÖWÍOIQÐPÖWÐÓÕS•‚™XÚÈ”ÕUT×ÕSÒS‘ÑQIÕUT×ÓÒÈŽÈXÚÈ‘’ST×ÕSÒS‘ÑQITÒÓÒÈ‚™XÚÈ’S‘VÔÑSPS•P×ÕSÒS‘ÑQIS‘VÓÒÈŽÈXÚÈTÕSÒS‘ÑQITÓÒÈŽÈXÚÈ‘Q‘—ÐÒPÒÏIQ‘—ÓÒÈ‚™XÚÈ”U×ÒS‘VÔÒWÑ’SSI
ÚLMœÝ[H‰QRS‹Ú[™^ˆ]ÚÈ	ÞÜš[	_IÊH‚™XÚÈ•TÕÔ•S—ÑTI•Sˆ‚™XÚÈ	ÐÓÓSRUS“Ó‘IÎÈXÚÈ	ÔTÒS“Ó‘IÎÈXÚÈ	ÔSÐÒÑQ	Â‚šYˆÈ‰WÔÈˆY\HH	‰ˆÈ‰WÐÓÕS•ˆHTÔÈH	‰ˆˆÈ‰ÔÈˆY\HH	‰ˆÈ‰ÐÓÕS•ˆHTÔÈH	‰ˆˆÈ‰ÐS×ÔÈˆY\HH	‰ˆÈ‰ÐS×ÐÓÕS•ˆHTÔÈH	‰ˆˆÈ‰ÓÓ•PÕÔÈˆY\HH	‰ˆÈ‰ÓÓ•PÕÐÓÕS•ˆHTÔÈH	‰ˆˆÈ‰QÐPÖWÔÈˆY\HH	‰ˆl€ˆ‘1e}=U9Pˆ€ôAMLt€˜˜p(€€l€ˆ‘MQQUM}=,ˆ€ôAMLt€˜˜l€ˆ‘!M!}=,ˆ€ôAMLt€˜˜p(€€l€ˆ‘%9a}=,ˆ€ôAMLt€˜˜l€ˆ‘AA}=,ˆ€ôAMLt€˜˜ˆÈ‰Q‘—ÓÒÈˆHTÔÈNÈ[‚ˆXÚÈ	Ô‘TÕST‘PÓÓ”Õ•PÕQÑVPÕÎÓÑ—ÎÔTÔ×ÓÓ“IÂˆ^]™šB‚™XÚÈ	Ô‘TÕST‘PÓÓ”Õ•PÕQÎÑÐUWÑRSQ	Â™^]B