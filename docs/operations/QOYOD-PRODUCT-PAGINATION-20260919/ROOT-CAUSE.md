# Product catalog end-of-list failure

## Evidence

The deployed source was verified at production merge
`1f2923f19ba503017a04677636d75caad8e4df91`. A read-only inspection of the
production manual-send lock through Emergent's MongoDB Viewer found
`GET /products`, HTTP 404, plain-text `We found nothing`, with no request body.
The stored failure finished at 2026-09-19T10:26:48.507000. No financial action
was performed during this investigation.

This is different from the route-level `URL not found` response investigated
in the earlier base-URL change. The deployed client already accepts the exact
empty sentinel on scan page 1, but rethrows it on later pages. Consequently
the saved sentinel must have escaped a later scan page; the exact page and
number of preceding rows were not recorded and are not claimed as observed.

Preview and production use different credential fingerprints. Tests with the
Preview credential cannot establish the Production tenant's behavior.

## Reproduction and correction

A synthetic HTTP response sequence reproduces the failure without contacting
Qoyod: filtered lookup returns 404; a catalog page returns 50 unique products;
the next page returns the observed plain-text sentinel. Previously the client
raised even when the requested SKU was already among the retrieved products.

The client now recognizes the exact empty-page sentinel at the end of the
scan and returns the accumulated SKU index. It never discards the preceding
products or turns a known match into absence. If metadata declares additional
unseen rows, the sentinel still fails closed. Generic 404s, unknown shapes,
repeated IDs/pages, scan caps, authentication, throttling, and network errors
remain failures. Sentinel matching was narrowed from a substring search to
the exact plain-text message or the existing single-error object form.

No invoice, payment, COD, amount-parity, or idempotency behavior was changed.

## Verification

- Before correction: 5 failing new regression cases, 31 passing cases.
- After correction: 79 focused tests passed (product lookup, client failure
  boundaries, base URL normalization).
- Broader CI/manual selection: 180 passed; 4 existing manual-send tests lacked
  the synthetic base URL in the local harness. After providing that URL and an
  ephemeral encryption key, all 43 manual-send tests passed. Earlier harness
  attempts also required backend working directory and installed CI dependencies;
  those were environment failures, not suppressed application failures.
- `git diff --check` passed.

Production remains unchanged. Live acceptance is unverified until a reviewed
governed release is deployed and verified. Do not replay the financial canary
as a diagnostic probe or release any COD backlog.
