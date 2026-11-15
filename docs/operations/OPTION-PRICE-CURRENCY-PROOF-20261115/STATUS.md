# Option-price currency representation recovery

## Scope and checkpoint

- Task: `OPTION-PRICE-CURRENCY-PROOF-20261115`.
- Repository: `AMASI-SA/AMASI-SA`.
- Branch: `codex/option-price-currency-proof-20261115`.
- Starting source: `7ef45017e5221635ce3c80c3788f73d9c50b1e08`.
- Implementation commit: `a9f563890c2be4c36b026eb8c081cb9dcacddd75` (local).
- State: implementation verified locally; exact-head real-Mongo CI is next.
- Owner authorization on 2026-11-15: explicitly approved pushing, merging and
  publishing this fix ("تمام وادمج وانشر"). The earlier push refusal is resolved
  by this new explicit instruction. Publication still requires the normal
  verification and v5 release gates.
- Production changed: **no**. No merge, release intent, release lease, deployment,
  order mutation, supplier receipt, or financial write was performed.
- The prior hold is lifted for this fix. This task does not authorize unrelated
  notification work to be included in either repository's release.

## Implemented behavior

`backend/component_option_currency_recovery.py` proves the narrow case where
approved nonzero KWD option prices later arrive as SAR. It binds the original
order exchange-rate envelope to the immutable approved business snapshot,
checks the completed local approval and its lineage, and compares every source
fact and unknown source-field fingerprint after restoring only the permitted
option envelopes in a temporary copy. Original source values, the approved
snapshot, accounting amounts, selections, quantities, stock, and receipts remain
unchanged.

The proof uses native minor units (KWD 0.001, SAR 0.01), rejects non-native
precision and exact half-unit boundaries, and requires the SAR representation
to lie strictly within half a fils of the approved KWD amount at the bound
order rate. This is an operational equivalence policy; it does not reconstruct
a unique historical SAR option price or assert Salla's tie-breaking policy.
Other currencies/directions or any independent financial/source change fail
closed. Repeated representation switches, the prior zero-price recovery, and
return to the original representation keep the original approval lineage.

`backend/fulfillment_v2_routes.py` calls this proof from the existing unchanged
snapshot revalidation path. The existing owner transaction, revision fence,
cancellation, authoritative-refresh and stock gates remain in place. Successful
proof records an audit on the component lifecycle. No scheduler or background
job was added.

`backend/preparation_piece_operations.py` reports
`assembly_source_reconciliation_pending` when the canonical in-progress order
is awaiting source reconciliation. Missing supplier receipt or mandatory
employee custody keeps its own blocking reason. The source lookup replaces an
existing read; this does not add a query per piece or change Ready eligibility.
The web client recognizes the source-pending reason and keeps its action frozen.

## Verification at this checkpoint

- Local focused backend run: **93 passed, 102 subtests passed**, exit 0.
  Command: pytest with `--noconftest -p no:cacheprovider` for
  `test_assembly_source_pending_message.py`, `test_preparation_piece_operations.py`,
  and `test_component_option_currency_recovery.py`; dotenv/bytecode disabled.
- Independent read-only review found no material blocker in FX binding, exact
  source comparison, native rounding bounds, approval lineage, transaction
  fencing, receipt safety, or query count. The reviewer independently executed
  **35 tests and 102 subtests**, exit 0.
- Web regression: **58 passed, zero skipped**, exit 0. The new diagnostic and
  removal of the misleading unprepared-product prefix were each observed
  failing before the fix. The card and actual page alerts now show only the
  source-pending reason for this code; pressing the frozen action never calls
  the Ready mutation. Existing receipt reasons retain their wording.
- Seven real-Mongo integration tests exercise source refresh, read-only dry run,
  received/unreceived readiness, immutable financial/business state, zero-price
  lineage, repeated transitions, and rejection of real changes or stale gates.
  They are included in `Review Completion Resumable`, with an executed-test
  minimum and the existing no-skip/no-failure evidence gate.
- The local environment could not start a disposable Mongo server. No substitute
  or production database was used for those tests. Their exact-head CI result is
  pending and must not be described as passing yet.

## Remaining work and next safe action

1. Push the checkpoint and open a Draft PR targeting
   `hotfix/prod-snap-meta-final`; record its exact source SHA and CI evidence.
2. Require the seven new integration cases and existing review/assembly suites
   to execute against the workflow's disposable loopback Mongo replica set.
3. Publish the mobile message from the latest verified published mobile base,
   excluding separately held notification changes. Confirm the existing release
   process and its device/build requirements before that publication.
4. After verified deployment, perform a scoped dry run of the
   existing revalidation path against the current revision of the affected
   order. Apply only if the full approved-source proof passes. The complete live
   proof has **not** been executed in this task; an FX-envelope match alone is
   not sufficient to claim the existing order will pass.
5. Preserve supplier-receipt and all other normal Ready requirements. A piece
   without receipt remains blocked even after source reconciliation succeeds.

The repository-wide continuity ledger is Issue #1006. Fresh read on 2026-11-15
still shows 2500 comments and a body last updated on 2026-11-12. This task-specific
status and its new PR are the active continuation records. The saved review
archive preserves the earlier pre-authorization checkpoint.
