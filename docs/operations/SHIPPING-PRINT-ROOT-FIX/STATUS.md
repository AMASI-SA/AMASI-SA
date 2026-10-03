# Shipping Print Root Fix — candidate only

Base: `1eeb12b1db1cd3f74efbe5c7587e4a3d8b34c30f` (prepared Supplier #1250 merge).
Live deployment remains `d0a3ff5458dde6e87a7399493369ab58bef7656a`.
Branch: `codex/shipping-print-root-fix`. No Merge, Prepare, Prepublish or Deploy.
The existing Supplier release/lease is preserved. No Production calls or writes.

## Mechanism

Print previously captured its baseline before its own resync, then rejected the
changed carrier. It also required external shipment validation for internal
delivery. The candidate reconciles once before freezing the baseline, leaves
external timestamp/carrier/superseded/CAS checks unchanged, and selects the
canonical shipment ID instead of an unrelated ready shipment with a larger ID.

Canonical Store Courier routes before shipment lookup/status mutation. Its
document uses the current order, excludes old embedded shipment addresses,
and reads current assignment at an owner-serialized final carrier check.
It has no external shipment/AWB/label requirement. Concurrent carrier changes
and contradictory order-level carrier observations still fail closed.

## Evidence

- Before patch: 6 failed / 2 passed on focused reproductions (4 internal-delivery
  cases incorrectly requested shipments; 2 same-operation carrier reconciliations
  failed the old baseline check).
- Local integrated suite: 190 passed / 1 skipped, Python 3.13, MongoDB 8.0.12
  disposable loopback replica set. The skip is memory-only rollback (mock has no
  transactions); the same injected-after-write rollback test passed on real Mongo.
- New fixtures exercise both issue and refresh, internal delivery without AWB,
  old iMile shipment, current address/assignment, unchanged/reconciled external
  carrier, canonical selection, concurrent replacement/carrier/AWB changes,
  superseded IDs, strict timestamps, repeated/concurrent prints and rollback.
- Owner-reported order/AWB pairs 289131568/6100326425847 and
  290336917/6100226225106 are synthetic fixtures, not captured Production
  snapshots. They cannot prove the exact prior Production rejection branch.
- `git diff --check` passed. Remote CI pending at this checkpoint; no readiness
  claim until Security, CodeQL, Frontend build and backend checks complete.

New CI workflow runs the shipping regression and real-Mongo matrix on exact PR
HEAD. Existing Security workflow/exception, synchronization, Supplier, Accounting,
Mobile and release policy are unchanged. No Mobile contract change is required.

Next: inspect Draft PR CI on exact HEAD; report failures without hiding them.
No release operation is authorized by this candidate checkpoint.
