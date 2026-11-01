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
- The web UI clears removed selected details and ignores superseded refresh responses. An unsubmitted completion form receives current amounts and closes when its delivery is no longer current. Removal during proof upload prevents a new status submission. An unresolved submission retains its source snapshot, exact payload/proof and explicit retry path independently from the selected detail.
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
29 passed; exit 0

git diff --check
exit 0
```

The web regression first failed on stale selected details and an unsubmitted form; its pending-submission preservation control already passed. The final suite also checks an older background response cannot restore a removed delivery. Backend HTTP checks use a Mongo-compatible in-memory database and mocked provider boundaries. They exercise both registered home/search implementations, non-delivery statuses, conflicting/missing/sparse identities, tenant isolation and unchanged history/financial records.

The sandboxed TestClient initially stalled in the AnyIO cross-thread portal. The two stalled test processes were stopped. The same unchanged tests completed outside that namespace sandbox; they did not use production credentials, data or provider calls. A Starlette TestClient deprecation warning is unrelated to the change.

## Native companion and release limits

The companion `AMASI-SA/amasi-mobile` task is `codex/courier-current-status-20261102`, based on reviewed courier PR #307 at `c8cdc2c7891f04e111384aebd70b50b56988c966`. It reconciles the separately cached native quick-search/details state. Its status document records exact source and native verification; a new signed APK is required because the delivered courier build has OTA disabled.

Freshness follows the existing Salla webhook and status synchronization. This patch does not claim knowledge of a provider change before it reaches the canonical order. The explicit initial pickup API remains available; its current-list row appears once the canonical snapshot reflects delivering. No new status-write or source-writeback behavior is introduced.

Production changed: **no**. No production merge, release intent, lease, deployment, live status change or accounting write by this task. Source checkpoint and CI review precede any separately authorized server release/native build. Installed-device acceptance remains outstanding.

Issue #1006 has reached GitHub's 2,500-comment limit (confirmed in its final continuation entry). This task's PR body and this document provide the durable continuation record instead of another failed ledger write.

Next: preserve exact source remotely, finish independent review and CI, link the native companion, then request the final production/native release authorization with these concrete results.
