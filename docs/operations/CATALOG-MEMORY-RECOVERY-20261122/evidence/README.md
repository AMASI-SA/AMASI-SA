# Synthetic catalog payload evidence

This experiment compares actual product-cost builder calls against an isolated
`mongomock_motor` database. It measures BSON-encoded documents returned by
catalog cursors, **not production memory, wire-protocol overhead, or latency**.
The fixture contains 1,000 products, each with a 16,384-byte description and
26,624 bytes of image text unrelated to the selected sale.

## Results

Both subprocesses exited 0. The standard builder, cooperative builder, and
card-only cooperative mode each ran an empty cohort and a one-product cohort.
Full result JSON SHA-256 values match the baseline in all six cases.

| Cohort | Baseline catalog BSON bytes | Patched catalog BSON bytes | Full raw products before / after | Expected cost |
| --- | ---: | ---: | ---: | ---: |
| Empty | 43,203,670 | 0 | 1,000 / 0 | 0 SAR |
| One selected product | 43,203,670 | 181,885 | 1,000 / 1 | 75 SAR |

These values hold for all three builder modes. For the single selected product,
the returned catalog payload is 99.57901% smaller. Its patched reads comprise
138,670 bytes of global alias records and 43,215 bytes for one full product.
The two sold units still cost `2 × 37.50 = 75.00` SAR.

The empty cohort reduces all recorded `find`/`find_one` calls from six to zero.
The one-product cohort adds a second catalog query: total recorded calls go
from 14 to 15 for the standard/cooperative modes, and 12 to 13 for card mode.
This is a payload tradeoff, not evidence that database queries became faster.

## Provenance

- Baseline checkout: `6ad54d81340402232b5f325e09c917eba8920d86` (frozen B).
- Baseline deployment merge: `10ee7b41204dd1d39c8b91daf9f0a9d899041ebb` (M).
- Both have identical `backend/dashboard_v2_routes.py` Git blob:
  `b01ed1a17692081e1a295e4e3ff810750024a6ce`.
- Baseline source SHA-256:
  `1f2b7fa44118ece4a1c82642533d6e249d4ce0aca9a7fa4cb59d27c6e174a3e6`.
- Measured patched source SHA-256:
  `42043c552104bddcbb9dab66d4522eb1d39df0e1b15f6f304881b50bc6911d0d`.

The patch was still uncommitted during measurement. Its exact source hash,
not its base HEAD, identifies the measured implementation. A subsequent source
change requires reassessment or a fresh measurement.

## Reproduction

Use the repository's installed test environment, with `mongomock_motor` and
the backend dependencies. Each invocation uses a fresh Python process so that
imports from the two checkouts cannot contaminate each other:

```bash
python measure_catalog_payload.py --repo /absolute/baseline/checkout --output baseline.json
python measure_catalog_payload.py --repo /absolute/patched/checkout --output patched.json
```

The script inserts only synthetic data into an in-process mock database. Its
builder-facing wrapper rejects write methods and checks that input orders are
unchanged. It asserts the independently calculated zero/75 SAR totals. It
also refuses to save results if the source file changes during measurement.
Compare `result_sha256` for each matching builder/cohort pair across the two
JSON files; `comparison.json` records the executed comparison.

## Limits

The owner-wide lightweight alias scan still grows with catalog size. Whole
variant arrays remain present in that projection to preserve alias precedence;
the fixture contains no variant arrays. Cost-profile, binding, and resource
query scopes remain unchanged, and those companion collections are empty in
this fixture. Production query plans, process RSS, latency, contention,
historical authority edge cases, and actual incident recovery require other
evidence. No live database, deployment, release lease, or financial record was
changed by this experiment.
