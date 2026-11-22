# Postpublication identity and latency evidence

Task: `OPERATIONAL-HOME-LOADING-RECOVERY-20261122`.
Date: 2026-11-22. Live observations in this record were supplied by the
coordinator; backend findings came from read-only source inspection. This
document records a verified release identity and remaining latency, not a
claim that the performance incident is resolved.

## Release verification completed

The candidate identities and exact-B CI results are retained in
[final-ci.md](final-ci.md). Production merge M5 is
`c302943e330bcdd534a4a5697b8ef813dfb7cb6e`, tree-equivalent to reviewed B
`8e5d0113be0daabe0989037c67315f2fc3d46574` at tree
`2eaeb022fa3cfc087026302158b35bcbc33ac990`.

- Emergent version `d8c03a2` was observed green/finished at 15:32 Riyadh
  (12:32 UTC). The new runtime reported boot time `12:32:21.811558Z`.
- At 12:39:28 UTC, a host public/API health observation returned HTTP 200 in
  859.33 ms, with the expected new source/runtime identity and verification
  flags true.
- The original Release Guard verification completed **SUCCESS, 3/3 probes**,
  with **760 public file fetches per probe** (2,280 total), in **889.211
  seconds**, finishing at **12:49:54.824478 UTC**.
- A fresh guard status subsequently reported **inactive**. The matching
  publication lease is closed; no further verification/publication should be
  inferred as pending from the earlier publication checkpoint.

The full guard operation was active during the earlier latency observations.
Those observations are not a controlled load comparison; no claim is made
that verification traffic caused the observed delays.

## Page observations and their measurement limits

| Observation | Recorded result | Interpretation |
| --- | --- | --- |
| First cold `/operational-balances` navigation | Authenticated home cards, accounts and recent movements loaded without an explicit retry | Bootstrap succeeded for this observation. |
| Repeat cold root reload, roughly seven minutes later | Initial authentication check, home context loader still visible at 25.198 seconds after reload, then cards appeared without an explicit retry | Cold navigation remained slow. This does not identify authentication, context, queueing or rendering as the dominant stage. |
| Home to entry through SPA navigation | Entry observed ready at 0.237 seconds | Existing full context was reused. **This is not query timing or evidence of a fast directory-only backend read.** |
| Fresh document at `?view=entry` | Initial authentication, then entry observed ready at 6.255 seconds | A fresh entry bootstrap completed; network phase times were not measured. |
| Fresh-entry document to home through SPA navigation | Context key/force policy required a new full-context query; loader appeared, then home cards were observed ready at 15.592 seconds | Additional full-context work completed, but its exact HTTP duration was not captured. |

These values are **tool-observation wall times/upper bounds**, including tool
overhead. They are not network TTFB, precise query execution times, or
comparable endpoint benchmarks. No actual Network waterfall was available.
The checks used navigation/read operations, with no form submissions or data
mutations.

Auth bootstrap and context have separate budgets: auth has an eight-second
overall budget; context has a 30-second budget beginning when its hook starts,
after authentication and the lazy route mount. A fresh document has no prior
document's pending hook lane. A loader at navigation plus 25.198 seconds followed
by cards can therefore be ordinary completion before the context deadline; it
does not demonstrate timeout recovery or an infinite loader.
Budget definitions: `frontend/src/context/authBootstrap.js:3` and
`frontend/src/pages/operational/ApprovedFinance.jsx:44`.

## Application health improved in samples; proxy and assembly latency remain

A later displayed log snapshot covered **12:53:49–12:54:16 UTC**: 12 successful
application health samples at 25.939–195.974 ms (median 82.696 ms), one context
HTTP 200 completion at 2,819.886 ms and one assembly HTTP 200 completion at
82,155.949 ms. No upstream timeout appeared in that displayed snapshot. It was
not a guaranteed real-time stream, and the missing request IDs/query flags
prevent conclusive pairing with a particular UI observation. This narrower
later sample does not erase the earlier intermittent timeouts or establish full
recovery.

In the coordinator's Production log window **12:36:37–12:37:12 UTC**:

| Route / layer | Samples and result |
| --- | --- |
| Application `GET /health`, HTTP 200 | 16 samples, 44.849–145.576 ms; median 82.802 ms |
| Application `GET /api/preparation-work-v1/assembly/orders`, HTTP 200 | Three samples, 126,005.984–184,558.173 ms; median 179,662.408 ms |

Nginx also logged four `/health` upstream timeouts at **12:36:43, 12:36:53,
12:37:03 and 12:37:13 UTC**. Fast application samples do not establish complete
request-path recovery. The application timer starts after ASGI dispatch and
ends at final body send; it omits preceding browser/proxy/accept/dispatch
queueing. Its lines contain no request ID or worker identity, so these samples
cannot be paired across replicas or treated as the same request. `/health`
performs no database or provider read. See
`backend/observability_middleware.py:34`, `:55` and `backend/server.py:233`.

Three assembly completions do not establish a p95, and their completion window
does not mean all their work occurred within that window. Broad availability
recovery, the database/CPU split and a sole causal effect of this release are
not established.

