# Isolated Linux acceptance measurements — performance blocker remains

Measured HEAD: `4d7fa3953d49915d0bef1f719adab74380659553`
TREE: `af7bea78b2caa039906eeda8006814e03320279d`

Workflow run37236748938, Ubuntu/Python3.12/Mongo8.0.12. Each dataset runs on a separate runner, with sequential fresh before/after processes and no test workloads on that runner. No Production access. Financial signatures match by tenant; tenant values differ.

| Orders/tenant | Tenants | Concurrent | Before s | After s | Before MiB | After MiB | Mongo docs before | Mongo docs after | JSON after ms |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
|10000|1|1|3.823|4.183|208.90|111.87|37366|19187|5.45|
|10000|1|2|7.693|4.272|235.86|111.00|74732|19187|10.39|
|10000|1|4|15.112|4.126|294.79|110.90|149464|19187|20.64|
|10000|3|3|11.405|12.487|282.21|117.30|112098|57561|15.95|
|100000|1|1|35.306|33.738|1240.75|112.10|364642|182825|5.28|
|100000|1|2|70.938|34.215|1313.18|111.84|729284|182825|10.06|
|100000|1|4|141.239|33.681|1736.35|110.91|1458568|182825|19.10|
|100000|3|3|109.867|116.147|1516.12|118.80|1093926|548475|15.72|
|50000|1|1|14.264|13.363|670.78|111.89|182822|91915|4.27|
|50000|1|2|28.222|13.667|747.77|111.93|365644|91915|7.49|
|50000|1|4|57.034|13.570|842.91|111.73|731288|91915|16.22|

Same-key concurrency1/2/4 shares request-lifetime work. Three tenants exercise independent calculations. Do not equate these workloads. Mongo document counts are cumulative, not peak-resident batch counts.

198 dedicated Real Mongo cases passed, no failures/errors/skips. Frontend pagination and CodeQL passed. Source-matched Security dispatch37236752263 passed on the measured HEAD, without dependency or workflow policy edits. Historical PR-event Security failure remains visible: merged-base #1254 workflow required a file absent from the task source; that run cannot establish source Security status.

The full-period latency improves substantially relative to the interim memory-safe implementation (108–130s), but independent100k tenants remain116.147s vs109.867s before. Treat this as a remaining latency/throughput blocker, not READY. Peak memory stays approximately112MiB single/shared and119MiB independent across measured collection growth.

The local latest instrumented stage profile is separate evidence (76.431s), not the Linux acceptance latency. Async stage timings overlap; profiling/host variation prohibits adding them or mixing them into the table. Canonical parser invocations fall109092 ->72728; private decodes1326973 ->1109073, but structural call reduction alone does not prove wall-time benefit.

Next safe work: measured Dashboard-only reduction of remaining Python/replay work. Do not alter shared financial formulas, security policy, dependencies or import #1254. Product resolver caching was inspected but not implemented: full-item cache hit rates must be established first; unique line IDs/options may make it slower.

No Merge/Prepare/Prepublish/Deploy. Production unchanged, financial writes0.
