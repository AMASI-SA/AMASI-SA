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

Fresh focused local tests: 52 passed, zero skips. The prior implementation
reproduced false `can_mark_ready` for in-progress orders with source or review
holds. A broader local run found one obsolete source-string assertion after
321 passing tests and 24 passing subtests; that assertion was removed because
the new regression suite verifies behavior. The corrected broader run passed
322 tests and 24 subtests. Existing direct integration expectations are updated
for the explicitly requested contract; supplier expectations remain intact.

The first remotely recoverable source checkpoint is
`c7aebc86f72c2d9dfc1465000ff32c5916d2919b`, Draft PR 1999. Its governed Linux
candidate build passed, but real G47 CI exposed six failures. The candidate
artifact from that SHA is superseded and must not be published.

The follow-up reads coherent saved provider/root status even when an unrelated
invalid creation date prevents DTO mapping, and still rejects contradictory
or absent status evidence. Direct stock deferral retains the previously
accepted historical-plan consumption contract; the new optional stock-service
argument defaults to the existing strict reviewed-snapshot check for supplier
callers. Their functions and conditions are unchanged. The two historical
virtual-readiness expectations are updated for the owner's status-only rule.
The actual prebuilt/purchased stock and conflicting-status assertions are
retained. Latest local affected suite: 145 passed. Fresh exact-source real-Mongo
and full CI remain required before freezing any new intent.

Canonical follow-up source `3ce1aaba367476d750253662b0b96b1670228ea2`,
tree `0d0aa572e6c169031d307aa76adeddd88a025c83`, passed the governed candidate
build and 329 G47 cases plus 127 subtests. Two remaining G47 assertions expected
an actually supplier-receipted order to remain incomplete after every direct
and operational item became Ready. Existing supplier completion policy allows
that fully covered order to complete; those assertions now preserve that
policy. Stock remains deferred and no supplier Ready condition is changed.
The next checkpoint changes only this test and this status record; runtime
blobs remain identical. Its new source SHA requires a fresh candidate intent.
Latest focused local Ready/supplier consumption run: 87 passed, zero skips.

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
