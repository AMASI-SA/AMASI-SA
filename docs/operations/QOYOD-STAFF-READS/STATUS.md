# Qoyod background work and employee reads

Issue #1739 starts from production commit `c47738b05397104b8b1a22d133b635cd1ce58c49`. This is a separate scope from #1724. The intermittent production incident remains open.

The dashboard badge reads one saved count for the authorized owner and order scope. The finance page can explicitly recompute the canonical count. Unknown, stale and incomplete observations are distinguished. The badge does not run the historical report on staff screens, on tab focus or when the most recent observation is old.

The automatic sender still checks historical order and invoice references. Its selector now yields between batches and skips building report-only proof for previously invoiced orders. Selection order, snapshot identity, quarantine and retry behavior are checked against the full audit. The sender's per-order validation and outbound operations are outside the patch.

## Offline verification

Run the benchmark with the backend dependencies installed and a disposable local MongoDB binary.

```sh
python docs/operations/QOYOD-STAFF-READS/measure.py --source /path/to/checkout --mongod /path/to/mongod
```

The fixture creates 2,000 synthetic settled orders, with 142 held and the rest matched to a financial reference. The production selection function returns zero runnable candidates throughout. The employee HTTP fixture checks the actual pending-review response on each request. Network access is restricted to the private database. The fixture measures from the scheduled start of a request, including event-loop delay.

| Alternating baseline and candidate runs (ABBA, BAAB) | Baseline | Candidate |
| --- | ---: | ---: |
| Successful pending-review reads | 512/512 | 512/512 |
| Pooled pending-review p95 in ms | 86.48 | 78.36 |
| Median selector duration in ms, 32 rounds per arm | 123.96 | 99.62 |

Individual runs varied, including one pair in which employee p95 increased. These local timings do not measure the field incident, staff device latency or the time to render every screen. No external invoice was sent and no production configuration was changed in this work.

Local verification covered the passive and explicit-refresh routes, fairness and cancellation, backlog selection, payment authority, auto-send protection, reconciliation, and the existing frontend header and report tests. Continue with PR checks and staff timing at the next captured incident before evaluating a release or claiming a production improvement.
