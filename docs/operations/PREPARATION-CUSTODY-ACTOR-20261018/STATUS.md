# Preparation receiving custody actor

## Scope and status

Task branch: `codex/preparation-custody-actor-20261018`.
Base: `origin/hotfix/prod-snap-meta-final` at
`e015b913ce950d6e5cf52cc0f1893da94b7b30b4` (fresh fetch on 2026-10-18).
Isolated worktree: `D:/amasi-build/backend-preparation-custody-20261018`.

The owner reports only the owner's preparation card appearing in Android's
preparation receiving custody, while pieces received from other preparers are
missing before assembly. No live example order is currently available. Do not
claim a specific production piece was inspected or repaired.

## Confirmed mechanism and change

The native request adapter validates the employee's live page grant, then uses
a merchant-shaped principal while preserving the authenticated employee in
server-owned `_mobile_actor_*` fields. The receipt POST already persists
`effective_operation_actor(user, context).id`. The custody GET instead queried
`context.actor_id`, which is the owner under that native adapter. A receiving
employee therefore saw the owner's receipts and could not see their own.

The custody GET now resolves the same effective receiver as the receipt POST.
Source-preparer grouping is unchanged and includes both owner and non-owner
preparers. Tenant scope, own-receiver scope, permission checks, active custody
filters, source-employee snapshots and receipt idempotency remain unchanged.
There is no history rewrite, data migration, Android change or provider call.

## Evidence

Real loopback Mongo 8.0.12, disposable synthetic database per test, actual ASGI
receipt/custody routes and actual native permission/tenant adapter. External
HTTP is forbidden by the fixture. Only the authenticated-user boundary is
supplied by the test; database queries and identity mapping are not mocked.

Before the product fix, the six new route tests produced **3 failed, 3 passed,
2 subtests passed** in 7.38s (exit 1). Failures directly showed owner identity
returned instead of the receiving employee, owner's unrelated piece returned,
and a successfully received employee piece absent from custody.

After the fix, the affected suite produced **65 passed, 2 subtests passed** in
9.24s (exit 0), zero skipped/error/failure cases in JUnit:

```text
python -m pytest --noconftest -p no:cacheprovider -q --tb=short
  backend/tests/test_preparation_receiving_custody.py
  backend/tests/test_preparation_piece_operations.py
  backend/tests/test_mobile_app_request_context.py --asyncio-mode=auto
```

Tests cover receipts from the owner and two other preparers, owner compatibility,
receiver and merchant isolation, exclusion of assembled/cancelled/archived
pieces, repeated read without data mutation, receipt retry without duplicate
piece/event, live page-grant denial, and browser compatibility. The new suite is
registered in Review Completion Resumable CI with a minimum-six-case gate.
The permission-denial subtests independently restore the valid grant before
revocation. The final rerun also passed **65 tests and 2 subtests in 9.58s**
(exit 0). `git diff --check` passed. No skipped tests were accepted.

## Release and remaining work

Production source/deployment changed: **no**. Production business writes: **no**.
No release lease, merge, APK build, OTA or live receipt was performed.
Hidden-customer-option diagnostics remain a separate unresolved task and are
not included. Existing shipping fixes are preserved by using the current
production base.

Next: final focused rerun/diff review, push this task checkpoint, open a review
PR and inspect CI. Coordinate any authorized release with the current release
owner, then follow the complete v5 source/intent/guard protocol. This is a
backend read fix; it does not require replacing the Android APK. A production
readback with a real receiving employee remains unverified.
