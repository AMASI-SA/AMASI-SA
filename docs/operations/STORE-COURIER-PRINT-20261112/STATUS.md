# STORE-COURIER-PRINT-20261112

## Scope and authorization

- Repository: `AMASI-SA/AMASI-SA`.
- Branch: `codex/google-dashboard-spend-r2-20261112`; draft PR1887.
- Production baseline: `245a0beccbdbc2585a3ec2214e808eeef3734b57`.
- The user authorized fixing and publishing store-courier label printing and
  automatic/explicit completion together with the Google dashboard fix.
- User reports iMile and SMSA work. Their official-label path must remain intact.
- Current state: the reviewed combined checkpoint `cf37d3dad789d123612c8c21b7768235abe561f2`
  passed all 22 exact-source workflows and the local governed build. A subsequent
  live investigation proved historical generic-identity corruption on both
  reported examples. The narrowly fenced recovery is now implemented and has
  passed independent review. The next source checkpoint requires fresh exact
  CI/build evidence and a new intent-only B; publication remains pending.
- Production changed: **no deployment**. No provider status write, shipment
  creation, source merge or release lease by this task. The user subsequently
  authorized direct label/print trials on the reported orders in Mezan; those
  UI reads may update the existing local label-verification bookkeeping.

## Observed defect and evidence boundary

The user reports the carrier display alternating between a named city courier,
the literal `shipping`, and no name. Fetching a label returns the backend's
no-outgoing-shipment message even after Salla shows completion. Production's
read-only order page confirms a completed order with the generic display.

Read-only Salla connector queries for both user-supplied examples confirm the
actual shape: the order response contains `delivery_method="shipping"`, with
no carrier object or embedded shipments. The separate shipment collection
contains exactly one active outgoing child, positively matched to its order,
with a named city courier, a nonzero numeric `courier_id`, `meta.app_id=0`,
`status="created"`, a null creation timestamp, and complete one-page pagination
whose `links` is an empty array. No customer fields or live identifiers are
retained here or in fixtures. The connector's successful read does not prove
the backend integration token has identical permissions.

The shared code workspace's configured DB did not contain either queried form
of the first example's reference. It was not used as evidence about production
workflow state. No credentials were printed or transferred. The live
`assembly_delivery` journal remains unobserved.

## Confirmed source mechanisms

1. `salla_shipping.extract_shipping` interpreted generic `delivery_method` as
   carrier identity before inspecting an outgoing shipment. A newer sparse
   order could therefore replace a named current carrier and clear its label.
2. The mobile strict reader removed a generic canonical identity, then
   `_mobile_label_carrier` reintroduced the same root display. This falsely
   rejected an independently verified current merchant shipment.
3. Even after removing that fallback, the external AWB snapshot validator
   rejected the first internal child because no local shipment ID existed.
   Recovery must retain the unique complete current-child proof, exact source
   fence, cancellation, supersession and timestamp checks; it must never
   persist an unproven replacement or waive external-carrier guards.
4. The mobile explicit `complete-and-refresh` route returned the local courier
   read without requesting completion when it explicitly reported the order
   still in progress. The automatic last-piece outbox has no carrier-specific
   dispatch branch; its live execution is not proven by this investigation.

## Intended behavior and invariants

- Generic `shipping` never establishes or overwrites a carrier identity.
- Existing generic records recover a courier document only from a named
  authoritative order or a unique, current, positively matched provider child.
- A named internal courier yields its merchant QR document, without requiring
  an external AWB/PDF or driver assignment. Unknown data does not imply courier.
- Explicit fetch may use the existing fenced completion action only when the
  local courier read explicitly returns `order_status_completed=False`.
  Already-completed courier reads and ordinary print refresh remain read-only
  with respect to Salla status. No retry after an ambiguous status POST.
- Initial incomplete pieces, cancellation, refresh flags, handoff, permission
  loss and concurrent claims cannot grant a new completion write.
- iMile/SMSA keep their official current PDFs and replacement/cancellation guards.
- No mobile source/APK or concurrent Android78 branch is changed by this task.

## Verification and next step

The extractor regression was red before implementation (16 failed, 5 passed)
and green after (21 passed). Its combined current-shipping/mapper/repository
run passed 151 tests with zero skips; independent rerun of the 21 cases passed.
Actual HTTP identity-recovery tests use synthetic fixtures matching the observed
shape and reject all provider writes. Required real-Mongo CI results are still
pending; mock DB tests do not prove transaction races.

