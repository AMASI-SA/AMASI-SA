# Qoyod empty product-list recovery — 2026-09-18

## Production symptom

After the canonical Qoyod endpoint release was verified, the single non-COD
canary order `286150959` still failed at `GET /products` with HTTP 404. No
invoice ID was persisted and the order was not retried.

## Root mechanism

Qoyod uses HTTP 404 with the exact provider message `We found nothing` for an
empty list on some tenants. The manual-send product resolver correctly refused
generic 404 responses, but it also refused this known empty-list sentinel on
the first unfiltered product-catalog page. That prevented the guarded product
creation step from running for a genuinely empty catalog.

## Bounded correction

The product-catalog scan now treats only the exact, case-insensitive
`We found nothing` sentinel on page 1 as a confirmed empty catalog. Generic
404 responses, `URL not found`, HTML 404 pages, authentication failures,
network failures, and later-page failures remain fail-closed.

This does not change duplicate checks, idempotency keys, totals/payment guards,
COD handling, or automatic retry policy.

## Verification contract

- The exact empty-list sentinel returns an empty catalog and permits the
  existing guarded product-create path.
- Route-level and ambiguous 404 variants remain blocked.
- The focused product lookup tests and the neighboring Plan-B manual/automatic
  send safety suites must pass before release preparation.
- Production acceptance still requires one normal retry of the same non-COD
  canary, exactly one Qoyod invoice, and no duplicate. COD remains deferred.
