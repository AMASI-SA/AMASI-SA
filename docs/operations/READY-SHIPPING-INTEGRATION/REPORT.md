# AMASI_READY_SHIPPING — integration review

Decision: Draft review only; NO-GO for production release. Draft PR #1327 integrates #1321 and #1325 commits plus the #1305 completed/printing contract. #1305 was not blindly cherry-picked over overlapping #1321 changes. No merge, deployment, release lease, live Salla, financial or advertising writes. Production base: `b41cd9cc36a738a0528dd89ddc7349b31db2a361`. Original PR branches unchanged. Final HEAD/TREE and exact CI conclusions are recorded in the PR checkpoint comment after the report commit, avoiding a self-referential commit identity.

## Operation ordering

1. Ready reads unambiguous canonical Salla status under the existing owner transaction. Root status/slug, raw status/slug/custom/parent signals must agree on in_progress; source refresh/component-pending flags fail closed. Physical/virtual inventory operations retain the same atomic transaction, idempotency and Outbox insertion.
2. All three canonical writers use that owner. A blocked status invalidates existing shipping success, revokes the claim/lease, preserves attempt markers and advances shipping_status_epoch. A later allowed status cannot resurrect the revoked worker.
3. Outbox claims remain leased and CAS-protected. Before the FIRST provider await, capture current shipping metadata, revocation epoch and courier assignment under the owner. Every later guard compares that identity, source, workflow and component evidence under the same owner through final CAS. Provider reads are outside Mongo transactions. Current revision is used only for CAS, not as an across-network material identity.
4. Unconditional status and AWB POSTs are stopped with requires_attention. Attempt markers are never reset; legacy enrollment is readback-only. The merchant can reconcile in Salla, then use the existing route to read back completed status and its current label.
5. Printing requires local assembly completion, two provider completed observations around shipment selection, unchanged provider carrier/shipment evidence, and a current legal canonical local status. When the order omits shipments, reread the shipment endpoint, including fallback merchant-courier selection. Direct refresh also captures shipping identity/assignment/epoch before its first provider read. All carriers share this boundary. A rejected fresh read revokes cached server confirmation; frontend also clears stale success and ignores superseded requests.
6. Legacy result persistence is fenced under the owner and checks the revocation epoch, so a stale successful result cannot overwrite a later rejection.

## Three original counterexamples

The original diagnostic was rerun against the combined #1321/#1325 source before the new guard, using Mongo 8.0.12 replica set on loopback and mock provider only.

| Boundary | Before | After diagnostic |
|---|---|---|
| delivered during status GET | POST status, ready=true | zero POST, ready=false, requires_attention |
| delivered during shipment GET | POST AWB, ready=true | zero POST, ready=false, requires_attention |
| delivered during label refresh | ready=true, confirmed | ready=false, requires_attention |

See before.jsonl and after.jsonl. Diagnostic exit zero is not acceptance; new test_g47_ready_shipping_safety.py asserts these outcomes plus saved-state invalidation, revoked attempt ownership, contradictory source evidence, existing-response-loss readback and terminal carrier rejection. Original #1325 physical/virtual stock assertions remain intact.

## Provider limits and intentional behavior change

