# Employee receipt contract — work in progress

Owner's final contract: supplier receipt, every required product service received (zero services passes), and fresh official Salla in_progress. Components, local review, reservations and payment do not gate custody. Preserve tenant/actor authorization, active piece identity and atomic duplicate handling. Ready/assembly retains its existing component protections. No live receipt test is authorized for this incident.

Base production: 9a8a7f1ef8dedb2a8134c296121360b054204366. Task branch: codex/receipt-two-conditions-20261019 (historic name; final contract has three conditions).

WIP source removes review/component revalidation and promotion from receipt, requires durable supplier receipt fields and completed services, and checks fresh Salla identity/status. Native authorization reloads the real employee and current grant instead of trusting the bridged owner principal. Custody counters remain atomic; file progress remains derived after commit. Search uses fresh provider status rather than stale component/source flags.

Initial focused run: 60 passed, 22 subtests passed, 8 failures. Five were new test request IDs shorter than the existing 8-character minimum; three fixtures lacked supplier receipt evidence. Corrected fixtures/IDs. Expanded Mongo suite running; not yet accepted. Historical tests expecting receipt to acknowledge components or promote workflows require updating to the new contract while keeping Ready protections tested.

Provider status is observed twice, not transactionally locked with Salla. A remote change after the final read remains possible. Current transport has a 30-second per-request timeout and authentication recovery, so latency/transaction deadline needs review before release. No performance improvement claimed.

No new merge, release intent, lease, publication, real receipt, inventory or financial write. Android background-session fix is a separate preserved checkpoint; APK not built or installed. Next: finish focused and affected tests, review network bound and downstream assembly behavior, freeze a reviewed source checkpoint, CI, then protocol-v5 release only after all gates pass.

## Focused verification checkpoint

Immutable production receipt module9a8a7f1 with the new synthetic acceptance case fails409/local_review_receipt_not_eligible (1.65s); updated module passes the identical case (2.29s). Focused custody/contracts69PASS+22subtests32.35s, including concurrent duplicate, provider status change, current native-grant revocation, tenant mismatch, supplier/services proof and timeout rollback. Mongo command listener asserts no component/reservation/payment reads during custody.

Expanded first run112PASS/41subtests but28failures: historical tests expected component acknowledgement, and seeding a live owner without email displaced the existing assignment-fixture fallback. Corrected that fixture with a synthetic email; updated obsolete promotion expectations. Follow-up receipt/assembly10PASS+6subtests with2 stage-expectation failures, then corrected stage-preservation tests2PASS17.61s. Supplier-only receipt followed by normal Ready now reaches completed through existing guarded assembly. Full affected rerun pending. No failing case is waived.

Provider resolution now has a10second total deadline per check, including auth recovery (maximum20seconds across both checks, excluding local DB work). A simulated20second second read is cancelled at the test deadline and transaction writes roll back. This is a bound, not a measured production latency improvement; remote status may change after the last read. No production POST was used for timing.
