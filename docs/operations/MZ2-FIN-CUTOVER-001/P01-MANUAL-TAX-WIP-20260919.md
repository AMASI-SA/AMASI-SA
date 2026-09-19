# Manual tax and operational recognition — WIP checkpoint 2026-09-19

P01 remains IN_PROGRESS; P02 is locked. This checkpoint is not ready for merge or Production deployment.

## Decision superseding the previous checkpoint
The owner explicitly canceled source-platform VAT as the calculation input. Mezan 2 now uses an explicit manual sales-tax rate, versioned by effective timestamp. Missing configuration is not an explicit zero. Source tax remains review evidence. Posted calculations retain the selected version and split; refunds use cumulative allocation against the original immutable split. Provider commission and settlement-fee VAT are unaffected.

## Implemented candidate
Based on b68ac4d05cda791ee570f9044164991d65c0db33, Draft PR #1091.
- Versioned owner-scoped policy with compare-and-swap revision and audit embedded atomically.
- Accountant source selection, read-only qualification, preview and permission-checked execution.
- Separate accounting.receivables.post permission and existing accounting.rules.manage for policy changes.
- Source identity, tenant, currency, fulfillment/capture and recognition-date checks. The actual Tabby normalizer shape is tested; capture dates are read from retained provider captures and full capture totals are reconciled. No synthetic-only captured_at field is required for that path.
- Existing-journal detection and common Mezan 2 bridge path for accountant/imported-event/sync/webhook recognition.
- Durable event identity and owner serialization. An uncertain ledger write remains blocked and is never automatically retried.
- Existing original-workbook storage and access controls preserved. No recovery tooling added to application source.
- No default cutover or Preview bypass in application code. New synthetic tenant acceptance must use an explicitly marked isolated test cutoff.

## Fresh local evidence
- 11 arithmetic/policy tests and 12 ASGI-to-ledger/service tests with Mongo emulation passed.
- 36 existing settlement/attachment targeted tests passed.
- 24 frontend tests across six suites passed, using a Windows-local Jest runner because CRA path discovery failed.
- Complete candidate frontend Vite build with Preview origin passed.
- Local tests use Python 3.12; explicit mock-Mongo dependencies. They do not prove real Mongo concurrency, browser login persistence or deployed acceptance.
- Existing React act/deprecation and bundle-size warnings remain. Full repository pytest has not run.

## Preview recovery attempt and blocker
The isolated saved task source still matched the checkpoint. The shared /app checkout was observed at 1f2923f19ba503017a04677636d75caad8e4df91 and clean. Initially Release Guard reported active=false and no conflicting Preview reservation was present. The task acquired only its own reservation.

Before restoration performed runtime writes, its fresh guard check found an active release lease and stopped. The lease owner could not be retrieved because the terminal then became unavailable. Do not abort or clear that lease. Direct Preview returned 502 Host Error at 2026-09-19 10:51:23 UTC; independent terminal displayed Preview Unavailable. Admin navigation attempts did not restore access. No paid chat or publish action was used.

The private recovery draft is outside /app and outside the application tree. It did not reach runtime preparation or activation. No application restart or new financial database write was performed. Backend/Frontend identity for the new candidate is unverified.

Own reservation last confirmed at /tmp/mz2-p01-operational-preview.lock, exact owner text:
P01 operational 01a08c76-d160-73c1-bfb1-7ef6940e107a
It could not be released after access failed. On resume, inspect current state and release/reacquire only this exact owner's reservation if appropriate. Never clear another task's reservation.

## Remaining required evidence
1. Restore terminal; verify Release Guard is inactive and reservations permit operation.
2. Recover external isolation runtime from the whole candidate, check authentication-source hashes and local DB, disable external egress/automatic workers, then verify both runtime identities.
3. Snapshot affected Preview documents. Use a new explicitly synthetic owner/fixtures and test cutoff; preserve all three previously completed settlements and their journals.
4. Run accountant UI settings → preview → sale/refund recognition → reviewed new settlement. Verify real ledger/bank balances and retry/cross-ingress conflict behavior.
5. Verify persisted settings/journals after logout/login and original XLSX actually saved by the browser, with tenant/permission tests.
6. Review crash recovery: core post_txn_group still writes legs sequentially. This candidate blocks uncertain attempts; it does not claim transactional group recovery on standalone Mongo.
7. Review source-field compatibility against actual imported orders/payment documents. Incomplete operational evidence must remain rejected; synthetic successes do not establish original-statement reconciliation.
8. Complete review and full candidate acceptance before considering merge. Production release requires separate authorization.

Production changed by this task: no.
