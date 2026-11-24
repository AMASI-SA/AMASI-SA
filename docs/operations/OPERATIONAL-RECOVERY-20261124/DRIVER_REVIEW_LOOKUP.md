# Request-local driver review effect lookup

## Reproduced cost

`operational_driver_review_report.validated_review_facts` previously searched the
entire movement list separately for every sealed bank-transfer review. The
financial-position builder invokes that validation for both delivery coverage
and destination linkage, including the employees view. With `R` bank reviews
and `M` movements, these two searches required `2 × R × M` comparisons.

A synthetic snapshot with 10,000 normal movements and 500 valid, independently
sealed bank-transfer reviews produced 1,010 movement-list passes while building
the employees report. There were 10 passes with no reviews. With 1,000 reviews,
there were 2,010 passes. Every review's saved effect was validated, and the input
snapshot remained byte-identical. The source fixture creates no database client
and reads or writes no real financial data.

## Change and retained contracts

Only `operational_driver_review_report.py` changes at runtime. The validator
first performs its existing seal/owner/amount checks, then indexes the requested
string movement IDs in one local pass. Each requested ID stores either its one
row or `(2, None)` for a duplicate, so duplicate history does not grow a list.
Unrequested movement IDs are not retained. This reduces the validated bank
effect lookup to `O(M + R)` per invocation, with storage bounded by the requested
valid review IDs. The index is discarded after that invocation.

The original equality scan remains the fallback for nonstring identities or a
non-list/tuple movement source. A nonstring saved movement ID disables indexing
for that invocation. This preserves old behavior for missing, null, boolean,
numeric, list and mapping identities, including Python's `True == 1` duplicate
semantics. Duplicate matching effects continue to invalidate the review even
when one duplicate is correct. The existing seal, monetary effect, ownership,
order-ambiguity checks, result ordering and issue messages are unchanged.

There are no authorization, source acquisition, financial date, money mutation,
schema, timeout, scheduling or cross-request cache changes. The POS allocation
loop in destination linkage is unchanged.

## Unprofiled paired measurements

Each comparison uses the identical saved snapshot, alternates the indexed path
and the original linear fallback, and takes three unprofiled samples per path.
The fixture contains 100 COD claims, 10,000 normal movements and the indicated
number of additional bank review movements. The employees JSON was identical
between paths, and the BSON input hash was unchanged after all reads.

| Sealed bank reviews | Total movements | Linear samples, ms | Indexed samples, ms | Linear median, ms | Indexed median, ms | Movement passes, linear → indexed |
|---:|---:|---|---|---:|---:|---:|
| 500 | 10,500 | 761.195 / 573.295 / 514.146 | 191.919 / 234.193 / 189.654 | 573.295 | 191.919 | 1,010 → 12 |
| 1,000 | 11,000 | 921.027 / 910.240 / 932.830 | 213.085 / 243.792 / 254.278 | 921.027 | 243.792 | 2,010 → 12 |

The observed median speedups were 2.99× and 3.78×. These are local synthetic
measurements, **not production page-load timings**. Production review counts and
per-function timings are still required to quantify this path's contribution
to the observed production CPU time. The smaller initial COD-only benchmark
contained no saved bank reviews and therefore could not expose this cost.

## Verification

The fail-first scaling test observed `[5, 50]` full history passes for 5 and 50
reviews before the change; it now observes `[1, 1]`. Additional tests cover
duplicate effects, bounded index retention, malformed identity parity, invalid
seals and POS-only input, revalidation after an effect changes, and exact UTF-8
JSON parity for all 12 financial-position views. The parity reference disables
only the index, retaining the original equality lookup and all existing proof
checks.

Focused command (with `PYTHONPATH=.:backend:backend/tests` and dotenv disabled):

```sh
python -B -m pytest --noconftest -p no:cacheprovider -q \
  backend/tests/test_operational_balance_driver_review_performance.py \
  backend/tests/test_operational_balance_driver_review_report.py \
  backend/tests/test_operational_balance_delivery_report_coverage.py \
  backend/tests/test_operational_balance_activity_driver_projection.py
```

Result: **104 passed in 4.57 seconds**. No Mongo or production environment is
needed for this pure report test set. Existing real-Mongo CI gates remain
unchanged.
