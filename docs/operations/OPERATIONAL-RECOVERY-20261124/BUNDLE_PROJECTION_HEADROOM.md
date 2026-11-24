# Financial refresh: omit unused historical cost expansion

## Scope and evidence

This is a separate, source-only follow-up to production commit
`0ffa962d3cfda3eb49b5750c7bdc2f932a645d4d`. The integration owner retains all
GitHub, production, Release Guard, publication and live recovery responsibility.
This branch neither publishes a release nor changes production data.

The integration owner observed three independent automatic cycles on the same
production version. The first hit the 15-second primary orders-read budget.
The second read all 19,005 orders but reached the 85-second whole-job timeout.
The third completed, with approximately 12.36 seconds in native evidence and
72.665 seconds in the enclosing aggregate stage. The third source collection
took 41.678 seconds, including 12.874 seconds in the remaining-bundle stage.
These nested stage durations must not be added as separate work. Successful
automation is therefore already observed, but its remaining time margin is
small; this change is not presented as a fix for an always-failing worker.

The already available saved report counted 4,135 post-start source orders,
compared with the 19,005 orders in the complete current owner source read.
The exact historical/current bundle proportions are not known. The main order
source must continue to read the entire cohort because old delivery, COD,
frozen payment and physical conflict evidence remain financially relevant.

## Change

`collect_sources` supplies its already parsed operational start to the
remaining-bundle adapter as an optional `cost_started_at` argument. The adapter
still normalizes all raw orders, loads every existing frozen authority row and
builds every historical/current unit, allocation, receipt and invoice witness.
It also retains all cross-order duplicate-allocation and receipt-overuse
checks. Only after those reads and global checks are complete does it omit the
final per-order cost expansion for an order with a provable native creation
instant at or before the start.

Both consumers of those expanded costs in `collect_sources` already exclude
the same older sales: the option catalogue reads only post-start item costs,
and the main source loop finishes its historical delivery/COD branch before
it reaches item-cost expansion. No other consumer opts into the new argument.
In particular, the full-history open-cost exposure retains its prior output.

The cutoff uses the same `instant` parser and native date-field precedence as
the existing main source loop. Missing, malformed, naive textual or otherwise
uncertain dates retain the old projection; future rows also retain it. Native
BSON datetime handling and Riyadh timezone envelopes remain unchanged.
Duplicate native references are still evaluated over all raw rows before
authority lookup. The optional optimization introduces no cache, new source,
write, worker, query, index, scheduling change, read cap or deadline increase.

An earlier proposal to remove historical authority reads was rejected during
review: an older order can share a receipt or allocation identity with a
current order. Omitting that historical witness could conceal a contradiction.
The implemented change expressly preserves those reads and checks.

## Verification

The pre-change focused regression run failed 16 tests because the scoped
projection contract did not exist. The final focused suite passed all 20
tests, including three cases on an owned temporary loopback Mongo replica.
An adjacent ten-suite acceptance run then passed **449 tests**, with **no
failures, errors or skips**, in **75.04 seconds**. The only warnings were two
existing Pydantic deprecations in an unrelated recurring-obligation module.

The new real-Mongo cases verify an ordinary historical/current cohort, a
duplicate allocation identity shared across the cutoff, and a shared receipt
whose combined allocation exceeds its quantity. In each case, the old and
scoped paths perform identical query multisets; the current cost outputs,
complete collected financial sources, reconciled state, report and financial
position match. Reconciliation replay is idempotent, all native database
collections are unchanged, and the input financial state is unchanged.

Other cases cover exact-boundary timestamps, timezone envelopes, native BSON
datetimes, malformed/unknown/future dates, old/current duplicate references,
malformed historical item structures, historical witness failures even when
no current cost is required, and cancellation before final projection.

The focused suite can create and clean its own isolated replica using the
repository-owned fixture:

