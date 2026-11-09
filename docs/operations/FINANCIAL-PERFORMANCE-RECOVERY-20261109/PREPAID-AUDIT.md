# Reverted prepaid audit capture: isolated performance evidence

Read-only audit on 2026-11-09; production was not changed. Historical helper:
`de76aed339be775e2878071c92063d0ab9df06df:backend/operational_financial_activity.py`.
That file is unchanged from published PR1817 merge
`6eed626bb8815c54db6d8cb46d45e97902e3db4f` through PR1820 and pre-rollback production.
The current production-based source is `18c245748d22615332d10c674812d4362b88ae71`.

## Confirmed introduced work

The old `operational_balance_store.audit` invokes `capture_after_balances` for
every financial event. `operational_prepaid_shipping.apply_events` invokes this
audit synchronously after each expense/payment pair inside engine reconciliation.
For a prepaid courier payment, the cash projection rejects the courier debt
counterparty (activity lines 213–216). The specialized driver path applies only
to `driver_payment_review_approved`, so prepaid falls through to full `report`
at lines 365–370. The final old source still repeats that report once per charge.
Earlier cash-only and driver-review optimizations do not cover this path.

## Reproduction and fresh results

Command (exit 0):

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 \
  /workspace/scratch/6bdec46b5d67/perf-venv/bin/python \
  scripts/benchmark_financial_prepaid_capture.py
```

The script loads the pinned historical activity helper with Git, calls the
current checked-out report implementation, and uses synthetic data only.
There are 5,000 preceding cash movements and 100 new prepaid charges of SAR34.50.
No COD/provider cohort is present, so the separate current COD index change is
not exercised. This is an isolated capture comparison, not a recreation of all
old dependencies, a complete refresh benchmark, or a production load test.

| Repetition | Capture disabled (s) | Historical capture enabled (s) | Full report calls with capture |
|---|---:|---:|---:|
| 1 | 0.000815 | 8.319187 | 100 |
| 2 | 0.000779 | 7.772907 | 100 |
| 3 | 0.000815 | 8.304617 | 100 |

Median capture-enabled batch: **8.304617 seconds**. Each disabled batch has
zero full report calls. Both branches end with cash **SAR11,550.00** and courier
payable **SAR0.00**. Assertions passed for every prefix's historical cash and
counterparty snapshot and for identical openings, monetary movements, and engine
obligations. The disabled branch is a measurement control, **not a proposed fix**:
restoring daily movements must preserve truthful after-balance evidence.

Initial attempts failed on missing local dependencies (fastapi, then bcrypt,
then cryptography); the completed run uses the shared isolated test environment
after dependency installation, without replacing imports or weakening assertions.

## Relation to preparation responsiveness

`server.py` starts `background_worker_process.ManagedWorker`, a separate child
process. Therefore scheduled reconciliation does not directly execute on the
web request event loop. However `background_worker_runtime.start_tasks` places
operational balances, assembly completion/delivery, order-review resume and
other jobs on that **same child asyncio loop**. A synchronous capture batch can
delay these preparation background jobs and also consumes shared host resources.
Manual financial mutation handlers can also call audit on the web loop.
These code paths demonstrate a plausible concrete mechanism, **not proof that
this workload occurred during the reported production incident**. Live workload
and timing evidence would be needed for that attribution.

## Other introduced read costs and recovery order

- Financial-position GET calls synchronous report/build logic directly; this
  baseline behavior predates the reverted finance changes.
- The reverted `operational_open_cost_exposure.project` lines507–508 add period
  profit collection when the engine timestamp equals the requested snapshot.
  Financial-position GET deliberately passes that matching timestamp, adding
  sales/salary/expense work per read. Default-now cost-exposure reads do not
  necessarily meet that condition.
- Reverted table coverage deepcopies applicable issues per table/currency
  (`operational_financial_table_coverage.table_coverage` lines161–181). Indexed
  response conversion happens after building the report, so wire compression
  does not remove these earlier allocations.

Before restoring the daily-movement audit hook, replace repeated full prepaid
report computation with a scoped parity-tested projection or one rigorously
maintained per-batch financial index. Preserve exact prefix balances, same atomic
write, retries, corrections/refunds, allocations and currency rules. Do not cache
the final batch balance onto earlier movements or remove the snapshot requirement.
The production-based COD-index optimization is a separate first slice; it does
not resolve this currently reverted prepaid hook.
