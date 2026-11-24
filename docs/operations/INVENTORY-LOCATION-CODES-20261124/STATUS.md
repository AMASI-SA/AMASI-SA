# INVENTORY-LOCATION-CODES-20261124

## Current state
**PR2113 merged with the reviewed tree preserved. All exact-B2 and exact-deployment D checks passed; the reviewed host rehearsal is now running. Live numbering publication remains pending and is already authorized.**

The owner settled **cabinet → column → row** and will create the cabinet themselves. This task changes numbering/display behavior only. Live cabinet creation and deletion of the old cabinet are deferred. No cabinet, location, inventory quantity, barcode identity, historical or financial record was directly changed by this task.

## Repository and immutable source
- Repository: `AMASI-SA/AMASI-SA`.
- Production branch: `hotfix/prod-snap-meta-final`; current base J2 `20fc28f3432a78996f1d70f3356a38bd78b0309e`.
- Release branch: `codex/inventory-location-short-codes-release2-20261124`.
- A2 source: `a611477be8d52b18f9ad851f44facf9c78be2905`; tree `3379fb9bc4be6b7b1f0e40523173ab801ec9774f`; sole parent J2.
- B2 intent: `6005a1be25a7a9402442a389f3052ffab8664e07`; tree `abcdea0a41cb694cdd229492a96a7b68e25d8a3a`; sole parent A2.
- Deployment merge D: `cb98e5660380d362e78f937d60abc7139cd1c462`; tree exactly B2's `abcdea0a41cb694cdd229492a96a7b68e25d8a3a`; parents exactly [J2,B2].
- [PR2113](https://github.com/AMASI-SA/AMASI-SA/pull/2113) merged normally at **2026-11-24 17:30:16 UTC**. Root and independent reviews confirmed the exact tree/parents and intent-only A2→D diff. Production Git ref is D; the live site has not been published by this task.
- [Frozen source status and complete changed-surface history](https://github.com/AMASI-SA/AMASI-SA/blob/a611477be8d52b18f9ad851f44facf9c78be2905/docs/operations/INVENTORY-LOCATION-CODES-20261124/STATUS.md).
- [Source diff from J2](https://github.com/AMASI-SA/AMASI-SA/compare/20fc28f3432a78996f1d70f3356a38bd78b0309e...a611477be8d52b18f9ad851f44facf9c78be2905): exactly 20 owned paths (10 backend, eight frontend, two task documents); 19 non-status blobs unchanged from reviewed A1. J2's preceding eight-path production delta has zero overlap.
- B2 changes only `release/release-intent-v5.json`. No source/documentation changes may follow frozen A2 in this release branch. This evidence branch is **never merged** into it.
- Older PR2110 and PR2106 are closed/unmerged; their branches and successful verification evidence remain preserved.

## Completed behavior
| Cabinet | Column | Row | Visible code |
| --- | --- | --- | --- |
| 1 | 1 | 1 | `111` |
| 1 | 2 | 1 | `121` |
| 1 | 2 | 3 | `123` |
| 2 | 3 | 1 | `231` |
| 1 | 12 | 3 | `1-12-3` |

New cabinets explicitly select `cabinet-column-row`, including inside legacy warehouses. A shared atomic owner/warehouse counter reserves above valid existing cabinet numbers across sections/statuses. New-policy partial uniqueness preserves legacy compatibility. Labels propagate through map/list/search/details/printing and relevant selectors. IDs, full scanner/QR values, historical records and legacy labels keep their established meanings. Rows/columns are user-selected; one row is supported without changing existing defaults. No reset, deletion or migration is included.

## Verification
- Exact A2 focused backend: **56 passed in 5.35s**, exit 0, no skips.
- Exact A2 isolated real MongoDB 7.0.14: **10 passed in 8.25s**, exit 0, no skips; owned loopback fixture only, external DB environment removed.
- Original governed Node22.23.2/Yarn1.22.22 two-clean-build: both passes succeeded (11.15s/11.14s; 7,853 modules each), identical artifact/proof, retained pass 2.
- Original build verifier, external freeze and candidate validator passed; normal tracked-intent B2 validator also passed without bootstrap override. [Tracked receipt](TRACKED-INTENT-VALIDATION.json).
- Independent source, candidate, complete proof/manifests, exact B2 bytes/ancestry and identity-only helper review: **PASS**.
- Root re-read **56 exact-B2 check records: 46 success and ten conditional skips, zero pending/failure/neutral** at 17:27:14 UTC; the independent reviewer confirmed the same at17:27:52. The initial47 all passed/conditional; marking the PR ready triggered nine additional accounting/Snapchat check records automatically, which also completed. No manual rerun.
- Source integration: **17/17 steps**, backend **290 +237 subtests**, full frontend **491 suites /5,552 tests**, and subsequent persistence/browser/mobile/RTL/lifecycle integration phases passed.
- Clean-clone adapter rehearsals: Host Node20.20.2 and24.19.0 each **21/21 steps** passed with the exact reviewed RID/artifact/build-meta/proof and isolated package checks.
- Stock Eligibility: **6,216 +97 subtests**, all13steps passed with no-skips enforcement. Operational Backend: **7,543 +151 subtests**, followed by106 and16 +18 subtests, all16steps passed. CodeQL, Security and every other applicable B2 gate passed.
- Skips are explicit manual/branch/unrelated-surface conditions; no required gate was waived or rerun to conceal a failure.

## Reviewed runtime identity
- RID: `rg5-1a6146f1c27f7bf287f7185591bcbfa1f88ea0ecc066ae398b4bbb3c4259b72e`.
- Intent: 1,530,059 bytes; SHA256 `cdc7a0d5af053b63f57b65fc896c133377fa8bb9892f260f94b28bcfde5546a0`; Git blob `e5b2870da4cd713d8c98fb80d8a1ad91c72219b7`.
- Artifact tree: `20bb37b904e519478211c634321272a4a293c5e44f3b3e0c77fc90e8cb3c9cc6`.
- build-meta: 591,784 bytes; SHA256 `b8aad6fcdd48f782ff9e8deaf57e1b701bedf567b7dfa2b27f532638e5323433`.
- Reproducibility proof: 1,521 bytes; SHA256 `8f582c809003c06d706e5a9513fb449638cf96731aa03f211e3e59bd72a36e58`.
- Full machine-readable evidence, commands, checks, logs/hashes and source manifests: [RELEASE-EVIDENCE.json](RELEASE-EVIDENCE.json).

## Exact-deployment CI and reviewed envelope
All **11 actual D check records passed**, across three automatic push workflows: [Readiness](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45449291028), [Security](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45449290986), and [CodeQL](https://github.com/AMASI-SA/AMASI-SA/actions/runs/45449291302). All are exact D, push, attempt1; no pending, failure, neutral or skips. The final independent observation was17:40:09 UTC. There is no separate CodeQL aggregate on this push. Both Host20/24 rehearsals again passed21/21steps with the same A2/RID/proof; manual handoff passed3/3steps.

[Reviewed seven-field envelope](operators/pr2113-reviewed-envelope.json): **429bytes**, SHA256 `7f806d949e255c252097cabafa43851f63d932660396c5de7840ff33de865f0a`; Git blob `d8f89ae6b2eff5a3774f09e36216aa26ff9543a0`. It binds J2/A2/B2/D/tree/RID and expected non-ignored untracked count **432**. Independent envelope/schema/identity/count review passed.

Original read-only host preflight at17:34:56 UTC: guard inactive, app J2 on the Production branch, tracked clean,432non-ignored untracked entries, zero cleanup overlaps, no collisions for five incoming new paths or symlinked parents, Python optimization0, bootstrap flag absent, real .git directory and sufficient free space. These are preliminary readings; repeat applicable conditions immediately before staging/execution. The helper also rechecks under the original operation lock.

## Host coordination and publication boundary
**Live Production changed by this task: no. The Production Git branch and host checkout are exact D. The first host rehearsal was interrupted before an observed result. No own lease, prepare, prepublish or publication has occurred.**

The predecessor PR2109 owner acquired the original guard for J2. The host was tracked-clean on J2 at 16:58:59 UTC. Its 100th platform version `d0a7e31` showed `Deployment finished Nov 24, 08:05 PM` (17:05 UTC). Our read-only original-status request at17:15 waited on an existing operation lock; the subsequent original status request began at17:19:35 and returned **active:false at17:25:38.667753 UTC** after the predecessor operation ended. This task did not clear, verify or reuse the foreign lease. A fresh original check at **17:28:11 UTC** again returned `active:false`, with `/app` tracked-clean on J2. Repeat these checks before host work.

[Reviewed helper](operators/pr2113_guarded_rehearsal.py): 23,356 bytes; SHA256 `cdcf838b2f754c83984906148b2c50e144c9b68005b643f7d2f766e23e3b5ccb`. It is preserved remotely. It was executed once; its final result was not observed and its temporary receipt is no longer available after the host editor reconnected. Only expected J/PR identity substitutions differ from the already reviewed helper. Original operation lock, full verified cleanup-target backups, foreign untracked preservation and all fail-closed checks remain intact.

## Interrupted host rehearsal and recovery
The original helper was staged and launched with reviewed bytes from evidence commit `9f70905566ef4ae7ce4938dcd78fa5ce6d29f8ac`. Its only observed stdout was `STAGE_PATH=/tmp/mezan-pr2113-rehearsal-sbyjrhil`. Code-server then disconnected. After reconnect/reload, the previous terminal and task paths under /tmp were absent. The cause is not established; no PASS is claimed for that interrupted run.

At **17:55:33 UTC**, /app was exact D on the Production branch, tracked-clean, with432 non-ignored untracked entries and original guard inactive. At **18:04:26 UTC**, read-only checks found frontend/build, frontend/.release and backend/release_identity.json missing; frontend/node_modules remained a real0755 directory. No matching old helper, original adapter, Vite or yarn build process remained.

The old J2-only helper must not be rerun on D. No reset or rollback is planned. A separate strict-D recovery operator has passed independent review. It retains J2 as the reviewed source base, all original operation-lock, verified-backup, foreign-file preservation, canonical adapter and identity/proof/package checks, and omits only the already completed FF step. Its private stage and backups will be beneath the real /app/.git rather than /tmp.

The interrupted run's pre-change snapshot/backups are unavailable. We will not claim historical byte preservation from the surviving count alone. Recovery will preserve and verify a fresh current non-ignored-file baseline. No source, candidate or CI change is needed: the exact A/B/D identities and successful gates above remain frozen.

## Exact next safe actions
1. Stage the exact reviewed recovery-operator and envelope bytes after fresh original guard/source/precondition checks.
2. Run the original canonical adapter build at exact D under the original operation lock with fresh verified cleanup-target backups and foreign-file preservation. Require final PASS and independently review the receipt.
3. Prepare this task's own lease, prepublish immediately before one authorized Republish, require a newer explicit successful deployment, run original three-probe production verification and confirm final inactive guard.
4. Read-only live cabinet form check: verify row/column labels and short-number guidance, preview one row without saving, cancel. No creation/deletion.
Publication authorization persists; no new approval is pending. Do not reset source, weaken guard checks, delete foreign data or record the interrupted run as successful.

## Reviewed recovery operator
[Strict-D recovery helper](operators/pr2113_recover_d_rehearsal.py): **23,977 bytes**, SHA256 `4fe5070533e8f6308fc88516d1af80f8fa12f5523e7ecf8a7777317a4182b798`, Git blob `70b22d0996e91b5f3e3eca837cbdefbba8535fe2`. Independent exact-diff and AST review **PASS**:20function bodies identical; only existing-D start/noFF/private.git stage, initial-head receipt and fresh-preservation scope metadata changed. Original controls remain intact. The seven-field429byte envelope is unchanged.

Fresh read-only preflight **18:07:39 UTC**: exactD/prodbranch/trackedclean,432non-ignored untracked entries, original guard inactive, Python optimization0, bootstrap absent and real.git directory. Current cgroup8GiB limit, about2GB use and zero OOM counters do not establish the cause of the earlier disconnect. Repeat mutable conditions before execution. Helper subsequently executed once after the fresh staging checks below.

## Active strict-D recovery
At18:10:18 UTC, staging under the original operation lock verified original guard inactive, exactDclean, Production refD,432untracked and exact helper/envelope bytes. Operator:`/app/.git/mezan-pr2113-recovery-operator-jyn9frvb`.

Recovery started once from/app in owned Terminal1. Persistent stage:`/app/.git/mezan-pr2113-recover-d-cm4y9sgc`; receipt:`/app/.git/mezan-pr2113-recover-d-cm4y9sgc/receipt.json`. At18:11:07 UTC original normal intent validation had passed, all432current foreign entries matched, and canonical-app-adapter was RUNNING. Generated build/.release/runtimeidentity were absent at the fresh baseline. Existing node_modules backup was fully copied/verified:60,704,294bytes,11,483entries. No lease or publication. Read this existing process/receipt; do not rerun or reset.
