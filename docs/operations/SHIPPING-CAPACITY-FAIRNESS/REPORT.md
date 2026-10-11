# AMASI_SHIPPING_CAPACITY_FAIRNESS_FINAL

Independent Draft #1335. **PRODUCTION RELEASE = BLOCKED.** No production
operation, Release Intent, provider test, APK/OTA or financial/stock write is
authorized or performed by this task.

## Source contract and identity

- Fresh Production BASE: `20191e31116bfa9921057ba907fd6df9b62c023b`.
- Imported #1332: `31c8afd98aefa8643643ff0ddf17b462930f006b`.
- Ordered integration checkpoint: `1203fbe41114e41479491059835145b08f9d229a`.
- Branch: `codex/shipping-capacity-fairness`.
- Final immutable HEAD/TREE, CI and test totals are in the final PR evidence
  comment. Never substitute integration-checkpoint CI for final-HEAD CI.
- Production advanced through #1333 (PDF/QR manifests) since the previous gate.
  The integration removed a duplicate PyMuPDF line. Both dependency manifests
  and `release/release-intent-v5.json` are byte-identical to this Production BASE.
- #1332 and original branches are unchanged. #1330 remains separate and was not
  adopted. No new release intent was created, and the inherited intent is not an
  authorization or valid release preparation for this candidate.

## Root cause and capacity change

Previously, issuance released its parser slot before hashing and Mongo storage.
The capability route read the complete blob before distributed admission, fetched
it again later, and returned a full-size response after admission had ended.
Slow persistence/read/send operations could therefore accumulate PDF copies
outside the two parser slots.

The existing two fail-fast slots now govern the complete application document
lifetime per event loop:

1. Issuance acquires before download and holds through parser verification,
   hashing, BSON persistence and return. Storage has a 10-second caller timeout.
2. The capability ASGI route acquires before any database read and holds until
   the response's last send or cancellation cleanup. Its total caller deadline
   is 45 seconds. Busy requests fail before loading PDF bytes.
3. The first read projects out bytes. Distributed document claim and final lease
   checks project only `_id`. One final blob fetch preserves integrity, expiry,
   shipment identity and canonical-order fences.
4. Motor IO is shielded. Cancellation or timeout does not release capacity while
   the underlying operation remains unfinished. Detached completion drains under
   the existing supervised slot ownership. A permanently stuck operation loses
   capacity rather than allowing unbounded replacement work.
5. Download and response chunks are 64 KiB. Response chunking avoids enqueueing
   an entire 2 MiB document in one ASGI write. The final empty send also observes
   server backpressure. No JSON/HTML success is appended after a partial PDF.
6. Parser child CPU=2 seconds, address space=512 MiB, wall=5 seconds and verified
   reap/quarantine policy remain. Parser input no longer concatenates the whole
   PDF with its header into an additional full-size Python object.

These are application admission bounds, **not a hard total RSS guarantee**.
For R replicas, W workers/replica and L event loops/worker:

| Quantity | Bound / interpretation |
|---|---|
| Admitted document lifetimes | `2 * R * W * L`, shared by issuance and reads |
| Unique admitted input PDF payload | `4 MiB * R * W * L`, each at most 2 MiB |
| Concurrent parser children | at most `2 * R * W * L`; often fewer during IO |
| Sum of child address-space ceilings | `1 GiB * R * W * L`; not reserved RAM |
| ASGI write size | at most 64 KiB, plus empty final body |
| Download/parse/store caller deadlines | 8 / 5 / 10 seconds, existing cleanup rules |
| Capability request deadline | 45 seconds; safe cleanup may retain capacity longer |

Transient download bytearray, BSON/driver, stdin and allocator copies must be
budgeted in addition to unique payloads. Shielding bounds concurrent ownership,
but does not turn Python/driver/native allocation into a cgroup limit. HTTP
server buffers can outlive ASGI send completion. Proxy/client buffers, request
objects, connections, middleware/APM-retained objects and other application work
are not bounded by the document slots. Hosting must supply a finite connection
and buffering policy plus measured native/process-tree memory before release.

Example: one replica with four workers and one loop each admits eight document
lifetimes and up to eight parser children: 16 MiB unique payload and 4 GiB sum
of child AS ceilings, before all other overhead. For per-replica acceptance:

`baseline/co-resident peak + parser RSS budget + bounded document/driver copies
 + connection/proxy buffering budget + reserve < effective cgroup memory limit`.

Actual production R/W/L and the remaining terms are unknown. Do not replace
them with Preview's numbers or extrapolate synthetic Python allocations to RSS.

## Fairness change and limits

The old first-16-orders window could contain only one cooling owner. The new
global-lease document persists an owner cursor and a frozen round due cutoff.
Each tick scans at most 16 distinct owners, chooses the oldest eligible order
for an owner, and advances with claim/expiry CAS. There is at most one bounded
wrap query. New/backed-off operations with due times after the round cutoff join
the next round. Cooldown, operation lease, read-attempt cap, GET budget, inventory
ownership and canonical status guards are preserved.

Global CAS is checked again after awaiting owner acquisition. Stale release
cannot erase a successor's claim or cursor. Cancellation/restart retains round
progress; lease expiry recovers abandoned ownership without provider POST.

One exhausted legacy pending row per tick is moved to requires_attention;
exhausted rows do not hide useful work for the same owner. A partial owner/due
index supports traversal. Each queue query has a 1,000 ms Mongo execution limit.
Local adversarial evidence (10,000 ineligible plus 2,000 eligible rows) made
useful progress in 699 ms but examined 7,000 keys / 6,999 documents (146 ms query).
The optimizer selected the older due index and top-one sort. An injected query
timeout produced zero GET and recovered on the next tick.

