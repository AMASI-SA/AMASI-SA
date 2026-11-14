# READY-PENDING-SOURCE-DIAGNOSIS-20261115

Status: live cause confirmed; no runtime fix; publication HOLD.

## Scope and authority

Investigate why supplier-received pieces remain locked in assembly while the order is in progress. The user explicitly states that inventory is inactive and component-stock shortage must not prevent product preparation. Keep the earlier notification batch and publication hold intact. The cancelled barcode-priority request remains excluded.

After the earlier access restriction, the user explicitly approved opening the production DB manager for a read-only, owner-scoped inspection of the reported order and its matching configuration, lifecycle and review records. That approved inspection succeeded. This checkpoint contains sanitized findings only.

This is a documentation-only checkpoint. It is not a deployment candidate or permission to alter live data.

## Source identity

Initial inspected production lineage: `324dab9fd01fab17a28a5f7de4bb2bd298d7aea9`.
Fresh production branch inspected during this investigation: `7ef45017e5221635ce3c80c3788f73d9c50b1e08`, tree `e55cdf2c2b15cfe08efe3e0305d043e87f916d8b`.
The intervening PR1932 changes campaign operating-period files and release intent only. The assembly/component/review paths inspected here are unchanged. No production ref or shared source was changed by this investigation.

The old reviewed-physical Ready fix was carried through merged PR1912. The present case reaches a different in-progress source-reconciliation gate.

## Confirmed live cause

The accepted and current source snapshots retain the same operational contract, but selected-option price representations differ:

- Ten option price currency fields changed from KWD to SAR.
- Three of those options also have different nonzero amounts; seven retain zero amounts.
- Product and variant identity, item order and membership, quantity, selected option/value identity, labels, selected names, custom fields, normalized options and all other raw option fields match.
- Actual charged order amounts/currency, payment method, and actual charged line amounts/currency/quantity match the immutable approved business snapshot.
- The current source remains revision 5 with `component_pending=true`.
- Its accepted lifecycle remains revision 3, generation 2, accepted, with no retry, no cancellation, no plan and the legacy operational/no-stock cohort.
- The provider timestamp is identical between accepted lifecycle and current source. Current mapped order status is active/in progress and payment eligibility passes.
- The matching owner's settings contain no G47 inventory configuration; the component-plan collection contains no documents.

The current component fingerprint differs from the accepted one. In a local, read-only counterfactual, restoring only the approved option-price amount and currency fields, while retaining every other current field, reproduces the accepted fingerprint exactly. This isolates the price/currency representation mismatch as the cause of the changed component contract.

The existing `restore_zero_price_currencies` recovery requires both old and current amounts to be numeric zero. The three nonzero changes fail that proof. Pending therefore remains set despite unchanged selections and charged amounts.

This is not evidence of component-stock shortage, missing supplier receipt for the two received pieces, or a changed customer selection. The arithmetic ratio between the changed amounts is not independent provider exchange-rate evidence; monetary conversion equivalence has not yet been established from an authoritative saved rate.

## Piece-level impact

The ordinary assembly API response, also supplied by the user, contains five pieces:

- Two physical pieces have supplier receipt and zero remaining services. Employee preparation receipt is optional. They are affected by the shared source-pending gate.
- A third physical piece still requires supplier receipt and must remain locked until that requirement is met.
- Two operational packaging pieces report ready-for-assembly but are also affected by the shared gate.

All five receive `can_mark_ready=false` and the generic `assembly_order_not_ready`. Removing the common source inconsistency must not remove the third piece's receipt requirement or any per-piece service, hold, instruction or status guard.

## Why the screen stays locked

- `assembly_status_policy.canonical_status` returns no eligible status while `component_pending` is set, even when raw/provider and saved statuses consistently say in progress.
- `_assembly_search` translates this into the generic order-not-ready blocker for every piece.
- `allow_legacy_component_execution` independently rejects pending source evidence before Ready writes.
- No G47 cutoff plus no existing plan selects the legacy no-stock cohort, but does not exempt it from current-source acceptance.
- Later source persistence can advance the revision at the same provider timestamp, set pending and attempt narrow revalidation.
- Unchanged-contract revalidation sees the changed fingerprint. Zero-price currency recovery cannot prove the nonzero changes; catalogue-label recovery cannot prove a price change.
- Ordinary non-strict reconciliation treats a changed same-timestamp contract as stale without exact current-revision proof.
- Mobile refresh can report top-level success while nested component acceptance remains deferred.
- No dedicated pending-component retry worker was found; already completed local reviews are not resumed by the retained legacy review worker.

Do not simply clear or ignore `component_pending`. It also participates in the financial source-current guard in `salla_payment_collection.order_page_source_current`, in addition to protecting operational contract changes.

## Source-supported import path; historical writer not observed

The inspected central Salla refresh reads Order Details and separately fetches all Order Items. It then assigns the Items response to `merged_raw["items"]`. The persistence merge preserves missing/empty options, but replaces present nonempty option objects with those received.

The item-fetch and option-merge paths copy provider prices without local arithmetic conversion. The mapper retains the original nested option objects in `options_raw[*].raw`, so an unchanged displayed choice can still acquire a different component fingerprint.

This path can import SAR option prices over previously accepted KWD option prices. No original revision-specific provider response or writer log was captured, so the first historical request that introduced the difference is not identified. Do not attribute the first replacement to a particular webhook or request as an established fact.

Relevant source: `backend/order_engine/salla_refresh.py`, `backend/salla_integration/order_items_snapshot.py`, `backend/orders_db.py`, `backend/order_engine/mapper.py`, `backend/fulfillment_v2_routes.py`, `backend/component_zero_price_recovery.py`.

