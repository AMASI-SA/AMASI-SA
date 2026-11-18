# Direct and operational Ready — 19 November 2026

The owner requests that an unfinished internal operational item or product
directed to assembly can be marked Ready whenever its current Salla order is
in progress. Other Salla statuses block the action. Supplier-product Ready
conditions must remain unchanged. The owner explicitly authorizes publication.

Production base: `80b43ca2f4699fecd895f34a24f77fc997234417`.
Task branch: `codex/operational-ready-status-20261119`.

## Working checkpoint

Search and the transactional virtual-piece writer use the same current order
status predicate. Component/source reconciliation, approval proof, local stage,
payment and stage instructions cannot reject the direct/operational Ready fact.
Tenant scope, actor authorization, revision checks and idempotency are retained.

Direct stock consumption uses the existing service's pre-write deferral contract:
available accepted units are consumed once; missing business prerequisites are
recorded as deferred, and a mutation failure still aborts the owner transaction.
Operational items never consume inventory. Supplier Ready functions are not
modified. Saving Ready does not bypass independent whole-order completion and
shipment requirements; an order-level business hold leaves Ready saved.

Fresh focused local tests: 48 passed, zero skips. The prior implementation
reproduced false `can_mark_ready` for in-progress orders with source or review
holds. A broader local run found one obsolete source-string assertion after
321 passing tests and 24 passing subtests; that assertion was removed because
the new regression suite verifies behavior. Final broader and real-Mongo
verification are pending. Existing direct integration expectations are updated
for the explicitly requested contract; supplier expectations remain intact.

Windows test command (from backend, with dotenv disabled and PYTHONPATH set):
`python -B -m pytest --noconftest -p no:cacheprovider tests/test_operational_item_ready_policy.py -q --tb=short`.

The Windows browser-control tool refused further UI access because it could
not establish the browser URL. In-app browser operations also timed out. No
shared `/app` changes, release lease, merge or publication have occurred.
An existing authorized terminal/project access route is needed for the shared
guard and final publication. Production changed by this task: **no**.

Next: finish exact-source tests, preserve source A remotely, governed Linux
build/frozen intent-only B, exact-B CI, then shared inactive guard, adapter,
prepare/prepublish and one publication. Require a newer visible successful
deployment and the unchanged official three-probe verifier. Do not use a
GitHub merge or a publish-button state as deployment proof. Issue 1006 is at
its comment limit; retain continuation in this task document and task PR.
