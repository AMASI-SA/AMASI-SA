# Ready rollback preparation — not a deployed or release-ready rollback

Current integration: c77a614ac762d48f50885d60e8b6b005dd2789dc.
Known deployed baseline: 29ccdb81af9eb51ca2670b1bca95afa4666fb9c7.
Baseline tree: ad21eff70fbcfd48c7b2f18ca5514bfcf6bf4106.
Remote retained reference: codex/ready-rollback-baseline-20261017.
Isolated inverse source commit: d9945b7d961dfebca27fda5e08b3515ca922edbb,
branch codex/ready-rollback-candidate-20261017. Its full tree exactly equals
the baseline, preserving payroll, Google periodic and dashboard changes.
The inherited intent is NOT valid for this new descendant source commit;
it must be rebuilt and frozen as a fresh v5 A/B release before use.
Baseline whitespace is retained intentionally; git diff --check reports three
pre-existing whitespace lines restored from baseline, not a semantic mismatch.

## Data boundary
No database migration, record deletion, compensation or inventory reset is part
of the rollback. A successful Ready remains committed. Never replay Ready POSTs
because deployment or rollback was interrupted. Persisted dispatch markers and
leases must survive. New worker cancellation/restart acceptance exercises
expired-lease recovery and completed GET without a duplicate POST.

The old baseline does not run the new version-2 outbox worker, so pending
completions can stop progressing after a full source rollback. It also lacks
the new canonical writer fence. Therefore a full rollback must not be presented
as safe uninterrupted Ready operation: suspend Ready use during the recovery
window, retain all pending records and reconcile provider outcomes before
restoring normal operation. Prefer a narrowly reviewed forward repair when
possible, retaining canonical fencing and durable readback.

## Execution gates (not executed)
1. Inspect the fault and current release lease; never clear another owner lease.
2. Fetch current Production and ensure the rollback only undoes this release.
   If Production advanced, rebuild the inverse against that head; do not rewind
   unrelated releases or force-push Production.
3. Build and test the rollback source, freeze fresh intent-only B and preserve A/B.
4. Review and merge the rollback through the normal source-of-truth branch.
5. In /app check inactive lease, clean tracked files and exact fresh origin HEAD;
   preserve env/untracked files and rehearse the governed adapter.
6. Run prepare and prepublish, publish once, require explicit deployment success
   and verify three consecutive probes, then inactive lease.
7. Audit retained pending operation counts/statuses without exposing customer
   data; resume only through readback/recovery, never inventory compensation or
   blind replay of Ready.

No Production write, release lease, intent freeze or rollback deployment was
performed to create these source references. Full rollback runtime rehearsal
and Android compatibility remain release prerequisites.
