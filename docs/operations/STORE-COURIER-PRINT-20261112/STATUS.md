# STORE-COURIER-PRINT-20261112

## Scope and authorization

- Repository: `AMASI-SA/AMASI-SA`.
- Branch: `codex/google-dashboard-spend-r2-20261112`; draft PR1887.
- Production baseline: `245a0beccbdbc2585a3ec2214e808eeef3734b57`.
- The user authorized fixing and publishing store-courier label printing and
  automatic/explicit completion together with the Google dashboard fix.
- User reports iMile and SMSA work. Their official-label path must remain intact.
- Current state: implementation and independent verification in progress.
- Production changed: **no**. No live order mutation, provider status write,
  shipment creation, source merge, release lease or publication by this task.

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
Actual HTTP recovery tests use synthetic fixtures matching the observed shape
and reject all provider writes. Integrated source and required real-Mongo CI
results are still pending; mock DB tests do not prove transaction races.

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

This verified checkpoint does not contain the unfinished explicit-completion
slice or its final CI registration. Do not freeze or deploy it as the combined A.

Finish independent review and root integrated tests, checkpoint the complete
source, rerun exact-source CI, then freeze a new protocol-v5 intent-only B.
Fresh-fetch the production ref and Issue1006, verify the official shared guard
inactive, run the actual governed adapter, own prepare/prepublish, publish once
under existing authorization, and verify three consecutive matching probes
with the lease closed. Preserve all concurrent tasks and untracked platform files.
