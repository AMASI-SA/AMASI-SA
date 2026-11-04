# Local full dashboard HTTP comparison

`run.sh` accepts a Git checkout containing both named commits, the frozen fixture checkout at `05b159dc4183f14adc7c370bfbd8014d8858e6b8`, a mongod executable, a Python interpreter with backend test dependencies, the output filename, the order count (a multiple of four), and the arm sequence.

```sh
./run.sh "$repo" "$fixture" "$mongod" "$python" results.json 512 ABBABAAB
```

The driver exported the complete backend from base `a56dbacd36678566eec3b5a76406db7582c3e7f2` (A) and candidate `d5531f3745e17dcb81dd50ba70239e5bc54ff5fe` (B). Each block used a separate Python process and newly seeded database on a private MongoDB 8.0.12 server. Both ranges were warmed first, followed by four measured full HTTP reads per block in the order month, selected, selected, month. The fixture contained 512 orders split equally across two dates and SAR/QAR, linked to one catalog product.

Both authentic `GET /api/dashboard-v2` implementations handled each call through FastAPI and HTTPX ASGI transport. The frozen repository test helper extracted the corresponding actual legacy server function. Both arms bound its normal currency reducer, cooperative order parser and deepcopy globals, without importing server startup. The route retained the production CPU budgets and real database/provider readers against the isolated synthetic database. A read-only collection wrapper blocked writes during measured requests. Auth used a fixed synthetic owner, the route date was fixed at 8 Oct 2026, and the process could connect only to this run's private loopback mongod. A synthetic cgroup reported 128 MiB current and 2 GiB maximum to admit the workload consistently. No production pressure thresholds were changed. There was no web server, proxy, service lifespan, replicated web/analytics process or concurrent employee traffic.

The driver compared the SHA-256 of canonical full response JSON across every measured repetition and both arms, and checked independent expected SAR totals and field-level currency conversion counts. All 32 responses were HTTP 200, with one digest per range. The complete month returned SAR 38,784 and the single-day subset SAR 19,392.

| Metric in ms | Base | Candidate |
| --- | ---: | ---: |
| Whole-response p50 | 92.821 | 91.816 |
| Whole-response p95 | 119.216 | 123.425 |
| Loop delay p95 | 9.641 | 10.571 |
| Loop delay p99 | 13.429 | 17.160 |

Per arm, there were 16 measured full requests, so nearest-rank p95 and p99 both select the maximum. A 5 ms periodic task accounted for every tick due while a full HTTP call was in flight, including missed wake-ups. It recorded 303 ticks in A and 302 in B. The numbers do not establish a full-route tail-latency improvement. They do not measure operational latency or multi-process isolation. The timings include handler execution and HTTP serialization but exclude post-response validation and hashing. The raw aggregate, per-request durations, delay summaries, hashes and money checks are in `results.json`.

## Larger cohort and budget check

A second uninstrumented run used the same driver, block order and counts with 2,048 orders. Change the final arguments to `results-2048.json 2048 ABBABAAB`. All 32 measured responses were HTTP 200 and matched byte-for-byte after canonical JSON serialization by range. The expected full and selected amounts were SAR 155,136 and 77,568 respectively.

| Metric in ms | Base | Candidate |
| --- | ---: | ---: |
| Whole-response p50 | 306.952 | 318.654 |
| Whole-response p95 | 363.858 | 375.074 |
| Loop delay p95 | 32.926 | 33.570 |
| Loop delay p99 | 68.268 | 59.688 |

The corresponding scheduled tick counts were 935 and 988. Across both cohort sizes these are 64 successful measured full responses. The heavier run's loop-delay p99 fell, while loop-delay p95 and response p95 rose. This is a shared-host synthetic sample and does not establish an end-to-end improvement.

A separate diagnostic posted one ready event-loop callback immediately before each actual outer currency reducer and checked whether it ran before that same call returned. We observed both implementations with their normal 5 ms CPU budget, apart from this small wrapper at reducer entry and exit. At 512 orders, the callback ran inside 0 of 6 base passes and 0 of 6 candidate passes. At 2,048 orders, it ran inside 0 of 6 base passes and 6 of 6 candidate passes. Timing tables above use exclusively uninstrumented runs. The diagnostic covers same-range aliasing and separate selected/current-month inputs. To reproduce the diagnostic, copy both scripts to a clean directory, apply `diagnostic/ready-callback.patch` there with `patch -p1`, and call `run.sh` with `512 AB` or `2048 AB`. The recorded diagnostic outputs are in `diagnostic/observed512.json` and `diagnostic/observed2048.json`.

## FX hydration scheduling

The legacy dashboard and the V2 dashboard order reader both rebuild historical currency fields from narrow Mongo projections. The isolated diagnostic compares the original synchronous pass at `939d4a8f7cfce9ca0c93a99cc9d2595490f66b34` with the cooperative pass on the same synthetic orders. Half of each cohort uses native SAR and half uses verified QAR. Ten alternating trials per version produced identical serialized order data at both sizes.

| Orders | Median largest ready-callback gap, base / candidate | Median phase duration, base / candidate |
| ---: | ---: | ---: |
| 2,048 | 14.48 / 5.18 ms | 14.45 / 14.22 ms |
| 8,192 | 56.08 / 5.29 ms | 56.03 / 57.14 ms |

One continuously ready asyncio callback records scheduling opportunities during hydration. At 8,192 orders other tasks could run sooner, while the full hydration phase took about 1.1 ms longer. HTTP response time, scheduled-tick latency, database work and the employee incident are outside this in-memory measurement.

Run from the repository root with `PYTHONPATH=backend python docs/operations/MZ-RUNTIME-299768/full-summary-ab/diagnostic/currency_hydration.py`. Fixture sizes, raw precision and complete output hashes are in [currency_hydration.json](diagnostic/currency_hydration.json).
