# INVENTORY-LOCATION-CODES-20261124

## Current state
**PR2113 merged with the reviewed tree preserved. All exact-B2 checks passed; exact-deployment D CI is running. Live numbering publication remains pending and is already authorized.**

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

## Host coordination and publication boundary
**Live Production changed by this task: no. The Production Git branch advanced to D through the approved PR merge. No own shared-host mutation, lease, prepare, prepublish or publication has occurred.**

The predecessor PR2109 owner acquired the original guard for J2. The host was tracked-clean on J2 at 16:58:59 UTC. Its 100th platform version `d0a7e31` showed `Deployment finished Nov 24, 08:05 PM` (17:05 UTC). Our read-only original-status request at17:15 waited on an existing operation lock; the subsequent original status request began at17:19:35 and returned **active:false at17:25:38.667753 UTC** after the predecessor operation ended. This task did not clear, verify or reuse the foreign lease. A fresh original check at **17:28:11 UTC** again returned `active:false`, with `/app` tracked-clean on J2. Repeat these checks before host work.

[Reviewed helper](operators/pr2113_guarded_rehearsal.py): 23,356 bytes; SHA256 `cdcf838b2f754c83984906148b2c50e144c9b68005b643f7d2f766e23e3b5ccb`. It is preserved remotely and has not been executed. Only expected J/PR identity substitutions differ from the already reviewed helper. Original operation lock, full verified cleanup-target backups, foreign untracked preservation and all fail-closed checks remain intact.

## Exact next safe actions
1. Wait all applicable **exact-D automatic push** Release Readiness, Security Gate and CodeQL checks; expected11Actionsjobs/usually12checkrecords, subject to actual results. Do not rerun PR-only suites manually.
2. Fresh-check Production still D, original guard `active:false`, and clean `/app` at J2. If Production advances, stop and review that release before touching the host; never publish a stale intent.
3. Check Python optimization disabled, bootstrap flag absent, exact non-ignored untracked count and no incoming-path collisions. Independently review the exact seven-field envelope binding J2/A2/B2/D/tree/RID/count.
4. Execute the remotely preserved reviewed helper under the original operation lock. It makes verified backups of all four adapter cleanup targets, fast-forwards to D, invokes original `yarn build`, checks identity/artifact/packages and preserves foreign non-ignored files. It creates no lease.
5. After the matched rehearsal, prepare this task's own lease, prepublish immediately before one authorized Republish, require a newer explicit successful deployment, run original three-probe verification and confirm final `active:false`.
6. Read-only live UI check: open the new cabinet form, verify row/column labels and short-number guidance, preview one row without saving if useful, then cancel. Do not create/delete cabinets.

No further owner approval or numbering/layout clarification is pending. Persist the deployment and final verification milestones in this evidence branch and the bounded [Issue1006 continuation section](https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-6238987123).
