# Assembly label observation — WIP, not released

2026-11-10 combined release: all 14 original-source CI workflows passed on8f6990cddf6fb874af4f7135d642ddbf6dda26ed, including real-Mongo source-refresh transaction acceptance. Owner now explicitly requests all fixes in one Mezan publication. This source is integrated into supplier PR1860; combined local suite115passed/one local replica-set skip. Require combined exact-head CI and fresh v5 intent. No publication yet. Source proof does not establish every historical all-ready screenshot incident or handset print timing.

Repository: AMASI-SA/AMASI-SA. Branch: codex/assembly-label-observation-20261110.
Base: 20dc12ac907ca7baafed620ffabbcad23b09103c.

Two defects reproduced before fixes:
- A delivery worker lease rejected explicit label fetch before reading an already completed provider shipment. Added a read-only observation during delivery ownership, preserving manual single-flight, authorization, material snapshot and carrier checks. No lease stealing, provider writes, polling or background work added.
- Employee source refresh reauthorization inside OperationalDatabase treated access-row user_id (employee) as merchant. Real permission lookup inside the restricted capability reproduced operational_owner_scope_conflict. Added an exact find_one owner_user_id + nonempty employee user_id read for fulfillment, retaining existing write restrictions.

Validation: pytest --noconftest -p no:cacheprovider for test_mobile_assembly_refresh_actions.py, test_manual_label_delivery_overlap.py, test_manual_courier_label_refresh_race.py: 53 passed, exit 0. Both new cases failed with their specific reported 409 before their respective fix. Mongo mocked; real replica-set acceptance pending.

Live web observation: an affected external-carrier label opened from one explicit click. Not physical Android or printer evidence. Shared Release Guard freshly returned active:false after terminal sign-in. No shared source mutation, merge, lease, publish or production data repair by this task.

Remaining: negative authorization/concurrency tests, CI integration and real Mongo acceptance; investigate separate all-products-ready versus completion eligibility mismatch using current order data; review and v5 release only after acceptance. Do not treat screenshot mismatch as proven cache accumulation. Preserve unrelated mobile worktree changes and prior successful release.

Production changed: no. Draft PR: https://github.com/AMASI-SA/AMASI-SA/pull/1859.

Follow-up: negative permission queries/write denial, permission revocation during provider GET, changed piece custody and concurrent manual claim tests pass. Current targeted result: 63 passed, one explicitly skipped real-Mongo test (no local disposable replica set), exit 0. The real transaction source-refresh case is included in the existing CI Mongo job; new delivery overlap file added to that job too. Live web inspection of a second affected order shows both pieces ready and completed order; its existing PDF opened on one click. This does not prove the Android readiness mismatch fixed.

Remote initial checkpoint: 6979c22278c68b287e09d32a0f4a0fdd9e8cd6d2, equivalent tree to local c013b0d850b58cbfa2182fdd6cf8650e6344ffc1. CLI push lacks credentials; use connected GitHub object tools, verify equivalent trees and fetch checkpoint before resuming. Latest exact SHA recorded in Issue #1006. Next: inspect exact-head CI including new real transaction case; continue readiness mismatch diagnosis before release. No APK/ADB or physical printer verification.
