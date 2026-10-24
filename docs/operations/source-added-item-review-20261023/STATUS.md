# Source-added item review

Task: source-added-item-review-20261023. Candidate implementation, not deployed.
Base: `571e1704fc823c681c61b2f31de1db4f5ea5f55a` on `hotfix/prod-snap-meta-final`.
Task branch: `codex/source-added-item-review-20261023`.

## Approved behavior

Merchant adds a new product line in Salla while the order is in progress.
Web and Android order details show `تمت المراجعة` for that new line. Explicit
approval admits only that line to the shared reviewed/unassigned product catalog
for ordinary later batching with other products. Parent stays in progress;
existing reviewed snapshots, stock units, assignments and physical pieces remain.
Approval itself creates no preparation file, assignment or Salla write.

The server validates live permissions, source identity/fingerprint, current
component lifecycle, unchanged original lines, local completion proof or legacy
review evidence, and whole positive quantity. Workflow append and audit event are
one owner-serialized transaction; repeated clicks acknowledge the prior approval.
Source ingestion appends reservations only for genuinely new lines with unchanged
original content/warehouses and newer source version. Existing-line edits/deletes
are not new-line additions. Completion/instant routing cannot bypass pending
review or physical preparation of additions.

## Changed surfaces

- Source addition HTTP service and existing review router registration.
- Component stock append path and regression tests.
- Reviewed catalog, local policy, preparation stage reconciliation.
- Ready physical coverage and instant routing guards.
- Web order page, shared review hook/service/button and tests.
- Existing real-Mongo CI adds new regression modules.

## Verification so far

- Component and real-Mongo compatibility: 39 passed, 1 pre-existing skip.
- Source review HTTP: 10 real-Mongo tests passed, including actual original
  and added-product mobile preparation-file/PDF and physical-piece creation. Only
  external image download is mocked; duplicate approval, stale source, tenant and
  permission checks, rollback, original piece preservation and normal assignment
  are exercised.
- Catalog/Ready/adjacent suites: 147 passed, 17 Mongo-only skipped, 3 environment
  proxy/PDF-client cases deselected. Baseline Ready counterexamples fail.
- Web targeted UI/service/shipping identity: 28 passed; full CI frontend: 289
  suites / 1968 tests passed.
- Two additional legacy real-Mongo endpoint cases passed: genuine legacy review
  proof permits ordinary assignment; absent reviewed_at blocks approval.
- Android dedicated 17 checks and existing 38 item tracking checks passed. Full
  mobile CI run 39977376011 passed including TypeScript and authentication checks.
- No production data or production configuration changed.

## Android companion

PR https://github.com/AMASI-SA/amasi-mobile/pull/295
Commit `1473acc595eb30bcddfa3cd63f77f6f980b4ad1e`.
Stacked on PR294, exact baseline `a8542ff3cbb6a762d1ba03447c6e2ed391509ba9`.
Full project TypeScript, historical auth checks and CI passed. Device acceptance
and APK build are not yet verified. Android source was prepared from an exact
subset; remote commit correctly uses the full baseline tree and parent.

## Next safe action

Review both verified candidate PRs, resolve inherited dependency audit blockers,
and perform Android device acceptance before any merge/release. No release guard lease,
APK, OTA, merge or deployment performed. Final checkpoint SHA is recorded in the
PR and repository continuation ledger Issue #1006.

## CI and continuation

Server source commit 95f9db9d5174fb60f4c541c0d761aad284e172c2 ran 555 review
contracts: 554 passed, one older test expected automatic catalog admission for a
new Salla line. That test now preserves its exhaustion-filter assertion and
checks exclusion before explicit review, then admission after review. Final CI
run 39978318230 passed on code commit
`9d075b12a4ac61a9feece244f338a585997ac36d`: 557 executed review cases and 317
subtests passed, with zero skipped/failing/error cases, plus the acceptance guard.
The catalog correction and both extra legacy cases are included.