Root identity-slice verification after integrating extraction and recovery:
**225 passed, 59 subtests passed, zero skips**, exit0, 8.70 seconds. Independent
recovery review: 74 passed plus 59 subtests, exit0, no blocking finding. Command:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
PYTHONPATH=backend:backend/tests \
/workspace/scratch/81f2839b514c/courier-test-env/bin/python -m pytest \
  --noconftest -p no:cacheprovider --asyncio-mode=auto -q --tb=short \
  backend/tests/test_salla_generic_shipping_mode.py \
  backend/tests/test_courier_generic_identity_recovery.py \
  backend/tests/test_amasi_shipping_compat.py \
  backend/tests/test_salla_current_shipping.py \
  backend/tests/test_order_engine_mapper.py \
  backend/tests/test_order_engine_repository.py
```

The identity-only remote checkpoint is
`45183c09fbb3a718b154af999aa450f896115de5`. It is not the combined release A.

The completed explicit-fetch slice adds 21 actual-route cases. Initial tests
were red (9 failed, 6 passed). A second deterministic regression exposed a
status POST after employee revocation or handoff during the provider order GET
(2 failed, 19 passed). The internal-only pre-POST callback now reauthorizes and
compares the real captured state immediately before sending. Both cases perform
zero POST. The optional completed-stage guard is enabled only for this new
internal fallback; the external call and defaults are unchanged.

Independent final completion/recovery integration: **84 passed, zero skips**,
exit0, 6.53 seconds. Root final courier integration: **306 passed, 59 subtests,
13 skipped**, exit0, 17.33 seconds. The 13 skipped cases are the Mongo variant
of `test_shipping_refresh_latency.py`; its fixture explicitly requires
`BUILD37_TEST_MONGO_URI` for an isolated replica set. They remain mandatory in
the existing Shipping Print Boundary CI and are not waived. The root command
above was extended with these four files:

```text
backend/tests/test_courier_explicit_completion_recovery.py
backend/tests/test_manual_courier_label_refresh_race.py
backend/tests/test_manual_label_delivery_overlap.py
backend/tests/test_shipping_refresh_latency.py
```

Shipping CI now includes all three new regression files and tracks the affected
route, compatibility reader and explicit-completion helper. Completed/confirmed
and repeated courier reads remain local; matched current-child evidence after
completion is ready on the first fetch. A genuinely absent/unavailable child
with sparse provider identity remains nonprintable, without automatic POST
replay. That negative scenario is not claimed as the observed production cause.

An existing display-only inconsistency can still arise if a historical root
carrier differs from canonical metadata: `projected_shipping` prefers the root.
This task does not broadly rewrite those rows or change real carrier precedence;
the current-document recovery and prevention are separately tested. No claim of
physical-paper printing or live post-deployment success is made yet.

Checkpoint the complete reviewed source, rerun exact-source CI, then freeze a
new protocol-v5 intent-only B.
Fresh-fetch the production ref and Issue1006, verify the official shared guard
inactive, run the actual governed adapter, own prepare/prepublish, publish once
under existing authorization, and verify three consecutive matching probes
with the lease closed. Preserve all concurrent tasks and untracked platform files.

## Live historical investigation after the combined checkpoint

The authenticated existing `GET /api/orders` reader, scoped to the current owner
and one exact order reference at a time, exposed the production metadata without
DB credentials. Both reported examples have generic `shipping`,
`source_kind=order`, `source_path=delivery_method`, `event_name=order.snapshot`,
no current shipment ID, and one superseded ID. In each case that ID is exactly
the sole active merchant child returned by Salla. Actual Mezan UI label attempts
still return pending/no-outgoing before publication. No customer data or live
identifiers are retained in this record.

Executing the old extractor and acceptor reproduces this state. However, a real
carrier change followed by a generic overwrite can produce identical local
metadata. Therefore, no general deletion or bypass of superseded IDs is valid.
The provider history for both examples contains four events, one native
`activity` with the exact carrier-change template
`تم تعديل بيانات الشحن من iMile للتوصيل إلى: مندوب الرياض`, then ordinary
in-progress/completed status changes. The complete list contains one child.

Salla's official Order Details documentation now states that expanded order
responses ended on 2026-09-01. Both fresh light orders lack a street destination.
The first local address has verified order-webhook provenance; the other is
only a customer-city projection and cannot form a complete delivery label.
References: https://docs.salla.dev/orders/details and
https://docs.salla.dev/order-histories/list. The native history schema separates
`activity` from `comment`; the append-history request accepts a note, not a type.
Treating an exact native activity as provider evidence is a bounded inference,
not a claim that arbitrary free text proves current identity.

The proposed narrow recovery returns an order-bound internal QR with shipment,
tracking and label URL fields null, and leaves all source/superseded records
unchanged. It requires the exact generic signature, a singleton superseded ID,
a complete unfiltered singleton shipment list matching that ID and order, one
native external-to-current-courier reassignment in complete history, no later
ambiguous carrier/address/shipment activity, and matching fresh child details.
The child is not reactivated as a shipment or external AWB. Its freshly verified
destination may be used only within this uniquely corroborated cohort, never
by copying an old rendered label or saved child snapshot. Provider identity,
material order content/version, local status/g47/address/assignment, and verified
cancellation evidence require final fences. This proposal remains subject to
actual-route red/green tests and independent review before the next source A.

Exact checkpoint CI evidence: Shipping Print Boundary run43110863797 passed
26 Mongo latency cases, 58 new courier cases and 596 broader cases, with one
intentional memory-only transaction-rollback skip (Mongo counterpart passed).
Runtime run43110863290 passed all 164 reference/baseline/runtime identities and
the zero-skip/I/O audit gate. The local exact-A build and verification both
returned exit0; artifact tree
`ab6e7d8994e8a5e32bb2843225bd156c34996798bb1ee780a5643ee35c535325`.
These are checkpoint evidence, not evidence for the pending recovery delta.

## Reviewed historical recovery implementation

The new `store_courier_history_recovery.py` helper implements the bounded
order-document path above without changing canonical or superseded source data.
It reads order A, a complete unfiltered singleton shipment list, complete native
history, the same complete list again, and order B; it then checks the captured
local source and workflow at the existing final owner boundary. A complete
order-scoped list is sufficient when a global endpoint is unavailable. Missing,
partial or contradictory evidence remains nonprintable.

Destination data comes from the current provider order, or the freshly bound
child when the light order omits its destination. Saved shipment/address
snapshots cannot override this fresh data. Current or saved independent
recipients and gift indicators exclude this narrow buyer-matched recovery.
The proof also rejects concurrent provider material/version changes, local
status/g47/address/assignment/handoff changes, revoked employee access, and a
verified cancellation or unapplied inbox observation. No archived shipment is
reactivated, and no provider mutation occurs for completed orders.

Actual `/v1/refresh` and `/v1/complete-and-refresh` tests use prepared pieces and
the normal route gates. Already-completed historical orders perform only GETs;
a valid in-progress order stays on the existing explicit completion path and
performs its single authorized POST. Localized store-name objects are converted
to Arabic/English/name text at the print-data boundary, fixing the serialized
object visible in the user's working-label screenshot.

The new regression files contain 44 identity/history cases and 18 race cases.
Writer integration: 204 passed plus 59 subtests, zero skips, exit0/13.17s.
Independent broad review: 153 passed plus 59 subtests, zero skips; the final
gift-only delta then passed all 9 selected concrete boundary cases (35 other
cases deliberately deselected), exit0/4.74s. No unresolved review blocker.
Final reviewed helper SHA256:
`85d0be985eed3b1cee0b79480f90c25943907eb5d980f284dcaf0dc4ae7338fe`.

This is deliberately not a general historical-data migration. Other order or
handover pages may retain a generic canonical display because source rows are
unchanged; the recovered print card/document uses the verified courier. The
cohort is the observed single iMile-to-city-courier reassignment, not arbitrary
carrier history. No live post-deployment preview or physical-paper success has
been claimed. Root integrated results and the next exact remote A belong in
the continuation ledger before build/freeze.

Root final integration on the frozen sources: **376 passed, 59 subtests,
13 skipped**, exit0/18.23s. The same 13 isolated Mongo latency variants described
above remain mandatory in Shipping CI. This run extends the earlier 306-case
command with `test_courier_history_identity_recovery.py`,
`test_courier_history_recovery_races.py`, and
`test_recipient_delivery_projection.py`. `git diff --check` also passed. Both
new files and the helper are registered in Shipping CI scope/classification;
the new regression step includes both actual-route files.
