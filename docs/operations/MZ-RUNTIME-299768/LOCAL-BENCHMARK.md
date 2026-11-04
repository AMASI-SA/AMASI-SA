# Synthetic dashboard contention benchmark

On branch checkpoint `8ec4f1aaa2449369b402d6c1985ed761c52c739a` with the local dashboard candidate, `dashboard_v2_routes.py` SHA-256 was `5f68548a623052b9a85dab88db1d1e0b9082ade8843c66d7919af46a1c17cbb6`. The raw output is in [local-contention.json](local-contention.json). The command exited 0.

```sh
python scripts/benchmark_dashboard_contention.py --mongod /path/to/mongod --workload summary-cost-stage
```

Eight seconds per phase, 100 requests per probe at fixed 80 ms intervals, staggered by 20 ms. Durations include delays between scheduled arrival and dispatch. Percentiles use nearest rank. All planned arrivals and 800 scheduled 10 ms loop ticks per phase are counted. A separate one-second response budget counts late completions without cancelling their underlying writes. Both phases started with the same fixtures in fresh databases and all endpoints were warmed.

| Probe | Idle p95 / p99 (ms) | Concurrent dashboard stage p95 / p99 (ms) |
| --- | ---: | ---: |
| Employee workspace and saved supplier assignment readback | 13.94 / 15.06 | 75.06 / 88.77 |
| Create a new supplier dispatch for one assigned piece | 17.28 / 20.07 | 123.44 / 139.90 |
| Read an existing external carrier label | 4.48 / 4.89 | 49.09 / 53.91 |
| In-process health payload control | 2.05 / 3.31 | 10.84 / 11.59 |
| Event loop wake-up delay | 2.39 / 2.83 | 10.46 / 11.58 |

Across 800 measured probe requests there were zero failures and zero one-second budget misses. Both phases persisted 102 unique supplier dispatches and 102 corresponding supplier events, including two warmups. Each workspace response had eight waiting pieces and included the expected saved supplier dispatch. Each label response returned the seeded URL. The benchmark never opened or downloaded that URL.

The loaded phase completed 105 invocations of the real cooperative product-cost function used by `/api/dashboard-v2`. Each invocation used 350 synthetic order documents, four lines per order and a real MongoDB catalog, and its total had to equal 12,600. Orders were already in memory before the stage, as they are when the summary invokes it. A separate preflight of the governed `/api/dashboard-v2/product-cost-summary` route returned HTTP 503 `resource_pressure` on this shared host in both phases. The measured load invoked the summary's cooperative stage directly. The guard thresholds were unchanged.

FastAPI routers ran in one event loop via HTTPX ASGI transport, without web-server sockets, deployed middleware, startup jobs, Cloudflare or provider requests. The workspace and supplier creation used fixed preparation-employee identities and real database role checks. Label lookup used a synthetic owner. The health control used the release health payload without the application's production readiness gate. A private loopback mongod (8.0.12) ran in a temporary directory and Python outbound connects were restricted to its port. Python was 3.12.14. These local measurements show contention for this workload. Deployment isolation, Preview/Production latency, the incident cause and the effect of an independent service remain unverified.
