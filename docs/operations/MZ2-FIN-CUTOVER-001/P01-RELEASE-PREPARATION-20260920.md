# P01 release preparation — 2026-09-20

PR #1099 resumes from remote source `e257c963a986feb6e0bb057361e35909f5b13df6`.
Production comparison base remains `635e181f7520354029efbdc8e89d76a94de77ff2`.
Both remote refs were read again before editing. The latest relevant Issue #1006
checkpoint is https://github.com/AMASI-SA/AMASI-SA/issues/1006#issuecomment-5744962276.
Synthetic Preview acceptance is retained; no browser or financial acceptance
scenario is repeated. P01 remains IN_PROGRESS and P02 remains LOCKED.

## Fixture repair

The original focused test reproduced AttributeError for missing
`_Db.settlement_entries`. The fixture now supplies a scoped statement row with
80 full + 20 partial refund and its 100 approved daily-refund link. The real
`refund_review_reasons` executes unchanged. Missing and insufficient links
return HTTP 409 `refund_reconciliation_required` before journal posting.
Bank snapshot, idempotency metadata and balanced settlement assertions remain.
No application code or recovery/transaction/refund guard changed.

The required backend contract files passed locally: 51 tests, exit 0. Local
Windows invocation uses `--noconftest` because the unrelated global Excel
fixture hardcodes `/tmp/salla_test.xlsx`; normal Linux CI must also pass with
the repository conftest enabled. This local run is not final package proof.
Final source CI and the intent-only B clean-clone adapter rehearsal are pending
at this source checkpoint; their exact SHAs and results belong in the final
handoff, without changing source A after freezing its intent.

## Write-stop review

There is no independently implemented owner write-pause flag in the inspected
Mezan 2 accounting paths. `managed_owner` selects Mezan 2 if either the cutover
operation marker or an owner tax policy exists. BNPL sale/refund ingress uses
this decision before the old bridge. Removing both markers can therefore
reactivate legacy posting. Removing just the cutoff can instead fail managed
recognition; it is not a supported pause mechanism. Keep policy, effective date,
cutover timestamp and operation marker unchanged.

Revoking an accountant permission or hiding UI controls is insufficient:
webhook/sync sale recognition also calls the service without that UI boundary.
`atomic_owner` provides transaction serialization, not an operational stop.
Blocking public HTTP alone does not stop already-running workers or in-flight
transactions. A database backup is also not a write stop.

Until a dedicated pause mechanism is separately implemented and verified, the
available operational plan requires a coordinated maintenance stop of **all**
application replicas and background writers using the affected database,
including webhook consumers, scheduled synchronization and bridge catch-up.
Block new mutating ingress, pause queues/schedulers, drain active work and
transactions, then stop all remaining writer processes. Keep the reviewed
source and all financial/configuration records intact. Verify process/replica
inventory and stable event/journal state after the drain before calling this a
confirmed stop. Unknown writer coverage means the stop is unverified and blocks
activation. Preserve incoming provider deliveries for controlled replay under
the same reviewed code, identities and idempotency rules after recovery.

This maintenance procedure has NOT been executed or proven on Production.
Provider retry/retention behavior and platform controls must be checked for
the actual deployment before relying on it. If read availability or uninterrupted
ingestion is required, a separately scoped fail-closed pause at the transactional
write boundary is needed, including concurrency/drain and no-legacy-fallback
tests. Do not represent such a flag as shipped by this fixture-only change.

After any financial write, prefer a forward fix preserving event identities;
do not restore older code that can resume automatic refunds without a reviewed
compatibility plan. No automatic reversal, deletion or replay is authorized.

## Remaining release gates

1. Final A checks/build and reviewed intent-only B; clean-clone package rehearsal.
2. Actual Production replica-set/session/transaction capability, indexes and
   restorable backup evidence; no assumptions from Preview.
3. Verified maintenance/write-stop controls and drain plan, with no policy or
   cutoff deletion and no legacy bridge fallback.
4. Explicit owner approval of real tax/effective date/cutoff/activation/permissions.
5. Separate authorization for merge and deployment, then current release guard,
   explicit Deployment Succeeded and exact identity/hash verification.

No Production merge, publication, configuration or financial write in this task.