```bash
env -u OPERATIONAL_TEST_MONGO_URI -u MZ2_TEST_MONGO_URI -u DEMAND_READ_TEST_MONGO_URI \
  PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
  DEMAND_READ_TEST_MONGOD=/absolute/path/to/mongod \
  python -B -m pytest --noconftest -p no:cacheprovider -q --tb=short --asyncio-mode=auto \
  backend/tests/test_operational_balance_bundle_cost_period.py
```

The adjacent gate uses the same owned fixture, explicitly passes only its
fresh loopback URI into `OPERATIONAL_TEST_MONGO_URI`, `MZ2_TEST_MONGO_URI` and
`DEMAND_READ_TEST_MONGO_URI`, and runs these suites with the same pytest flags:

- `test_operational_balance_bundle_cost_period.py`
- `test_operational_balance_sources.py`
- `test_operational_balance_remaining_products.py`
- `test_operational_balance_bundle_legacy_receipts.py`
- `test_operational_balance_bundle_piece_index.py`
- `test_operational_balance_mapping_combined.py`
- `test_operational_balance_cost_read_scope.py`
- `test_operational_balance_order_source_projection.py`
- `test_operational_open_cost_exposure.py`
- `test_operational_bundle_cost_projection.py`

An independent reviewer passed the same 20 cases in 4.94 seconds, including
all three actual Mongo cases, and verified the frozen hashes before and after.
Their static review found no blocking issue in the consumer boundary, complete
historical witness retention, financial parity or cancellation behavior.

The new suite is included by the existing Operational Balance Web workflow's
`test_operational_balance*.py` selection. The test runner deliberately uses
`--noconftest` and disables dotenv; no production/shared Mongo URI was used.

The first combined Stock eligibility run exposed a fixture-environment mismatch:
that gate supplies its owned replica through `OPERATIONAL_TEST_MONGO_URI` and
`MZ2_TEST_MONGO_URI`, whereas the generic demand-read fixture recognizes its own
`DEMAND_READ_TEST_*` variables. Its three new Mongo cases were skipped, so the
existing no-skips gate correctly rejected the run. A module-local adapter now
accepts the established operational/MZ2 capability only after validating one
consistent, explicit loopback URI without credentials or a database path.
Local standalone runs still delegate to the original owned-replica fixture.
With the same environment-variable set as Stock eligibility, a newly owned
loopback replica and both `DEMAND_READ_TEST_*` variables absent, all **20 tests
passed in 3.07 seconds**, including those three Mongo cases, with no skips.
No workflow assertion or application runtime was changed for this correction.

## Controlled performance result

The final-code synthetic benchmark uses 400 frozen financial bundle orders
and 1,600 preparation pieces, of which 87 orders require current-period costs.
After one warm pair, it alternates the full and scoped paths for three measured
pairs. Every current cost row is identical and the input orders/pieces remain
unchanged. All shared witness reads remain in both paths.

| Path | Measured durations, milliseconds | Median |
| --- | --- | --- |
| All final cost projections | 441.812 / 422.144 / 529.799 | 441.812 ms |
| Current-period final cost projections | 264.381 / 276.435 / 298.689 | 276.435 ms |

The median is 37.4% lower in this synthetic mock-Mongo scenario. This is a
controlled CPU-work result, not a production latency claim. It does not
measure the production distribution of bundle types, native I/O or host load.
The release owner must still verify the exact published identity and complete
independent automatic cycles under the unchanged 85-second job deadline.

## Frozen runtime files

- `backend/operational_remaining_bundle_sources.py` SHA-256:
  `eb57d600c4432bf07e4901aaddb8d06f8ff1fcdc7da379e3c4fb9911ae662ee3`
- `backend/operational_balance_sources.py` SHA-256:
  `357f7343b3a7665603f68665f2fffc2263f4f7449b4185eb0d7a3bf09f9674d5`
- `backend/tests/test_operational_balance_bundle_cost_period.py` SHA-256:
  `f99e85a45658cb4daf1da6414352694280847bfb3feb915e1525e1ba2beec20f`

Independent review is complete. Integration and production verification remain
with the integration owner; local tests alone are not publication evidence.
