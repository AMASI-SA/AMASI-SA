# Qoyod non-COD recovery — WIP

The owner explicitly deferred every COD order until last. All other eligible
payment methods remain in scope, including the previously tested Mada order.
Do not retry COD, bulk-send a mixed payment list, weaken totals/deduplication
checks, or claim completion from an empty ready queue.

Base: f8fae757409b94e84a4994f64297fbfd1f90cb4d.
Branch: fix/qoyod-latest-failure-20260917.

PR1069 product lookup repair is live within PR1071; platform finished Sep17
20:15 UTC, matching runtime health and exact public build metadata. One
subsequent authorized, non-COD single-order retry had matching amounts and
fresh eligible payment facts, but returned provider404. No successful invoice
creation was confirmed. No further send was submitted in this continuation.

Root of stale diagnostics is confirmed: the reader always prefers the original
open quarantine and ignores last_manual_retry_error and newer failed locks.
This change refreshes only sanitized endpoint/status/time evidence from the
latest dated persisted failure. It preserves classification and every send
safeguard. A newer failure without known endpoint hides obsolete evidence;
an unfinished retry or older failure cannot displace prior evidence.

Regression tests first failed in all three reproduction cases. Focused tests
now cover latest retry, newer lock, unknown newer failure, older retry and
in-progress retry, plus existing range/reconciliation/privacy tests. Final
commands and result are recorded in Issue1006.

Current provider404 root remains unknown; diagnostic repair is not an invoice
recovery claim. Browser connection test did not yield a visible result.
Automatic approval review rejected opening Emergent admin at its /chat URL,
interpreting the user's paid-chat prohibition broadly despite existing admin
permission. No bypass or paid chat used. Shared deployment and lease untouched.

Next: review this diagnostic patch/CI, prepare a fresh governed sourceA/intentB
release only through an authorized available admin path, then inspect latest
persisted failure before any new non-COD retry. COD remains deferred. Source
checkpoint alone is not ready to deploy; current tracked intent is unchanged.

Reference-list RCA follow-up: fresh Production refresh at 2026-09-17T21:04:45Z
returned 404 for /product_categories and /product_units, while accounts,
customers, taxes and inventory succeeded. Official https://apidoc.qoyod.com/
collection documents /categories and /product_unit_types, with response roots
categories and product_unit_types. Corrected both GET paths and accepted the
unit response root while retaining legacy response compatibility. A regression
through the client list methods and refresh/cache boundary failed on the old
paths, then passed. Combined reference-list/diagnostics/unsent/failed-retry
suite: 50 passed in 1.14s, exit 0; git diff --check exit 0.

This is a confirmed reference-list defect, not proof of the latest invoice
404 endpoint. No new send, settings change, lease or Production deployment.
The /branches behavior is untouched (existing route documents its absence in
Qoyod v2). Fresh governed A/B release and latest invoice failure diagnosis
remain pending; admin access rejection described above remains unresolved.