Security Gate fails dependency audits on unchanged inherited manifests (frontend
Vite/follow-redirects; backend audit 37 advisories in 3 packages). Dependency
upgrades are outside this feature patch; do not merge/deploy with this gate red.
All other workflows on code commit 9d075b12 passed. Security Gate is the only
remaining failed workflow. The final status-only commit changes no implementation
or tests; the verified implementation tree is preserved.

An extra combined local test-run follow-up was rejected by automatic approval
review for an unverified external telemetry endpoint. It was not retried or
bypassed. The completed 10 + 2 isolated cases and GitHub CI provide verification;
the supplementary local run is not counted as completed evidence.

Issue #1006 cannot accept comments: GitHub returns 403 because it exceeds 2500
comments. Continuation evidence is preserved in PR #1462 comments and this file.
No production changes, merge, release lease, APK or OTA.

## Final mobile checkpoint

Current Android head `c0251c1fbfc6c788685d3c069158dbf2341365d9` also passed the
full AMASI Mobile CI (run 39978409078). This is a status-only follow-up to the
previous verified code commit. Both candidates remain draft/unmerged.

## 2026-10-24 publication continuation

Owner explicitly authorized continuing and publishing after newer updates.
Integrated the approved feature source onto production `f75b9fcc9e5ac48d214feba005e12e472d1aafb9`
in isolated branch `codex/source-added-item-release-20261024`. Source-only patch
applied without conflicts; current operational accounting, dependency fixes,
print rename recovery and option notices are preserved. Prior frozen release
intent remains inherited and MUST be regenerated for the new source before release.

Read-only deployment evidence: public frontend still reports source
`c59f23a77dcf17bfe506ff4efb577b6e57dc0ac7`. Emergent displays Publishing and a
24 October18:14 Riyadh deployment start. PR1465 records an active lease held by
`codex-operational-integration-20261024-01a1555c`. This task has not changed /app,
created a lease, started a release rehearsal or clicked Publish. Await the other
owner's completed verification/lease closure; do not clear or reuse it.

New integrated CI pending. Android must integrate onto published Build56 source,
not the obsolete original PR294 baseline; read-only compatibility assessment underway.

## Rollback requirement and integrated verification (2026-10-24)

Owner explicitly requires recovery if this update causes a code problem. Added
SOURCE_ADDITION_REVIEW_ENABLED operational disable, with authenticated empty
capabilities, server-side rejection and compatibility retained for approved work.
See ROLLBACK.md; a raw baseline reset after additions exist is unsafe. Four new
regressions await fresh CI. This is source A preparation, not deployment.

Integrated checkpoint b2b9064138c4a41cffd2dbcdcdad9600069deabd passed all 18
workflows, including Security Gate. Review run40107394999: 561 passed plus317
subtests,32 print recovery checks and1 acceptance guard, no skipped/failing cases.
New disable changes require a new full run before freezing source A.

Prior production release is now verified: PR1465 final evidence reports three
v5 checks and inactive lease; this task independently observed inactive guard
and live health identity rg5-91d532b56d827c3f9380e4e20afb24ce0ab72f7e45e3d8afc9964f70dd1ab54e
with source d34eb6f19b0df2da587a70e42b2893984580b7c5. No production changes by this task.
Android integration PR297 source370cc036aad31b84f6f51ab3dcadc9aaaac399c5
preserves published56 session fixes; fresh CI/build and successor version pending.

Next: push recovery changes, pass CI, build/freeze intent-only B and clean-clone
rehearsal, then merge and publish under v5. Issue1006 remains comment-capacity
blocked; PR1466 is continuation ledger fallback. No lease or publish by this task.

## Final publication and recovery handoff — 2026-10-24

Production changed: YES. Owner authorized publication and recovery preparation.
Source A: `5e99dd93f52f04f10ff4a67c49e3938a29228941`.
Intent B: `e97c41d7af6bd03613f6771e9659a0867d595541`.
Deployment merge: `4e4e064ca9855c3a626968df55ef844de77de947` (tree equals B).
Runtime release: `rg5-79259e335434682b97811e55e0ba54abfb6c807cde71fb06b0bd733721a1dd9a`.
Frontend artifact tree: `39c9852195a1d25f944abda1a6ad2f63520248421cdc30096ad58bbf878c112c`.

