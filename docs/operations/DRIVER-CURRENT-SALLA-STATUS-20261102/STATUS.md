# Driver current Salla status — 2026-11-02

Task: `DRIVER-CURRENT-SALLA-STATUS-20261102`.
Repository: `AMASI-SA/AMASI-SA`.
Branch: `codex/driver-current-salla-status-20261102`.
Verified production base: `3d8720860fb524d729f6c83a8c56c5b4de3a2afb`.

## Requirement and implementation

Only orders whose current synchronized Salla status is **delivering / جاري التوصيل** belong in a driver's current deliveries. Any other status, including shipped, delivered, completed, canceled, restored and unknown, removes the order from current home/search results and their counts.

- The existing batched canonical order lookup now filters current assignments before returning home items, district counts, account delivery counts or search details. Both legacy duplicated home/search registrations use the shared rule; hidden searches return 404. Account monetary totals still use their durable earning/collection/settlement records.
- Status conflicts and refresh-required observations fail closed. Parent/original status categories cannot override the actual current custom status. The historical normalized `shipping` token needs an explicit delivering observation; `shipped` is never accepted.
- Canonical identities must match unambiguously. A missing second identity is supported; an explicit contradiction is rejected.
- GETs perform no order, assignment, receipt, earning or collection write and make no per-order Salla request. Tenant and driver scoping remain enforced.
- The mixed web `/deliveries` surface retains locally delivered historical rows needed for late evidence and rejected receipt recovery. Native home/search contain current work only. Financial reports continue to read durable history.
- The web UI clears removed selected details and ignores superseded refresh responses. An unsubmitted completion form receives current amounts and closes when its delivery is no longer current. Removal during proof upload prevents a new status submission. An unresolved submission retains its source snapshot, exact payload/proof and explicit retry path independently from the selected detail. HTTP 408 and a rejected retry cannot discard an earlier uncertain attempt; a definitive first validation rejection remains editable.
- The existing Store Delivery CI test command includes the new HTTP regression suite.

## Verification

The production-base router was executed in memory against the new HTTP regression. The shipped-order case failed for the intended reason: the old home response included the stale assignment. No source file was reverted for this check.

Fresh candidate checks:

```text
PYTHONPATH=backend /tmp/mezan-driver-tests/bin/pytest -q \
  backend/tests/test_store_delivery_current_status.py \
  backend/tests/test_store_delivery_self_assignment.py \
  backend/tests/test_store_delivery_driver_app_v2_contract.py \
  backend/tests/test_store_delivery_driver_security.py \
  backend/tests/test_store_delivery_domain.py \
  backend/tests/test_store_delivery_operational_v2_contract.py
105 passed; exit 0

CI=true node node_modules/react-scripts/bin/react-scripts.js test \
  --watchAll=false --runInBand --runTestsByPath src/pages/AmasiDeliveryApp.test.jsx
33 passed; exit 0

git diff --check
exit 0
```

The web regression first failed on stale selected details and an unsubmitted form; its pending-submission preservation control already passed. The final suite also checks an older background response cannot restore a removed delivery. The follow-up reproduced three failures before its correction: an unknown first result followed by retry 401 or 409 discarded the form, and an initial HTTP 408 unlocked its payload. The initial validation-rejection control already passed. Backend HTTP checks use a Mongo-compatible in-memory database and mocked provider boundaries. They exercise both registered home/search implementations, non-delivery statuses, conflicting/missing/sparse identities, tenant isolation and unchanged history/financial records.

The sandboxed TestClient initially stalled in the AnyIO cross-thread portal. The two stalled test processes were stopped. The same unchanged tests completed outside that namespace sandbox; they did not use production credentials, data or provider calls. A Starlette TestClient deprecation warning is unrelated to the change.

## Native companion and release limits

The companion `AMASI-SA/amasi-mobile` task is `codex/courier-current-status-20261102`, based on reviewed courier PR #307 at `c8cdc2c7891f04e111384aebd70b50b56988c966`. It reconciles the separately cached native quick-search/details state. Its status document records exact source and native verification; a new signed APK is required because the delivered courier build has OTA disabled.