## Source findings retained for the next diagnosis

Source B and deployed M5 have the same tree. The exact home bootstrap request
is `/api/operational-balances/workspace/context` with `ad_bank_fx=true`,
`provider_positions=true` and `home_preview=true`.

- **Home:** authentication/scope/access checks precede a current-state read and
  off-thread snapshot unpacking, full current financial report preparation,
  navigation counters, sequential catalog reads, recent-posting projection and
  a final fresh-access check. The report begins with a state deepcopy and
  iterates movements and obligations. No historical audit pages are
  materialized by this reader, and the home path does not acquire the review
  preparation cache lock. Relevant functions: `checked_read` and
  `resource_catalog` in `backend/operational_balance_workspace.py`,
  `read_financial_position_state` in the corresponding state module, and
  `report` in `backend/operational_balance_service.py`.
- **Assembly:** tenant-wide piece order-number discovery precedes up to 5,000
  full workflows, projected approval proofs, up to 50,000 physical pieces and
  serial canonical hydration in 100-workflow batches. Membership, sorting,
  group counts and totals precede page slicing. `limit=1` alone does not bound
  acquisition. Full persisted Salla raw payloads are copied and mapped into
  complete DTOs; virtual pieces expand by quantity and may be built twice for
  local in-progress rows. No remote Salla call occurs in this GET. See
  `backend/preparation_piece_operations.py:3527`,
  `backend/fulfillment_v2_routes.py:2332` and
  `backend/order_engine/repository.py:725`.
- The new five-millisecond CPU budget yields between complete rows. It does
  not reduce cohort work or impose a wall-clock deadline. Existing Mongo
  connection/selection/pool-acquisition bounds are not whole-query or HTTP
  deadlines. The prior 205-order parity/fairness tests and small mobile timing
  fixtures do not measure this production cohort.
- Existing `api_timing` output has no auth/context/assembly phase breakdown.
  Optional process-local diagnostics expose lag, pool waits and Mongo command
  aggregates; the separate phase tracer permits dashboard/Snapchat stages,
  not this initial context or assembly chain. No diagnostic setting was changed.

### Index audit is source evidence, not a live query plan

| Read selector | Existing source declaration |
| --- | --- |
| `unified_orders` owner/order lookup | Unique `(user_id, order_number)`, `backend/server.py:5328` |
| Physical piece owner/order lookup | Nonunique `ix_preparation_piece_order_cost_v1(user_id, order_number)`, `backend/supplier_invoice_reporting_costs.py:48`; its installer is awaited at startup in `backend/server.py:4901` |
| Workflow owner/order lookup | Unique `(user_id, order_number)`, `backend/order_review_routes.py:354` and `backend/fulfillment_v2_routes.py:106` |
| Local completion proof | Explicit `_id` membership in `backend/review_local_policy.py:175`; worker also declares `(user_id, order_number, created_at)` in `backend/order_review_resume_worker.py:162` |

The source audit did not find a matching `(user_id, updated_at)` declaration
for the initial broad workflow OR query and sort. This is a **candidate query
support gap only**. It is not proof that the live index is absent, that Mongo
uses a collection scan, or that this stage dominates latency. Existing
owner/order declarations also mean hydration must not be described as having
no index support. No index change is justified by source inspection alone.

## Next bounded evidence and access block

The existing `directory_only=true` context path offers a useful differential:
it retains access checks and catalog reads but uses a narrow state projection
and skips snapshot unpacking, financial report/navigation work and recent
postings. The SPA reuse observation above did not exercise that difference.
The fresh-entry/home observations narrow the user-visible path but lack the
network evidence needed to identify a dominant stage.

If another observation is necessary, preserve one cold-navigation Network
sequence: auth attempts and any refresh/replay; lazy-route asset completion;
exact context request start/status/finish; queued/stalled, TTFB and download;
and first visible cards. Keep the same actor/state for any directory/full
comparison, avoid overlapping retries, and separate any post-response render
delay from backend delay. A later single assembly `q` comparison can reduce
downstream cohort work, but still incurs the initial distinct/workflow reads
and cannot isolate mapping CPU from database waiting by itself.

The attempted **private Production Mongo Viewer snapshot was rejected by
automatic approval review**. Its stated reason was that authorization covered
the page fix, not explicit access to the private database. The viewer was
closed after the denial. **Mongo connection credentials were not revealed or
copied; no database contents/schema/query plans were read.** No private viewer
URL or token is recorded here.

If database evidence becomes necessary, the next approval must be explicitly
limited to **read-only index definitions, exact query plans and bounded
statistics** for these selectors. Start with index definitions/query-planner
evidence; any execution statistics should be separately bounded and serial.
That scope must exclude customer document inspection, index writes and all
financial/data mutations. No alternate access path was used after the denial.

**No definitive dominant stage or new patch is justified by the present
evidence.** The coordinator owns the next diagnostic decision and the task
status/release-state records. This documentation task changed only this file;
it performed no commit, push, browser or production operation.
