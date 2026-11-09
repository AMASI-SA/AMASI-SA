# Financial performance recovery after rollback

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

## 2026-11-09 exact-source CI and release-candidate checkpoint

Source A `64ee370759dd6a3e148244bcaf8cb1461764d5c3`, tree `1860a890a4439abfd3248125c136892422a1ed56`, Draft PR1841. All 11 PR-triggered workflow runs returned by the connector completed SUCCESS, including release readiness42632991700, operational balances42632991795, G47 integration42632991734, stock42632991589, salaries, security and CodeQL. The connector is first-page-only; no claim about unreturned checks.

Readiness candidate artifact13682407024 downloaded and ZIP SHA256 verified `e0bf11f0edec8ea1cc085fffbd3bee1ba53107c4acf4943c11247d8f27dee88a`. Candidate validation and backend/release-control Git manifests PASS from an isolated exact-A worktree, exit0. Candidate runtime `rg5-0247b9aa567adf143da1ec7fcbf6ad30922bb962f14fd080f637235b4e167477`. Expected ignored backend/release_identity.json is absent in this source-only checkout; candidate validation is not a materialized build or release rehearsal. No intent-only B created.

Fresh readonly shared terminal observation: guard active:false and /app HEAD18c245748d22615332d10c674812d4362b88ae71. No shared code edit, lease, merge, financial write or deployment. Pending courier PR1840 has frozen A/B and ready checks but remains unmerged, blocked on its management sign-in. Preserve its release order: after1840 publishes, rebuild this narrow patch on the then-current production source, rerun exact-head checks, produce a NEW governed intent and rehearsal. This evidence branch is NOT a deployment branch and must never be merged as an implementation candidate.

Separately, an isolated historical prepaid-capture fix is under independent review. It does not restore the reverted financial bundle. No claim of production fulfillment latency recovery or completed financial tables.
