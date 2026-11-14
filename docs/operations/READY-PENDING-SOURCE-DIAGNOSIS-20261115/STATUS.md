# READY-PENDING-SOURCE-DIAGNOSIS-20261115

Status: partial diagnosis; no runtime fix; publication HOLD.

## Scope and authority

Investigate why supplier-received pieces remain locked in assembly while the order is in progress. The user explicitly states that inventory is inactive and component-stock shortage must not prevent product preparation. Keep the earlier notification batch and publication hold intact. The cancelled barcode-priority request remains excluded.

This is a documentation-only checkpoint. It is not a deployment candidate or permission to alter live data.

## Source identity

Initial inspected production lineage: `324dab9fd01fab17a28a5f7de4bb2bd298d7aea9`.
Fresh production branch read: `7ef45017e5221635ce3c80c3788f73d9c50b1e08`, tree `e55cdf2c2b15cfe08efe3e0305d043e87f916d8b`.
The intervening PR1932 changes campaign operating-period files and release intent only. The assembly/component/review paths inspected here are unchanged. No production ref or shared source was changed by this investigation.

The old reviewed-physical Ready fix was carried through merged PR1912. The present case reaches a different in-progress source-reconciliation gate.

## Observed evidence (sanitized)

An owner-scoped ordinary assembly API response, also supplied by the user as an attachment, shows five pieces: two received physical pieces with zero remaining services and optional employee receipt; a third physical piece still requiring supplier receipt; and two operational packaging pieces. All five receive `can_mark_ready=false` and `assembly_order_not_ready`. Visible source and workflow stages are in progress.

Normal owner-scoped source reads show `g47_salla_snapshot.component_pending=true`. The completed review operation uses `mezan_local_v1`. The saved fulfillment decision has no blockers and all three physical lines use `finished_goods_inventory_not_tracked`, `requires_branch_inventory=false`, and `sell_without_finished_goods_inventory=true`.

These observations do not expose the G47 cutoff, any existing component plan, the accepted lifecycle fingerprint, or the exact revalidation failure. The provider source timestamp does not date the beginning of the pending state.

Recent runtime UI logs contained no matching target/component event. No broad webhook monitor scan was attempted.

## Confirmed mechanism

- `assembly_status_policy.canonical_status` returns no eligible status while `component_pending` is set, even when raw/provider and saved source statuses consistently say in progress.
- `_assembly_search` translates this into the generic order-not-ready blocker for every piece.
- `allow_legacy_component_execution` independently rejects pending source evidence before Ready writes. This is not a quantity-shortage calculation.
- No G47 cutoff plus no existing plan selects the legacy no-stock cohort, but does not exempt it from current-source acceptance.
- Local review completion atomically asserts lifecycle acceptance. A later canonical source persistence can set pending again.
- Source persistence invokes narrow unchanged-contract revalidation. Full reconciliation is not run by ordinary mobile refresh, which can report top-level success while nested component acceptance remains deferred.
- No dedicated pending-component retry worker was found; already completed local reviews are not resumed by the retained legacy review worker.

Do not blindly clear or ignore the flag: it also fences actual quantity/options/eligibility/cancellation changes.

## Verification

Root directly executed the actual cohort, canonical status and legacy execution functions against a read-only synthetic DB boundary, with no configuration or plan. Exit 0 and assertions passed: legacy cohort true; pending status null; source-pending helper true; execution blocker `component_execution_blocked`; removing only the synthetic flag makes the control status in progress. No DB connection or writes occurred.

An independent worker reproduced actual persist/revalidate/reconcile logic with an in-memory Mongo double. An unchanged same-timestamp replay advanced revision and cleared pending. Changing only a fixed-choice label with identical IDs/quantity/price/currency left pending; ordinary non-strict reconciliation returned stale_ignored; repeated refreshes stayed pending. This is a possible mechanism, not the diagnosed cause of the live case. Transaction/concurrency semantics were not tested by that in-memory reproduction.

Root attempted three existing integration selections in `test_g47_operational_boundary.py` and `test_salla_sync_component_snapshot.py`: exit 0, **3 skipped, 38 deselected**. The required real Mongo fixture was unavailable; these are not passing integration evidence. No source patch was made.

## Access boundary and discarded evidence

Automatic approval review rejected opening the production Mongo Database Manager because that would grant broad private operational/customer-data access beyond diagnosis of one order. The rejected action was not retried, and no connection-secret or alternate database route was used.

A prior existing diagnostic script executed in the code-server environment returned deferred, but a scoped existence read showed that environment did not contain the target owner/order. That dry-run result is **not production evidence** and must not be used to explain the live pending state.

The normal app projections do not expose the exact lifecycle/review comparison. `GET review detail?local_only=true` is not strictly read-only because it applies product defaults; it was not used as a diagnostic workaround.

## Concrete next scoped read

After any required explicit approval for opening the DB manager, read only the reported owner's single order and that owner's G47 setting. Retain only these diagnostic fields/comparisons; do not export raw customer/options text, credentials or unrelated orders:

1. Canonical source watermark: revision, pending, authoritative-refresh, cancellation and provider version.
2. Matching component lifecycle: state, accepted, retry_required, error_code, generation, snapshot_revision, source version; compare fingerprint equality without exporting raw payload.
3. Owner G47 cutoff validity/presence and existence of a plan for this exact order.
4. If a plan exists: state, accepted/reconciliation-required and generation/version alignment.
5. Matching completed local review: valid operation/workflow linkage and immutable business-contract comparison. Report changed field categories only, not customization values.

This read should distinguish prior intake failure, stale same-version fingerprint, wrong cohort/plan alignment or actual approved-contract change. It authorizes no update, retry, refresh, reserve, consume, release, supplier receipt, Ready click, deploy or build.

The fix must preserve the third piece's missing-supplier-receipt blocker, per-piece services, holds/instructions, current status, cancellation, quantities and product-option identity. Inventory shortage is not to become a preparation gate. A specific pending-source explanation should replace misleading generic order-stage wording.

## Continuity

Repository: AMASI-SA/AMASI-SA.
Task branch: `codex/ready-pending-source-diagnosis-20261115`.
Changed file: this status document only.
PR/merge/deploy: none. Production changed by this task: **no**.
Exact checkpoint SHA is recorded by the branch ref; no self-referential SHA is embedded.

Existing notification candidates remain unchanged: backend PR1931 at `ec13c2c6a495b36cf5f70795428267d567634f86`; mobile PR356 at `4763d3a2d627156c3aaad70ec0b237f7af44047d`. They remain held.

Issue1006 readback exposes an oversized/stale body ending in a preceding release handoff, so it is not authoritative for this new checkpoint and was not overwritten or truncated. Resume from this dedicated branch, re-read the current production ref and applicable instructions, and preserve all unrelated work.
