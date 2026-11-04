# Local dashboard process comparison

The same dashboard computation ran inside the operation routes' process in one arm and in a second local Python process in the other. Both shared one temporary MongoDB server and the host's CPU/memory quota. At the same offered job rate, the four business routes had lower p95 and p99 latency in the separate-process arm in both runs.

| Probe | Run 1 idle | Run 1 shared | Run 1 separate | Run 2 idle | Run 2 shared | Run 2 separate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Employee workspace with supplier assignment readback | 10.78 / 11.12 | 96.52 / 139.97 | 19.13 / 37.59 | 19.89 / 24.43 | 70.68 / 99.96 | 13.22 / 15.82 |
| Supplier file creation | 14.48 / 16.29 | 103.14 / 140.99 | 24.75 / 36.20 | 31.21 / 44.80 | 68.74 / 93.76 | 20.44 / 21.55 |
| Order detail | 5.67 / 6.28 | 33.86 / 75.66 | 5.97 / 8.65 | 10.35 / 11.67 | 14.25 / 42.26 | 6.14 / 6.92 |
| Saved carrier label | 4.14 / 4.45 | 43.53 / 72.76 | 5.61 / 19.11 | 8.41 / 15.70 | 27.15 / 55.15 | 4.74 / 6.84 |
| Health payload control | 1.87 / 2.20 | 9.18 / 12.10 | 2.03 / 2.23 | 2.71 / 8.71 | 1.94 / 9.80 | 2.07 / 2.20 |
| Application loop wake-up delay | 1.88 / 2.02 | 10.18 / 11.53 | 1.90 / 2.10 | 2.08 / 3.13 | 10.08 / 15.03 | 1.94 / 2.10 |

Each cell gives p95 / p99 in milliseconds (nearest rank). Each run used 100 fixed arrivals per business probe and per health control in each arm, 80 ms apart per probe and staggered by 16 ms. Every planned arrival counts from its scheduled time, including loop delay. The 10 ms loop monitor counted 800 ticks per arm. All 3,000 requests passed their response checks, none missed the one-second budget, and all six arms verified exactly 102 supplier dispatches with 102 associated events, including two warmups. The workspace returned the known saved assignment on every read. The order detail returned the expected synthetic order, and the label lookup returned its saved URL.

In each loaded arm one sequential consumer handled 50 dashboard jobs arriving every 160 ms. Every job evaluated the real cooperative summary cost stage over the same 350 synthetic orders with four lines each and verified a total of 12,600. All 200 jobs completed within their eight-second probe windows. Run 1 had idle, then shared, then separate. Run 2 used the reverse order. Each arm started with identical fixtures in a new database on the same private MongoDB server. One untimed dashboard calculation warmed the process that would execute the work. Raw counts, per-job service and arrival-to-completion percentiles, deadlines and per-probe results are in [LOCAL-ISOLATION.json](LOCAL-ISOLATION.json).

The work was executed at commit `d5531f3745e17dcb81dd50ba70239e5bc54ff5fe` with the benchmark extension in the working tree. Python 3.12.14, MongoDB 8.0.12, a shared host quota of eight CPUs and 8 GiB of memory. One spawned dashboard-only interpreter was supervised through readiness, a common monotonic start time, bounded completion, clean exit and fallback termination. Each interpreter restricted outbound connections to the temporary loopback MongoDB port. The test harness mounted only the needed routers and kept the application lifespan disabled.

The governed product-cost-summary route returned HTTP 503 `resource_pressure` in every preflight on this host. The experiment exercised the summary's cost function directly, with order documents preloaded in memory and catalog/authority reads on real MongoDB. ASGI routes used fixed test identities, with preparation-employee roles checked in MongoDB. The health control returned the local release health payload. The measured paths exclude network transport, full dashboard loads, ads synchronization, a managed worker lifecycle, provider traffic and production infrastructure. The incident cause and the platform’s deployment capabilities still require operational evidence.

```sh
python scripts/benchmark_dashboard_contention.py --mongod /path/to/mongod --workload summary-cost-stage --compare-isolation
```
