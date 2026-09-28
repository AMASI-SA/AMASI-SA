# Accounting public-error boundary candidate — 2026-09-28

## Frozen source inputs

- Production base / rollback: `a7bcb1626e2ad4defba2260d4a113417f91f3120`.
- Source A6: `ede591c149be42a31048c92aae622a510ac9b58f`.
- Frozen B6 / Draft #1179: `3c5aca83816aa32f7bcbcc9f44d61e89d9505135`.
- Code Scanning alerts 73–76: `py/stack-trace-exposure`, open on exact B6 at review.

A7 retains all accepted A6 source and changes only the public exception-response
boundary, its regression tests and this provenance record. A7 is assembled as one
source commit directly above the same Production base. B7 must add only a newly
generated protocol-v5 release intent. B6 and its intent are not modified or reused.

## Security boundary

The receivable and P02 preview handlers returned exception text in reasons.
Their execute/post siblings and the shared tax-policy rejection helper also
returned that text in HTTP 409 details.

The shared accounting_public_errors mapper accepts only one exact plain-string
argument matching the reviewed business-code catalog and returns the catalog's
own literal. All other arguments map to accounting_request_rejected. It never
stringifies, truncates or normalizes exception text. The catalog preserves the
109 literal codes in the direct evidence, receivable, tax and P02 service paths.

Only these three route modules call the new mapper:
- backend/accounting_receivable_routes.py
- backend/accounting_shipping_p02.py
- backend/accounting_shipping_settlements.py

Catch sets, permission checks, HTTP status codes, response envelopes, successful
responses and HTTPException propagation remain unchanged. No exception class,
service calculation, posting/transaction path, fail-closed control, tax policy,
transition policy, G47, Fulfillment or Store Delivery behavior changes.

## Validation contract

PublicAccountingErrorBoundaryTests is added to the existing
test_mz2_receivable_workflow.py suite already executed by mandatory MZ2 CI.
It exercises nine actual ASGI endpoints, known codes and success responses,
unknown/multiline details, absent/multiple/non-string arguments, control
characters, overridden exception stringification, string subclasses and the
existing HTTP 423 barrier. The original source failed the non-disclosure tests.
Focused tests and adjacent pure tax/shipping contract tests passed locally.

Readiness requires fresh exact-B7 MZ2, G47, Fulfillment, Qoyod, Security, CodeQL
Python and JavaScript/TypeScript, frontend build, Release Readiness, Host Node20
clean-clone rehearsal and Store Delivery V1 gates. Mandatory skips do not pass.
CodeQL success alone is insufficient: PR review threads and Code Scanning
instances must prove alerts 73–76 are absent from the new candidate after analysis.
Final immutable identities and evidence belong in the Draft PR and Issue #1006.

No Production/Preview DB access, live control action, release lease, Merge,
Deploy, Publish, opening action or financial activation is authorized.
