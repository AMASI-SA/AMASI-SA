# Ready rollback preparation — not a deployed or release-ready rollback

Current integration: c77a614ac762d48f50885d60e8b6b005dd2789dc.
Known deployed baseline: 29ccdb81af9eb51ca2670b1bca95afa4666fb9c7.
Baseline tree: ad21eff70fbcfd48c7b2f18ca5514bfcf6bf4106.
Remote retained reference: codex/ready-rollback-baseline-20261017.
Isolated inverse source commit: d9945b7d961dfebca27fda5e08b3515ca922edbb,
branch codex/ready-rollback-candidate-20261017. Its full tree exactly equals
the baseline, preserving payroll, Google periodic and dashboard changes.
The exact old tree can validate as the old reviewed identity, but it is not a
new rollback release relative to the proposed Ready deployment. Initial CI
39060118127 refused its candidate-base input because it classified as reviewed
B. This is a failed rehearsal, not evidence of deployability.

The prospective deployable rollback is now codex/ready-rollback-release-20261017,
source 3cb433e64a8a7d9563f20642e5f46749816aa4e0, based on frozen Ready B
ff442eb34a91fa618d10d0b3e1484e0be1e3a313. It restores baseline source bytes
while retaining the new predecessor intent, allowing a fresh rollback intent
to be built under the unchanged release protocol. Its rehearsal is pending.
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


Reviewed rollback B: e6e161194d5ae5d27a623c91f652612e5e30a086, tree
50e5bd2bc5a70fd4747e8dd537fd36b17cb34e8f. Workflow39060790175 passed
Backend preflight, reproducible Frontend build and Linux clean-clone adapter
and runtime JSON proof. This is a tested package contingency, not proof that
old code continues processing new outboxes. No rollback was activated.
Owner instruction relayed by Android task: keep the working update; do not
restore it. Contingency branches are not scheduled for merge/deployment.