One returned row is not a bound on examined rows: ineligible history can still
increase query work and timeouts. Query timeouts perform no provider call and
must be monitored; unexplained repeated timeouts block capacity acceptance.

The global cooldown remains 15 seconds after a job and each owner 60 seconds.
At zero job duration, 100 continuously eligible owners can receive first service
at t=0 through t=1,485 seconds. This is conditional on a functioning DB, available
leases and the finite eligible round. It is not a production SLO. Initial service
for 2,000 jobs still needs about 8h20m minimum; fairness does not raise throughput.
Automatic read budget remains at most 12/job (at most 48/min sustained at zero
job duration), not a strict rolling-minute or application-wide GET guarantee.
Manual routes/document reads retain their separate controls.

## Verification and evidence interpretation

- Previous #1332 storage code reproduced failure of the new slow-storage
  regression: a third download entered instead of fail-fast refusal.
- Previous scheduler reproduced `[1,0,0,0]` at simulated 0/15/30/45 seconds.
- Local new capacity/admission/capability checks passed. The 2,000-concurrent-
  request synthetic slow-send test admitted exactly two PDF handlers and refused
  1,998 before handler/blob allocation. CAPACITY.json records Python allocations;
  native RSS, real slow sockets and production ingress are deliberately excluded.
- Event-based delayed IO tests cover caller timeout/cancellation and capacity
  recovery. They are unit evidence, not a measured Motor driver-thread stress test.
- Real isolated Mongo Replica Set, UUID databases and fake provider transport
  cover clustered 2,000-order/100-owner rotation, concurrent workers, lease loss,
  cancellation/restart, backoff/exhaustion and business collection invariance.
  The rotation case serves 100 distinct owners before job 101 repeats an owner;
  it does not claim a complete 2,000-job drain.
- CI runs exact source HEAD on Linux, forbids external socket connections,
  executes original delivered races, real parser limits, physical/virtual stock,
  review/writer/rollback contracts and current Production TikTok contracts.
- Initial local invocation errors (async fixture mode) were corrected to the
  existing CI's `--noconftest --asyncio-mode=auto`. Five legacy PDF route tests
  cannot pass on Windows because isolation intentionally rejects non-Linux;
  their actual acceptance comes only from fresh Linux CI, not a fallback parser.
- No guard or valid race test was removed. The final-reload race test accepts
  the new metadata-only keyword while retaining its terminal-writer assertion.

## Read-only hosting gate

No authenticated read-only Production topology/config export was available in
this task. The previously accessible code-server is Preview/UAT, not Production.
Its earlier Linux/aarch64, 2-CPU quota, 8-GiB cgroup, 7.875-GiB peak and old UAT
process observations are historical context only. The platform's `--workers 1`
process is not evidence of the application's worker count.

Request the following **sanitized read-only evidence from Emergent**:

1. Actual Production service/image identity and architecture, minimum/current/
   maximum replicas and surge policy, ASGI launch argv and workers/loops for
   every replica. No environment values or secrets.
2. Per-container CPU quota/period, memory.max/high/current/peak/events, swap,
   PID limits, co-resident process classes and cgroup sharing. Confirm Linux
   resource-limit/process-group compatibility and reviewed PyMuPDF installation.
3. ASGI concurrency/connection/keepalive limits, upstream buffering and maximum
   connections, request/response timeout policy. Provide metrics, not raw URLs.
4. Every ingress/load-balancer/Cloudflare/CDN/WAF/APM/error-collector/request-log
   layer: deployed sanitized configuration proving capability path redaction,
   query/header/referrer sanitization, body capture policy, retention, access
   control and sampling. No live capability examples or raw access records.
5. Cache bypass/no-store rules for success and errors and unexpected redirects.
   No-Store/No-Referrer headers do not by themselves prevent URL logging.
6. Isolated staging proof with invalid synthetic canaries (including encoded
   paths and errors before ASGI), then synthetic valid-document tests. Export
   only counters/hashes. Never test with customer capabilities or real providers.

Until these exist, actual replicas/workers, CPU/RAM/swap/PDF aggregate capacity,
CDN caching and absence of capability leakage are **BLOCKED / UNVERIFIED**.
Application sanitization does not certify hosting. A 300-second capability is
still a bearer secret; expiration cannot revoke bytes already downloaded.

## Monitoring and compatibility gate

Monitor admitted/retained/quarantined slots, pending-IO age, parser children,
timeout/refusal rates, cgroup/process-tree RSS and OOM, CPU throttling, event-loop
lag, Ready p95/p99, active connections/buffer bytes, queue query timeouts/examined
rows, oldest due age and per-owner time since service. Use aggregate/bucketed
labels, never token/URL/AWB/customer identifiers. Alert on growth or violated
approved budgets. Numerical production thresholds require isolated representative
measurements and the actual topology; no collector was enabled here.

The existing Ready/carrier-label URLs, PDF MIME, capability authentication model,
expiry and shipment/order identity fences remain. SMSA/iMile/store-courier rules
are unchanged. Build44 needs no source change for chunked HTTP responses.
Optional #1330 still requires approval_token absent from #269; it was not adopted.
Existing Samsung/printer plan and offline fixtures remain unexecuted. No APK,
OTA, device action or actual printing was performed.

Before release: independent final-source review, Production evidence above,
representative Linux/real-server slow-reader and native memory tests, approved
queue latency/SLO, and isolated Samsung/printer acceptance. No production or
paid action follows this report.
