# Financial performance recovery after rollback

## R2 isolated integration checkpoint

The original PR1841/source64ee370 remains preserved. This R2 branch transplants
only that six-file patch onto production merge
`cedd7158ba1a7fbae13d1503a503c61b19ad7ae6` (payment-display PR1842).
No courier or payment-display code is changed. The previous candidate intent
must not be reused. Courier PR1843 supersedes1840 and retains next release order.
The latest shared ledger reports1842's active lease; this task performs no /app
mutation, rehearsal, lease or deployment. R2 tests and a new source checkpoint
are required; rebuild on production again if1843 advances it before release.
Historical findings and measurements below apply to the original source unless
explicitly marked R2.

R2: all six financial suites rerun, **72 passed in2.75s / exit0**. Three-sample
5000movement/100COD median86.867ms, full report hash
4619c9054ca6712265d91a8d45ed02d220d2c4bffb6fa863ab596441b280cfce unchanged,
independent bank/COD arithmetic PASS and input unchanged. `git diff --check`
passed. Payment-display files remain byte-identical to1842. Additional local
assembly test collection initially required missing test dependency reportlab;
this is not a product regression or a successful test result. Exact R2 CI is
still required before any release.

## Scope and base

Owner authorized resuming financial completion incrementally, with preparation responsiveness checked before restoring changes. Base: `18c245748d22615332d10c674812d4362b88ae71` (PR1839), after rollback1836 and Android76. Preserve courier/employee work and pending1840. Original financial PRs/checkpoints remain preserved; do not merge the old integration wholesale.

Production changed by this task: **no**. No /app mutation, lease, financial approval, tariff change, backfill, or deployment. No production load test.

## Findings and limits

- Current `operational_balance_service.report` scans the full movement history once per COD report to calculate incoming settlements. This predates the reverted changes: service source is unchanged between rollback baseline eee3078 and de76aed. Optimize it without changing arithmetic, ordering, currencies, completeness, or state.
- Financial HTTP routes synchronously call report/financial-position projection on the web event loop. A synthetic loop-delay measurement is not production preparation latency.
- Background operational reconciliation is in a separate process from web, but shares its background event loop with assembly/review jobs and shares host/database resources.
- Reverted1817 introduced after-balance capture. Final1820 still falls back to full report for prepaid courier expenses, once per new prepaid event. Cash-only and driver-review paths already have specialized projections. This is a concrete mechanism to evaluate before restoring the feature, not proof of the historical production incident.
- Reverted1820 adds period-profit computation when cost exposure uses the engine timestamp. Financial-position reads explicitly use that timestamp; arbitrary other reads do not necessarily execute that branch.

## Verified first slice: bounded COD settlement projection

The service now indexes incoming COD allocations in one history pass, retaining per-claim addition order, duplicate allocations, outgoing exclusion and unknown/empty claim behavior. No new state, cache, source fetch or financial write. It does not restore any rolled-back feature.

Intended regression before source edit: **1 failed / 2 passed**; history scans rose from17 for10claims to107 for100claims. After:8scans for both sizes. Root independently ran six related suites: **72 passed / exit0**, including financial position, report closure, engine and prepaid position. `git diff --check` passed. Independent source review found no blocker.

Synthetic 5000movement workload (milliseconds, warmed imports, same interpreter):

| Run | COD claims | Samples | Baseline median | Candidate median | Full report/input parity |
|---|---:|---:|---:|---:|---|
| Agent | 100 | 5 | 143.704 | 85.911 | PASS |
| Root independent | 100 | 3 | 128.314 | 85.648 | PASS |
| Agent scaling | 1000 | 5 | 678.777 | 106.260 | PASS |

Root100claim full-report SHA256 `4619c9054ca6712265d91a8d45ed02d220d2c4bffb6fa863ab596441b280cfce` matched before/after. Scaling1000claim hash `77b7d574211eb7e42b2a448464143ae8bcbdd9af0883360e50ebebf9d87eb125` matched. Independent bank/COD arithmetic passed and input remained unchanged. Root maximum timer delay153.067→112.348ms; candidate still blocks synchronously, so this is not complete isolation or proof of production recovery.

Root additionally ran100 deterministic mixed incoming/outgoing allocation workloads (seed937), compared every field against the base service and verified unchanged input: PASS/exit0. Historical prepaid-capture benchmark and its limits are recorded in PREPAID-AUDIT.md; that reverted implementation is not restored by this patch.

Reproduce: `PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests python -m pytest --noconftest backend/tests/test_operational_balance_cod_projection_performance.py backend/tests/test_operational_balance_financial_position.py backend/tests/test_operational_balance_report_closure.py backend/tests/test_operational_balance_engine.py backend/tests/test_operational_balance_engine_fixes.py backend/tests/test_operational_balance_prepaid_position.py -q --tb=short`. Benchmark: `python scripts/benchmark_financial_report_reads.py --repeat 5`; add `--claims 1000` for scaling and `--service-file` pointing to service saved from base18c24574 for before comparison.

## Verification boundaries

Synthetic-only fixture and repeatable benchmark prohibit network connections, compare complete report hashes and input immutability, and measure wall/CPU time and timer delay. A deterministic history-pass regression avoids timing thresholds in CI. Existing operational test glob includes the new test.

Fresh isolated venv bootstrap initially exposed missing bcrypt/cryptography/openpyxl dependencies. Those import failures are environment failures, not the intended regression evidence. They were installed without changing test assertions.

Next: preserve source checkpoint/Draft PR, run current-head CI and governed release prerequisites after coordination with1840. Separately eliminate prepaid capture full-report replay before restoring that feature. Never claim all financial tables complete from performance tests.