## Direct verification

The matching review operation is completed, uses `mezan_local_v1`, is not superseded, and carries business snapshot schema 2 / normalization 3. The actual repository snapshot integrity verifier and exact operation/workflow identity binding both pass.

Using the actual mapper and component fingerprint functions offline:

1. Current provider version matches the accepted lifecycle timestamp; active/payment-eligible checks pass and cancellation is false.
2. The component comparison differs only in raw options.
3. The raw option comparison differs only in the ten allowed price currency paths and three associated amount paths.
4. Restoring just those approved amount/currency values reproduces the accepted component fingerprint exactly.
5. Actual order monetary fields, currency and payment method match the approved snapshot.
6. Actual per-line monetary fields, currency and quantity match. Nested unknown fields were compared through the snapshot's actual canonical tree/hash representation, not by comparing an encoded hash with a raw object.

These diagnostic executions completed successfully. They are evidence for the cause, not tests of an implemented repair, database transactions or deployed behavior.

An independent worker ran the existing pure zero-price-proof and immutable-business-snapshot selection: 19 passed with 18 subtests, exit 0. A separate synthetic comparison showed that a component fingerprint alone can miss a changed order total while the full business snapshot catches it; future recovery must retain the monetary proof.

## Previous checks and their limits

Before the approved production read, root exercised the actual cohort, canonical status and legacy execution functions against a read-only synthetic DB boundary, with no configuration or plan. Assertions passed and no DB connection or writes occurred.

An earlier worker reproduced same-timestamp label-only pending behavior with an in-memory Mongo double. That was a possible mechanism, not the diagnosed live cause. The live cause is the nonzero option-price/currency difference established above.

Three selected existing integration tests exited 0 with **3 skipped, 38 deselected** because the required real Mongo fixture was unavailable. They are not passing integration evidence.

A prior code-server diagnostic returned deferred, but that environment did not contain the target owner/order. Its result is discarded and is not production evidence. A potentially mutating review-detail endpoint was not used as a read-only workaround.

## Safe correction boundary; not implemented

A future correction belongs in backend current-source revalidation. It must not bypass Ready eligibility in the mobile UI, omit all prices from the component fingerprint, or blindly clear pending.

Required proof inside the existing owner-fenced transaction:

1. Confirm current legacy/no-stock cohort and no existing component plan; never abandon an existing plan merely because configuration is absent.
2. Require accepted intact lifecycle, valid matching completed immutable local review, exact operation/workflow binding, no supersession, cancellation or retry.
3. Bind the exact current source revision and provider timestamp; retain current order/payment eligibility and authoritative-refresh checks.
4. Prove all operational choices and all other option metadata unchanged; permit differences only in explicitly allowed option-price amount/currency paths.
5. Prove each nonzero currency representation change using saved order-bound provider exchange-rate evidence and the repository's declared direction and rounding rules. Do not infer a rate from the observed option ratio or fetch a current market rate.
6. Compare the full approved/current charged order and line monetary facts, including totals, taxes, discounts and payment facts. A matching component fingerprint alone is insufficient.
7. Require exact reconstruction of the previous accepted component fingerprint by restoring only the proven fields in memory.
8. Acknowledge only the exact current source revision with a bounded proof/audit record. Preserve provider prices and all stock, financial, supplier receipt, custody and assembly completion records.

The smallest remaining evidence for deciding automatic nonzero-currency recovery is the current saved order exchange-rate object and its binding to the approved snapshot. If it is stored as an unknown-field hash, compare the current canonical hash with that saved hash. Missing, changed, ambiguous or incompatible rate evidence must keep automatic recovery unavailable.

Focused regression cases should cover proven foreign-to-SAR changes, zero-price mixed cases and idempotent replay; reject same-currency amount changes, incorrect/missing rate evidence, changed charged totals/payment facts, product/quantity/choice/custom-field/unknown-metadata changes, existing plans, invalid approvals, cancellations and revision races. Confirm zero stock/financial writes and preserve the unreceived piece's lock.

A specific pending-source explanation should replace the misleading generic order-stage wording.

## Access, privacy and mutations

The earlier automatic access rejection was resolved by the user's explicit scoped authorization. Connection credentials were entered through secure browser authentication and were never read or printed by the assistant.

Only the matching order, lifecycle, owner setting and immutable review were examined after scoping. Temporary exported comparison inputs were protected locally and deleted after verification. This document contains no owner, order, customer, option-personalization or live fingerprint identifiers.

No business data, source watermark, lifecycle, inventory, finance, receipt, Ready state, notification, production ref or deployment was changed. No runtime source patch, APK build, EAS update, OTA, merge or publication occurred.

## Continuity

Repository: AMASI-SA/AMASI-SA.
Task branch: `codex/ready-pending-source-diagnosis-20261115`.
Changed file: this status document only.
PR/merge/deploy: none. Production changed by this task: **no**.
Exact checkpoint SHA is recorded by the branch ref; no self-referential SHA is embedded.

Existing notification candidates remain unchanged: backend PR1931 at `ec13c2c6a495b36cf5f70795428267d567634f86`; mobile PR356 at `4763d3a2d627156c3aaad70ec0b237f7af44047d`. They remain held.

Issue1006 readback exposes an oversized/stale body ending in a preceding release handoff, so it is not authoritative for this checkpoint and was not overwritten or truncated. Resume from this dedicated branch, re-read the current production ref and applicable instructions, and preserve all unrelated work.
