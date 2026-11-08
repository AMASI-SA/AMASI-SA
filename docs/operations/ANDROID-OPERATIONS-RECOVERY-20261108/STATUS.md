# Android76 server release evidence

This evidence branch is NOT a deployment candidate; the frozen release remains PR1838 at intent-only B ed088b18887f53f6a56aed3eacb6472079142870.

Authentication succeeded. Fresh /app guard active:false and HEAD cf2e3df3fe11caf9d608a83706d0ae9cafeb95b6; tracked workspace clean, unrelated untracked files preserved. Source A6a1c18d4d87a92cd7a473db1eb8aaba33ae7fc85/tree79d9dccfed7d87911ff5dddd1797c17c588eb7c8 preserves rollback. B tree80083db25a79cc3fce945807c8042a3467310e4f differs from A only in release/release-intent-v5.json.

Source real-Mongo226PASS/59subtests, retained658PASS/345subtests and1acceptance. G47 first attempt had one collection-creation transient WriteConflict, retry cancelled when intent B triggered full CI; B G47 pending. B release readiness42529356911 PASS including Node20/24 clean-clone and package/JSON routing checks. Local Emergent clean clone /tmp/android76-release-rehearsal at exact B completed yarn build in66.38seconds, exit0. Package-boundary and isolated runtime verification passed, matching sourceA and release rg5-b27aabe2e6917c998c4bb3f13eb9c6f979797954a6e74d4edf4a2cf8692e5b6a. No lease created during rehearsal.

Current stage: waiting remaining B CI before normal merge. No merge/publish; production changed:NO. Next: require applicable CI pass, recheck remote production/guard, normal merge preserving A/B, fast-forward /app with unrelated files intact, governed build, prepare/prepublish, publish once, newer explicit success, verify3probes and active:false. Owner deployment authorization persists. APKAndroid76 already delivered; physical mixed-cycle acceptance not run.

## Historical source checkpoints (superseded where above differs)

# Android operations recovery — production-based candidate

## Publication continuation — 9 November 2026 Riyadh

Owner explicitly authorized Mezan publication after Android76 delivery. Android76/1.0.61 was successfully built from mobile3f0567991c20c1b229811ceb8b852d8bb040bf6c; APK package/version/certificate matching75 and native camera/PDF code presence verified. EAS buildf7d6ff87-72da-4e0b-9fc5-bfde9f401b46 succeeded. Physical mixed-cycle acceptance remains unrun.

Production branch advanced to cf2e3df3fe11caf9d608a83706d0ae9cafeb95b6 via an owner rollback of unrelated changes. Transplanted only the exact19-file Android server patch from9e4258d8579a40c35f481ec37cb1f0553e7ca7a9 onto that current base without conflict; no reverted financial/advertising changes restored. New branch codex/android76-server-release-20261109. Fresh local focused suite201PASS/20Mongo-dependentSKIP/59subtests, exit0; diff checkPASS. Fresh remote integration/readiness still required; prior9e425 realMongo226PASS/59subtests +658PASS/345subtests +1acceptance is historical, not certification of this new base.

Emergent management accessible. Code Server requires login; secure credential submission returned Incorrect password. No shared /app guard status acquired, no /app change, rehearsal, lease, merge or publish. Production changed by this task:NO. Next: complete supported Code Server login, read /app guard status and HEAD before any rehearsal; inspect exact new-source CI and current production, freeze new v5 intent-only commit, rehearse, governed publish and three verified production probes. Never clear another task's lease or use the old-base intent.

Scope: employee app Ready/printing recovery and manual order refresh, optional preparation receipt, assignment-date navigation, operational image validation regression. Production changed: no.

Integrated base: de76aed339be775e2878071c92063d0ab9df06df (hotfix/prod-snap-meta-final). This replaces the stacked PR1826 candidate89e9e0a200fc871022a0076d707342399253fa97. Only five required date paths from770b4be863bff0352eab09e3276a8a41cd2dd4b9 were extracted before applying this task; no employee invoice/catalog approval feature was carried over. Dedicated branch: codex/android-operations-integrated-20261108.

Mobile companion: AMASI-SA/amasi-mobile PR346, sourceb57e84696dbd3b260261c0f008d95850ecb2c657. Source changes require these APIs before delivery; no APK or backend deployment is claimed.

Implemented:
- Optional preparation receipt allows direct Ready while retaining supplier, service, hold, order and permission checks; never fabricates a receipt.
- Explicit mobile order refresh reauthorizes the actual employee/store/page before fetch/persist and afterward; no automatic fulfillment, billing or delivery observation.
- Explicit complete-and-refresh label action proves all-piece readiness, leases the order, performs one completed status POST even if already completed, verifies its acknowledgement, then immediately looks up shipment. Ordinary printing remains unchanged; no automatic mutation replay.
- In My Products and monitoring in-progress, a selected day means assignment TO the employee. The product must also belong to a supplier dispatch made by that employee. Supplier dispatch date and order date do not select the day. Existing Mezan/Salla eligibility is preserved unchanged: navigation keeps its original nonterminal prefilter and downstream canonical eligibility remains authoritative. Valid locally reviewed/in-progress orders retain visibility when Salla remains under_review; this task changes the date basis only. Stale customized review status cannot override a current terminal status.
- Employee date navigation does not read file registry dates. Legacy file-date callers retain registration-day selection and server-side batch narrowing. Product detail hydration remains paginated.
- Server image signature validation remains unchanged; regression rejects mislabeled JPEG/WebP and accepts corrected MIME. Mobile encoder corrects MIME from actual payload bytes.

Fresh coordinator verification on integrated working tree:
PYTHONPATH=backend:backend/tests python -m pytest -q --asyncio-mode=auto backend/tests/test_mobile_assembly_refresh_actions.py backend/tests/test_preparation_progress_dates.py backend/tests/test_preparation_piece_operations.py backend/tests/test_amasi_shipping_compat.py backend/tests/test_assembly_completion_delivery.py backend/tests/test_assembly_completion_scheduling.py backend/tests/test_assembly_direct_preparation_receipt.py backend/tests/test_mobile_app_request_context.py backend/tests/test_order_review_operational_images.py --disable-warnings --maxfail=3
Result after eligibility-preservation correction:201 passed,20 skipped,59 subtests passed,exit0. Date and existing local-policy focused verification:43 passed,22 subtests passed. Earlier wider compatibility run before this correction:283 passed,29 skipped,64 subtests passed. Diff check passes.

Real Mongo is unavailable locally: existing pinned8.0.12 executable fails isolated startup with Operation not permitted before initialization. No production URI is used. Existing Review Completion Resumable workflow now runs the focused suite against its disposable Mongo replica and rejects any skip/failure/error, retaining JUnit. Remote result pending.

Next: inspect exact integrated remote SHA/CI, verify current native/mobile combined candidate (monitoring two-column UI is implemented), then governed release and signed APK through the operational build/signing environment. Current repository APK transport is ClosedTransport/NOT_COMMISSIONED. No version bump, OTA, real Salla mutation, deployment or device acceptance has occurred.

Remote intermediate41f799 validation: Review Completion Resumable42521510802 passed219 focused tests/59subtests,658 retained regressions/345subtests and1 acceptance check, all zero skips. A separate supplier-scan workflow exposed3 old lazy-date fixture failures caused by missing employee assignment timestamps. Updated only the synthetic seed timestamp, retaining all original pagination/hydration assertions; the5-case real Mongo lazy suite is now included in focused CI with explicit WAITING_TEST_MONGO_URL. Latest eligibility-preservation correction and fixture follow-up require fresh remote results. No production changes.