All applicable final B workflows passed. Review40108302695:565 passed +317
subtests,32 print recovery checks and1 acceptance guard, no skipped/failing
cases. The earlier A run was cancelled when B superseded it; the completed B
run proves the exact implementation. Release40108302622 passed governed A/B,
clean-clone Host20 adapter, isolated package and governed route checks.
Root independently checked the generated bundle and local package boundaries.
The slow supplementary local dependency installation was cancelled(exit130);
no local build is claimed. Verified CI outputs supplied the release bundle.

Generated artifact12565089664 ZIP SHA256:
`859886a9d532b19a1c03966041853fb597b4595c6aa66f21b3c5e94a5fff33b3`.
Inner tar SHA256:
`73d4b06f20ad527edf06f0a7a090d70dca7d42eb7a31e59b3a8f9295e84ad26d`.
/app fast-forwarded without altering pre-existing untracked files. Prior
generated outputs and runtime-created backend bytecode were preserved at
/root/source-addition-prior-release-20261024. An initial package check rejected
prepackaged bytecode; after preserving it outside governed source, package
verification passed. No dependency install/build ran in /app.

V5 prepare succeeded2026-10-24T15:58:20.231781Z, actor
`codex-source-addition-release-20261024-62b367ca`. Prepublish returned
ready_to_publish=true, then one Republish/Publish changes action started at
19:01 Riyadh. Emergent explicitly reported Deployment finished19:09 Riyadh,
platform version `9e3a61f`; previous version `1025b98` remains listed.
Official verify returned verified=true, checks=3 with exact intended backend
and frontend identity; boot2026-10-24T16:08:59.260114Z. Status then active=false.
A display-only wrapper initially attempted len(integer checks) after successful
verification; corrected readback confirmed checks=3 and inactive status.
Do not rerun verification as if the successful lease were still active.

Cloud Build's full adapter transcript was not exposed by the viewed UI; its
View Logs surface showed runtime logs. Do not claim observation of undocumented
platform packaging commands. Clean-clone evidence and live identity probes passed.
No customer order review, financial/accounting, advertising, stock-edit or other
business write was used for deployment acceptance. Physical device/reviewer
end-to-end acceptance remains unverified.

Recovery: previous deployment `f75b9fcc9e5ac48d214feba005e12e472d1aafb9`
(source `d34eb6f19b0df2da587a70e42b2893984580b7c5`) is pinned on
`codex/source-addition-rollback-baseline-20261024`. Prefer the documented
SOURCE_ADDITION_REVIEW_ENABLED=false operational disable across all instances,
retaining approved-work compatibility. It is not an instant cross-instance
switch. Do not click raw platform rollback after additions exist without the
cohort audit described in ROLLBACK.md; no database restore/deletion authorized.

Android57 (1.0.42) source `d0053c51ff4ae241c5a18dce1928d401cf145803`: full
CI40108121290 and40108121246 passed, Expo build succeeded.
https://expo.dev/accounts/amasi-gulf/projects/amasi/builds/edd4f430-8a81-4b05-af5a-e4e3aaf9f3b5
No store submission or OTA. Device installation/fresh login are unverified.
Android final status branch `codex/source-addition-android57-status-20261024`
at `b2a4cf83842d279e3ac5da189b46e04d21f829db`; prior56 pinned on
`codex/source-addition-rollback-android56-20261024`.

This final status-only checkpoint lives separately from frozen release source.
PR1466 is the final continuation ledger (Issue1006 comment capacity exceeded).
Task publication is complete; next action is owner device acceptance or a
specific reported issue. Do not duplicate deployment, rebuild or create a lease
without a new reason; preserve all unrelated work and the recovery references.
