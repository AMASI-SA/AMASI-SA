# Supplier receipt Ready policy

## Contract

The owner explicitly removed component/source-approval prerequisites from Ready
for a physical product already received from its supplier while the current
Salla order is in progress. This supersedes the former business requirement
that matching component revisions and approved source hashes gate this path.

Current status evidence, tenant/actor authorization, actual receipt, required
services, configured employee custody, explicit holds/instructions and current
whole-order product/unit coverage remain independent requirements. Other
direct/virtual Ready paths retain their existing policy.

## Source checkpoint

- Repository: `AMASI-SA/AMASI-SA`.
- Task branch: `codex/ready-supplier-receipt-policy-20261118`.
- Initial production source: `d7c9ed77137bed0948f4d32a34c6bf571d75e6f8`.
- The local checkout is isolated from concurrent mobile printing work.
- **WIP — not release-ready.** No release intent, merge, live data change,
  release lease or production publication has occurred.

## Implementation

- One supplier receipt predicate drives search and the actual Ready transaction.
- A saved receipt basis supports complete current product coverage without
  depending on approval/component acceptance. Missing/extra/duplicate units and
  incomplete mixed-order work cannot complete an order.
- The same proven basis is retained through the existing delivery outbox,
  status display and shipping invalidation. Provider identity, status,
  shipping/material evidence, claim ownership and effect journaling remain.
- Healthy component consumption must remain correct. Unavailable component
  evidence may defer only during strictly read-only validation, before any
  inventory write; the Ready record/event records that inventory outcome.
  Mutation failures cannot be swallowed. No new background worker is added.
- Source flags, supplier receipts and approved snapshots are not rewritten to
  manufacture eligibility.

## Verification and remaining work

- Initial remote checkpoint preserves the independently reviewed inventory
  preflight/deferral service and its regression tests. The remaining supplier
  Ready integration is still in progress in the dedicated checkout.
- Stock-focused verification: 27 tests and 7 subtests passed, zero skips. Tests
  include all-location validation before writes, healthy consumption/retry,
  and propagation of mutation failures after the first write. They replace
  only the owner boundary and do not claim real-Mongo rollback evidence.

- The first search regression was RED on the prior code: four failures for
  received supplier pieces under local source flags, eight retained negatives
  passed. The revised search suite then passed twelve tests.
- The isolated test interpreter uses the exact direct pins from the existing
  Review Completion Resumable CI workflow. No application dependencies were
  changed.
- Local MongoDB 8.0.12 cannot start: its allocator fails before server startup
  because `/sys/devices/system/cpu/possible` is unavailable; Docker is absent.
  Real transaction and API race acceptance must execute in the existing CI
  disposable replica set. Skips are not acceptance.
- Independent review identified healthy-stock preservation, legacy read-only
  outbox compatibility, and claimed-vs-current source fingerprint fencing.
  These are being implemented and retested before an acceptance claim.
- Next: finish those corrections, run the combined focused suites, preserve
  the exact source SHA, execute the real-Mongo CI gate, independently review
  the final diff, and report release readiness separately from deployment.

Issue #1006 is full. This task document and its draft PR are the continuation
record. Keep any source checkpoint explicitly WIP until the required evidence
is complete. No customer payload or credentials belong in this record.