Reviewed official [Update Order Status](https://docs.salla.dev/5394148e0), [Create Shipment](https://docs.salla.dev/5394231e0), and [Order Events](https://docs.salla.dev/1894252m0). The documented mutation contracts do not expose a verified expected-status/version/If-Match precondition. Asynchronous event delivery is not a serialized remote compare-and-set guarantee. This is an absence of a verified guarantee, not proof that an undocumented feature cannot exist.

A Mongo check immediately before POST cannot prevent remote status changing during network transit. This candidate therefore sends no unconditional shipping/status POST through these public issuance/Outbox paths. It deliberately sacrifices automatic completion/first-AWB creation pending review; no feature flag silently enables unsafe behavior. Already dispatched attempts from older workers remain readback-only. No external POST is placed inside a retriable Mongo transaction.

A successful GET is a point-in-time observation, not a lease on Salla state. This system cannot prove that Salla remains unchanged after the last response, prevent a merchant action in Salla, or retract a PDF already opened on another device. Canonical Ready eligibility describes accepted local Salla evidence; unobserved remote changes and unordered/stale incoming events remain a provider freshness limitation. Missing/conflicting/known-stale evidence is rejected. No claim of absolute cross-system atomicity is made.

## Validation status

- Original three unsafe outcomes reproduced; post-fix diagnostic blocks all three.
- First focused real-Mongo run: 27 passed, 2 failed due overbroad invalidation adding cache fields to unfinished workflows. Production invalidation narrowed; both original failures plus all three counterexamples rerun: 5 passed in34.258s. No race assertion removed.
- Added revocation/legacy-publication/all-carrier tests: 3 passed +9 subtests in20.41s.
- Frontend:84 passed; initial PR CI frontend build and tests also succeeded.
- Fixture compatibility:72 preparation/payment unit tests passed. Successful fixtures now explicitly seed canonical evidence; no production fallback for test doubles.
- Original shipping unit baseline on new runtime:126 failed,63 passed,128 skipped because Mongo env absent in that unit run. This evidence is retained as a pre-adaptation result, not a passing gate. After fixture corrections and explicit new read-only issuance expectations:317 passed in55.79s, zero skips/failures, with real Mongo configured; see shipping-validation.txt.
- Original #1321 POST-success assumptions are intentionally updated to expect requires_attention/no POST and externally reconciled GET. The crash-after-legacy-dispatch test seeds the durable old marker and proves cancellation/restart readback with zero additional POST. No surviving race is reclassified as success.
- The a175 integrated local run executed 519 cases:513 passed,6 failed,111 subtests passed in688.25s. Failures were five canonical fixture/error-precedence cases plus one inherited copy. Existing component_execution_blocked precedence is restored without bypassing the positive canonical guard; search fixtures now include real canonical source. Targeted corrected contracts:6 passed in19.60s.
- af3 focused safety/Outbox/boundary suite:102 passed,1 skipped,9 subtests passed in155.17s. The single skip is the memory variant of real-transaction rollback; its real Mongo variant passed. Two added direct-refresh real-Mongo races passed in13.82s. Initial-read identity races originally failed5 cases; after the runtime fence, the memory boundary suite passed55, with22 Mongo variants intentionally not run in that memory-only invocation.
- Review Completion CI now explicitly runs the original #1325 canonical race suite and the new shipping safety suite, with no-skip/no-failure XML gates. Final HEAD CI must be read from the PR checkpoint; earlier green or failing checkpoints are not release acceptance.
- Final shipping sweep:392 passed,1 failed,1 skipped in82.01s. The failure was an old contract explicitly allowing an old provider PDF after local shipment replacement. Tightened it to require shipping_snapshot_changed, cleared cached readiness/URL, and preserved the new local identity; all29 current-label tests then passed in2.48s. No unsafe race assertion was relaxed. The one memory-transaction skip has a passing real-Mongo counterpart.
- Independent audit found the fallback merchant shipment selection gap; added its real-Mongo GET-only regression, passed1 in10.12s with stock unchanged. Public issuance/Outbox paths have no reachable provider POST. Older unused private status helpers still contain POST code and are not a supported issuance entry point.

## Owner-lock performance

See PERFORMANCE.json and benchmark_ready_shipping_owner.py for source hashes and synthetic isolation details. Measured source d237e702: Ready135.339/121.791ms, same-owner write during held provider GET30.012ms and finished before GET release. Explicit owner hold204.011ms; contending same-owner writer236.615ms, different-owner34.511ms. No provider POST or Ready provider IO. No production percentile/SLO claim.

## Android Build44

No Android files changed. Source inspected at `D:/amasi-build/build44-auth-integration`, SHA `9f6d02206333ca43ef2b2019214cb1523a8cc496`, frontend/app.json versionCode44. Existing Ready and completed carrier-label/refresh routes are retained because Build44 does not allowlist the new completion/resume route. Positive Ready acknowledgement retains matching piece/order identity, assembly_ready, progress counts and ok. Native transport remains single-attempt15s; unknown results reconcile GET without repeating POST.

Build44 refreshes the existing carrier-label route before printing and checks artifact/screen ownership, but its client does not independently require order_status_completed. Backend current-status rejection therefore remains essential. The existing completed-label refresh route now returns shipping_snapshot_changed for a business shipping rejection, preserving its detailed cause in reason_code; Build44 already invalidates cached labels for that code.37 focused HTTP error-contract tests passed. Authentication/permission failures remain their existing403 contract; installed-client cache handling of those failures is not newly validated. This is source-contract compatibility evidence only; installed APK/device behavior has NOT been verified.

## Next safe action

Review the intentional suspension of automatic status/AWB POST with the owner and obtain an actual provider conditional-write guarantee before proposing restored automation. A green CI does not remove this product limitation or grant release approval. Keep #1327 Draft. Do not merge or deploy.