Freshness follows the existing Salla webhook and status synchronization. This patch does not claim knowledge of a provider change before it reaches the canonical order. The explicit initial pickup API remains available; its current-list row appears once the canonical snapshot reflects delivering. No new status-write or source-writeback behavior is introduced.

The user explicitly authorized server publication and the updated signed native APK. Production publication is in progress as recorded below. No live order status or accounting data was manually changed. Installed-device acceptance remains outstanding.

Issue #1006 has reached GitHub's 2,500-comment limit (confirmed in its final continuation entry). This task's PR body and this document provide the durable continuation record instead of another failed ledger write.

Initial remote source checkpoint: `5569b23c1a0199a2461675ccf05fc1649eb4fde4` (tree `3a52ae8920eb6fc42738346a599e2dcd3e931db4`). The PR head records the exact follow-up checkpoint containing the additional uncertain-retry correction above.

## Authorized release checkpoint — 2026-11-02

- Source A: `b6c6e42fce74d8900526a81e1d5a88be3cc297c4`, tree `9aac07a3b10cf8410126ef1110739ad811a25e09`.
- Intent-only B: `98630af107939536ea630f51ef1558cf69fc1361`, tree `605545ceec21e232c88bdcd4d0c2f5937eb5621e`.
- Conventional merge: `70576e2a06237eb193b1c927f11ced92e2229589`, tree exactly B, parents J and B. [PR #1688](https://github.com/AMASI-SA/AMASI-SA/pull/1688) is merged.
- All 12 applicable B workflows passed, including all four required branch checks and both Host Node 20.20.2/24.19.0 clean-clone adapter rehearsals: [Readiness 41457474046](https://github.com/AMASI-SA/AMASI-SA/actions/runs/41457474046).
- Correct source-A proof: `01782d16b0acf04532e684364a9420225b93f5d4d33a53ae6bc483cb7f420bfb`. The candidate ZIP artifact 13104709452 hash was `0dc4d66e318f948f6a404da6e0d668da2d3f910ed58b62ab9c6511f0672b9601`.
- Full backend manifest: `9517a53b5bd18050f674ccec8a696afb866993b6df128ec43f9d02deb1e8ce85`, 1056 reviewed files. Release-control manifest: `542e5248a81ca0996b163f67a45b1d10efc953d3b40e9a86e9d0a22f540c540f`.
- Runtime release: `rg5-cc743775a823588ac1de627e70855d9c29682ad31d1d97d2dce3d084dfce8f81`.
- Shared `/app` fast-forwarded from J to the reviewed merge after fresh guard `active:false`. Unrelated `.emergent/testing/`, `deployer-agent-docs/`, and the unsaved editor buffer were preserved.
- Actual host: aarch64, Node 24.19.0, Yarn 1.22.22, Python 3.11.18. Adapter uses repository-governed Node 22.23.2/Yarn 1.22.22.
- First host adapter attempt refused two ignored derived `.pyc` files. Fresh inactive guard was verified, and those two files were moved intact outside `/app` to `/tmp/mezan-driver-bytecode-20261102-9djfxy6g`. No guard or source bypass. Retry with bytecode writes disabled passed in 91.99s; built=true, package-boundary and isolated backend health/frontend runtime checks passed.
- Frontend artifact tree: `bb9024962a20bc664efec18c689927b1c5218d68fbeeb00f788bd9ecf7a393f1`; build-meta SHA256 `648165c9ae1bf408ebe700c81bf99635acfa5d4dd72adf0fa8de2944bd961934` (301685 bytes).
- Own v5 lease: actor `chatgpt-driver-current-salla-status-20261102`, prepared `2026-11-02T14:09:18.748838+00:00`. Prepublish returned ready_to_publish=true for exact A/merge/release.
- One Publish changes action was accepted. UI shows **Deployment Started Nov 2, 05:10 PM (Asia/Riyadh)**, Changes packaged, then Building your website. Input transport timed out after the click, but fresh UI proved it started; no second publish was sent.

Production changed: **yes — server release published and verified**. The one owned lease closed through successful verification; subsequent status returned **active=false**.

## Completed server acceptance

The current Emergent UI showed green **Deployment finished Nov 2, 05:19 PM (Asia/Riyadh)** and a new published version `5b4a0a6`. This is the literal observed UI label; no different success wording is claimed.

Official command `PYTHONDONTWRITEBYTECODE=1 python scripts/production_release_guard.py verify --url https://mezansalla.com` completed with exit 0 and:

```json
{
  "verified": true,
  "checks": 3,
  "source_git_sha": "b6c6e42fce74d8900526a81e1d5a88be3cc297c4",
  "source_base_git_sha": "3d8720860fb524d729f6c83a8c56c5b4de3a2afb",
  "deployment_git_sha": "70576e2a06237eb193b1c927f11ced92e2229589",
  "release_id": "rg5-cc743775a823588ac1de627e70855d9c29682ad31d1d97d2dce3d084dfce8f81",
  "boot_started_at": "2026-11-02T14:19:24.196869+00:00",
  "frontend_artifact_tree_sha256": "bb9024962a20bc664efec18c689927b1c5218d68fbeeb00f788bd9ecf7a393f1",
  "final_guard_active": false,
  "ui_status": "Deployment finished Nov 2, 05:19 PM (Asia/Riyadh)",
  "platform_publish_id": "5b4a0a6"
}
```

The three probes accepted all required health/identity flags, the complete backend/control manifests, critical hashes, the exact frontend artifact/proof, canonical and cache-busted build-meta JSON bytes/MIME, public shell/assets and retirement service-worker responses. Boot identity was stable across the three probes and newer than lease preparation. Final tracked worktree was clean; the two pre-existing unrelated untracked directories and unsaved editor were preserved.

Raw verifier output is 5,845 bytes at `/tmp/mezan-driver-status-20261102-verified.json` in the shared host. The durable minimum acceptance fields are above. No real driver account UI or installed-device acceptance is claimed by this identity verification.

## Completed native release and delivery

Signed local Android [run 41461059770](https://github.com/AMASI-SA/amasi-mobile/actions/runs/41461059770) completed successfully on exact merge `d5fdda1e6a141703c56f9a0c82b971fe2a3a5ee0`, tree `11c4313db19f0c54de3731be89e032a0d044432b`. The validation job passed all 56 tests, TypeScript and Expo Doctor 18/18. Signed job 125330310589 completed at 2026-11-02 17:34:51 Asia/Riyadh (14:34:51 UTC); Gradle reported BUILD SUCCESSFUL in 10m42s.

- Package: `com.amasi.sa.delivery`; version **1.0.17**, version code **18**.
- APK: `AMASI_Delivery_1.0.17_18.apk`, **84,424,726 bytes**.
- APK SHA-256: `438f108d2883ace40b613a015f38b0df6208903ba186ac31987d25e86a0ae94f`.
- Original signer SHA-256: `d59891595cc2564036a2c56f7f27f77b7fc1f4d58aa3a5e09562cb873983d37f`; apksigner verification passed.
- [Artifact 13150840921](https://github.com/AMASI-SA/amasi-mobile/actions/runs/41461059770/artifacts/13150840921): ZIP **40,459,773 bytes**, SHA-256 `ee848528bc8f6e2144b4a3f382592c7abbcf8c8dc912e00476c4e55e7fc94bb1`; download matched API and CI upload digests before extraction.
- Exact package/version/code and existing Firebase/notification resources were verified. OTA remains disabled.
- The signed APK was saved successfully for the user's download. No app-store submission, OTA publication, or driver message was sent.

**Acceptance: server production published and verified; signed APK built, independently checked and saved for delivery.** Install this APK as an update to courier 1.0.16/17, then open/refresh the app. Current work is filtered using the current synchronized Salla status; after the accepted current-home refresh, cards/forms for orders no longer delivering are reconciled. Historical/accounting records are preserved.

Installed-device UI, camera/layout and actual phone push delivery remain untested. Server identity verification does not establish real driver-account UI acceptance. No further deployment, merge, build or artifact upload is pending for this release. Do not repeat these operations.

This evidence branch is separate from the runtime branch and frozen server A/B source. Documentation changes do not alter the published runtime.

Native detailed evidence: [https://github.com/AMASI-SA/amasi-mobile/blob/codex/courier18-release-evidence-20261102/docs/operations/courier-current-status-20261102/STATUS.md](https://github.com/AMASI-SA/amasi-mobile/blob/codex/courier18-release-evidence-20261102/docs/operations/courier-current-status-20261102/STATUS.md).
