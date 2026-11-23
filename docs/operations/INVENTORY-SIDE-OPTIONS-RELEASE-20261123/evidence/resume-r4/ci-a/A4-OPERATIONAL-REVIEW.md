# Exact A4 Operational Balance Web review

**PASS — run 45284191276, attempt 1, completed/success; all four jobs succeeded.**

- Source: `deeefceffd9a1e37968513ac4f3833314bb415fd`
- Tree: `a7317c4aa62c3d2266937bf60d4f0b2c4f62a27d`
- Parent/base: `28f6d4ca0fc2b7ea7f140432d8e32c768823b6a4`
- PR: #2097

## Counts without inflation

| Evidence | Main tests | Separate subtests |
|---|---:|---:|
| Operational backend | 6,921 | 151 |
| Durable demand read | 106 | Not separately reported |
| Shipping summary/calculator | 16 | 18 |
| Frontend Jest | 1,779 across 111 suites | Not applicable |
| Worker XML | 4 testcase records | Not separately reported |

The backend main XML has 6,921 testcase records, matching the raw pytest main count. The 151 subtests are separate and not added to that figure. No failed/error/skipped testcases were found. The shipping step is independently confirmed successful; its raw result is preserved because the workflow does not retain shipping XML.

## Browser evidence

All 35 scenarios passed: 8 inventory purchase/receiving/option-role journeys, 13 simple-balance journeys, and 14 financial-position journeys. Source manifests were independently verified against Git A4: 61 inventory files, 35 simple-balance files and 23 financial-position files.

The inventory fixture records exactly 2 physically received units, zero payment calls and one option-role write. Desktop/mobile role screenshots and the saved side-options screen were visually inspected: controls and labels fit, the physical size remains required/locked, gift fields become a neutral customer-only summary, and quantity 3 / unit price 79.80 / tax 35.91 remain intact.

## Controlled timing evidence

Loopback Mongo/ASGI benchmark: first useful HTTP 200 in 1384.45 ms; ready home p95 5.31 ms. Both configured targets passed. The 20-page register fixture reused one report/source read instead of 20, a 95% reduction; ready p95 6.73 ms. Financial results match and source data is unchanged. These are isolated CI timings, not Production or browser/network latency.

## Provenance and raw evidence

Six original GitHub artifact ZIPs were downloaded through the connector. Every ZIP's bytes and SHA-256 match its GitHub artifact metadata, and every artifact records this exact run and source. `raw/metadata/artifacts-final.json`, `raw/metadata/run-final.json`, `raw/metadata/jobs-final.json`, `SOURCE-BINDINGS.json`, and `A4-OPERATIONAL-REVIEW.json` retain the bindings. The original artifact ZIPs retain raw XML/JSON, browser evidence, screenshots and source manifests.

Candidate provenance and validation are documented separately in `raw/candidate/independent-candidate-review.json`. This review does not claim B readiness, merge, host rehearsal, or deployment. No source, refs, host or external state was modified.

## Evidence checkpoint

This is evidence for immutable source A4 only. The evidence branch is not deployable. Source B, merge readiness, host adapter rehearsals, and Production deployment require their own checks. Public provenance is in `SOURCE-BINDINGS.json`; this packet contains no caches, dependencies, release build output, signed download URLs or credentials.
