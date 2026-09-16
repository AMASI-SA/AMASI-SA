# Salla Orders V3 — P0 shadow release

Current release-preparation base: `76542622073bba1c61e87038cb855a72907484c7`

Original audit base: `1de6118484ac4fe1d0981e230618dbb573d8c58c`.
PR #1000 hardening baseline: `013ca7cf9abb3e150c20b76950ca1369004c5007`.

This change deliberately stops before cutover. V3 is an isolated observer and
compatibility producer. Existing Order Review, Fulfillment, supplier files,
Qoyod, Snapchat attribution, campaign revenue, and dashboard totals continue to
read the current production order path.

The hardening source is now preserved on GitHub in PR #1000 and the cycle-5
recovery branch. The prior frozen A/B pair remains preserved in PR #1049.
Current preparation uses `codex/salla-v3-release-current-20260916`; its PR and
exact checkpoint are recorded in Issue #1006. It does not enable Shadow, create a
cutover route, touch provider data, or alter Production.

## Root causes confirmed in the production base

1. `salla_integration/auto_sync.py::_discover_recent_orders` requests 60 rows,
   reads only page 1, and slices the response to 60. The documented List Orders
   maximum is 30, so newer orders can hide older updated orders on later pages.
2. `auto_sync.py::_reconcile_status_page` treats
   `len(rows) < requested_per_page` as exhaustion. A Salla cap below the
   requested size therefore resets the cursor after page 1.
3. `auto_sync.py::_sync_light_order` returns immediately for every locally
   existing order. An existing order with missing products or customer options
   is never repaired by that path.
4. `salla_integration/sync.py::run_orders_sync` persists List Orders Light rows
   without calling List Order Items. It can discover orders, but cannot provide
   authoritative products or customer choices.
5. The verified order webhook stores the base order snapshot but does not place
   a durable Order Items enrichment job. A one-time enrichment failure therefore
   has no order-scoped retry state.
6. Single-order implementations are duplicated across `sync.py`,
   `order_engine/salla_refresh.py`, and
   `order_commerce_enrichment.py`. Their parameters and shipment behavior differ;
   two Order Details calls omit explicit `format=light`.
7. `orders_db.py` preserves non-empty historical item arrays, but the caller
   cannot express four different states: authoritative empty success, endpoint
   failure, invalid payload, and endpoint not called. The current order upsert is
   also a read-merge-write sequence rather than a stale-versioned order write.
8. The page-triggered scheduler is called from Order Review and Order Engine
   reads. It is not an independent recovery service and uses in-process task and
   cursor dictionaries rather than a distributed lease.

## Existing paths audit

| Path | Discovery/internal id | Products/options | Persistence risk | Trigger |
|---|---|---|---|---|
| `auto_sync._discover_recent_orders` | `GET /orders`, Light, `row.id` | `GET /orders/items` only for a new local order | existing incomplete orders skipped; page 1 only | Order Review/Order Engine page reads |
| `auto_sync._reconcile_status_pages` | sequential `GET /orders` Light | never fetches items | short-page exhaustion uses row count | page-triggered background task |
| `sync.run_orders_sync` | paginated `GET /orders` Light | does not fetch items | Light row is mapped and saved | manual sync/background task |
| `sync.resync_single_order` | List Light then Order Details | `GET /orders/items`, plus shipment APIs | independent mapper/merge implementation | manual consumers, Qoyod preflight helpers, shipping helpers |
| `order_engine.salla_refresh` | List Light then Order Details | `GET /orders/items` | separate merge and freshness fields | order detail/review/engine routes |
| `order_commerce_enrichment` | List Light then Details Light | `GET /orders/items`, plus shipment path | fourth independent implementation | diagnostic/enrichment caller |
| verified business webhook | webhook payload id | no Order Items call | base order can remain incomplete | immediate webhook |
| `orders_db.upsert_order` | n/a | raw rich-item preservation | no authoritative-empty state or stale revision condition | every current Salla writer |

## V3 canonical boundary

- `salla_orders_v3/gateway.py`
  - `GET /orders?format=light&per_page=30`
  - sequential pagination driven by `currentPage`, `totalPages`, and `links.next`
  - one-day `from_date` / `to_date` discovery windows with a durable local page
    cursor; `updated_at_gt` is not used because this PR has no accepted provider
    contract evidence for it
  - `GET /orders/{internal_order_id}?format=light`
  - `GET /orders/items?order_id={internal_order_id}` as the only item source
  - bounded retry with exponential backoff; caller overrides cannot exceed the
    reviewed provider-attempt cap
- `salla_orders_v3/normalizer.py`
  - one normalizer for list/dict option containers and scalar/dict/list values
  - supports `value`, `name`, `label`, `text`, `option_value`, `selected`,
    `choice`, and `answer`
  - preserves `0`, `false`, multiple selections, attachments, files,
    customizations, and personalization
  - stores both `raw_item` and a display-safe normalized item
  - rejects empty/identity-less item objects before they can become
    authoritative; uses the provider line id when present and a deterministic
    product/variant/SKU/name signature only as a fallback
- `salla_orders_v3/compatibility.py`
  - emits the current top-level order fields and current `products/options` shape
  - keeps current campaign and UTM fields
  - adds audit-only V3 metadata without requiring a consumer schema migration
- `salla_orders_v3/worker.py`
  - separate discovery and enrichment loops, so a slow or failed Items request
    cannot pause later List Orders discovery
  - metadata pagination, daily windows, durable continuation/page cursors,
    explicit page-budget truncation, bounded traffic, bounded concurrency,
    capped backoff, and fenced Mongo leases with heartbeats
  - stable `(user_id, store_id, _id)` integration ordering plus a durable scan
    continuation, so stores beyond the first 100 are not starved
  - discovery records only order identities in the queue; only the independent
    worker calls Order Details / Order Items
  - every Light row is one atomic scheduling signal. One total, transitive key
    orders revision presence, numeric provider revision, `provider_updated_at`,
    verified `event_created_at`, then a deterministic payload digest. Arrival
    permutation cannot create aggregate-clock hybrids or change the winner
  - the winning Light payload and its clocks replace the prior signal as one
    unit. Light fields never deep-merge across revisions, omitted fields stay
    omitted, and an explicit empty object remains a clearing value. Canonical
    Order Details, not Light, supplies the persisted candidate snapshot
  - an existing pending/retrying/processing job keeps its attempts, status, and
    error; a completed/failed job reopens only for a newer signal or an explicit
    owner-authorized manual requeue; an expired processing lease is recoverable
    only below `MAX_JOB_ATTEMPTS`
  - a newer signal gets an independent attempt budget even if it arrives during
    the previous revision's last attempt
  - claims occur only inside available worker slots, so no claimed lease or
    provider attempt waits behind the concurrency gate
  - every job claim and exhausted-lease sweep samples a fresh clock value;
    finalization requires `lease_expires_at > finalize_now`, and heartbeat stays
    active until the exact terminal/snapshot compare-and-swap completes
- `salla_orders_v3/shadow.py`
  - performs only provider reads, normalization, and candidate construction; it
    has no persistence operation
  - after identity-checked Order Details succeeds, that Details response is the
    sole canonical base. Stale or partial Light fields are never merged into it
  - the worker stores a successful compatibility snapshot in the same isolated
    job row as its terminal transition, using one Mongo update fenced by exact
    job id, lease token, lease epoch, and `signal_revision`
  - stale workers therefore cannot write an authoritative snapshot, and failed
    reads never overwrite the last successful snapshot
  - the worker rechecks token, epoch, expiry, and signal after Order Details and
    before Order Items, so a superseded Details read cannot trigger the more
    expensive Items request
  - failed, invalid, and not-called Items outcomes are never represented as an
    authoritative empty product list; successful empty responses are marked
    explicitly, while incomplete rows remain eligible for enrichment retry
- `salla_orders_v3/diagnostics.py` and `parity.py`
  - strict Fulfillment, Qoyod dry-run, Attribution, and regression gates
  - Fulfillment requires a valid authoritative snapshot for the queue's latest
    signal, exact owner-bound typed `(user_id, store_id, order_number)` identity,
    and compares options/custom fields by semantic `name`/`value`, not
    raw/provider provenance. Canonical values carry explicit type tags, so a
    boolean never equals an integer; malformed or unsupported choice rows fail
    the gate instead of disappearing from the comparison
  - Qoyod evidence requires literal booleans, a non-empty string idempotency
    key, and either a non-empty eligible payload or an explicit ineligible
    reason. Fulfillment and Qoyod evidence on both sides must bind to the same
    typed order identity and authenticated owner
  - Qoyod and Attribution each require non-empty evidence or an explicit
    persisted `not_applicable` audit loaded read-only for the authenticated
    owner; callers cannot create an exemption by passing a mapping
  - the expected candidate HEAD is resolved from the backend's already-validated
    runtime release identity. It, an internally generated run id, authenticated
    owner, and internally sampled lifetime are persisted as one owner-bound
    parity-run context; the report samples its own evaluation time. Report
    callers cannot supply or override those values, and an audited N/A must
    belong to that same run
  - all parity evidence must carry that same full commit SHA, run id, owner id,
    and a bounded fresh timestamp. Attribution requires unique order ids,
    finite numeric amounts, and an exact row count. The five named regression
    results must be present with values exactly equal to boolean `true`
- `salla_orders_v3/ingestion.py`
  - verified event rows are durable outbox intents; their TTL begins only after
    an idempotent job enqueue is marked delivered
  - a bounded local repair sweep closes event-insert/job-enqueue crashes, but is
    registered only inside the same disabled-by-default V3 worker lifecycle. It
    has its own isolated lease, reviewed row budget, and returned/logged metrics;
    it has no provider call and no separate server route or startup hook
  - every event intent is atomically claimed with a per-row token, monotonic
    epoch, and expiry. Pre-enqueue, success, retry, and quarantine writes all
    require that exact unexpired fence. Job materialization and the delivered
    marker commit in one Mongo transaction, so a takeover aborts and rolls back
    the job write instead of allowing the stale owner to leave a queue row.
    Missing transaction support fails closed and leaves the intent retryable
  - failed intents use a 600-second base backoff, greater than the 300-second
    repair cadence, and the bounded scan sorts by `next_attempt_at` before
    arrival order. Multiple old poison pages therefore cannot starve a newer
    due intent; exhausted or malformed-attempt rows enter an auditable,
    bounded-retention quarantine and are reported truthfully in repair metrics

Official Salla references used for this design:

- [List Orders](https://docs.salla.dev/5394146e0) — sequential pages and
  `per_page=30`
- [Order Details](https://docs.salla.dev/5394147e0) — Light excludes Items
- [List Order Items](https://docs.salla.dev/order-items/list) — complete items by
  `order_id`, requiring `orders.read`

## Shadow safety contract

`SALLA_ORDERS_V3_SHADOW_ENABLED` defaults to false. When explicitly enabled,
the startup worker and verified webhook observer can write only:

- `salla_orders_v3_shadow`
- `salla_orders_v3_events`
- `salla_orders_v3_jobs`
- `salla_orders_v3_sync_state`
- `salla_orders_v3_leases`
- `salla_orders_v3_parity_audits` (read-only evidence source in this PR; no
  audit writer or route is introduced)
- `salla_orders_v3_parity_runs` (internally created, owner-bound, one-hour
  trusted report context; no HTTP route is introduced)
- `salla_orders_v3_parity_evidence` (read-only source of sealed artifacts bound
  to one run, verified runtime HEAD, owner, internal observation time, digest,
  and TTL; this PR introduces no evidence writer or route)

The legacy-named `salla_orders_v3_shadow` collection stays on the closed
allowlist for review compatibility, but this hardening pass has no writer to
it; the only authoritative candidate is committed in the fenced job row.

No V3 code mutates current order storage, inbox storage, accounting jobs,
preparation data, Salla order state, or advertising attribution. Static tests
enforce the two operational-collection prohibitions.

`salla_orders_v3/config.py` is the closed P0 configuration boundary. It permits
only the eight isolated collections above, GET-only Orders paths, one reviewed
environment switch (`SALLA_ORDERS_V3_SHADOW_ENABLED`), and fixed page, retry,
concurrency, integration, outbox-attempt/backoff, one-hour parity-run, and 30-day
retention budgets. Unknown V3 environment switches fail closed.
`CUTOVER_IMPLEMENTED` is permanently false in this phase.

## Required pre-cutover proof

Cutover remains closed unless one report shows all of the following as passed:

1. Fulfillment parity: product count, quantity, `order_item_id`, SKU, options,
   and custom fields, with a valid authoritative V3 Items snapshot whose
   successful revision exactly matches the queue's latest signal.
2. Qoyod parity: unchanged eligibility, exact dry-run invoice payload, and exact
   idempotency key; `provider_write_reached=false`. Empty evidence fails closed
   unless a fresh, same-run persisted owner audit records it as not applicable.
3. Attribution parity: identical attributed/unattributed counts, campaign id,
   UTM fields, per-campaign revenue, and zero duplicate order numbers. Empty
   evidence uses the same persisted-audit rule.
4. Fresh regressions for Order Review, Fulfillment, Qoyod, Snapchat attribution,
   and dashboard order totals; the key set must be exact and every value must be
   the boolean `true`. The report accepts only a persisted, sealed artifact
   whose digest covers its payload plus full HEAD SHA, run id, owner id, and
   storage-observed time. The artifact cannot predate its run and is invalidated
   if the verified runtime HEAD changes. No caller-facing evidence writer or
   route exists in this phase.

Fulfillment and Qoyod evidence must name the same exact owner, store, and order.
Persisted not-applicable audits are also bound to that identity, so an exemption
for one store or order cannot be reused for another.

The gate implementation is `salla_orders_v3.diagnostics.build_parity_report`.
It may report `parity_ready=true`, but always reports
`cutover_allowed=false`. No cutover switch or operational adapter registration
exists in this change. Shadow comparisons require the exact authenticated owner
identity, and no diagnostic or manual-requeue HTTP route is introduced.

## Durable cursor and queue invariants

1. Each recovery lease is scoped to `(user_id, store_id)` and co-located with
   its sync-state row. Heartbeat, release, and every checkpoint require its
   exact token and monotonically increasing epoch.
2. `salla_orders_v3_sync_state._id` exists only in selectors and
   `$setOnInsert`; repeated checkpoints update by `state_revision` CAS and never
   place `_id` in `$set`.
3. One fenced state update records both the next cursor and a bounded durable
   queue-intent list. Job materialization is idempotent; after a crash the next
   lease owner repairs the intent before fetching another page, then clears it
   with the same token/epoch/revision fence. A lost owner cannot advance the
   cursor.
4. `next_from_date`, `next_to_date`, and `next_page` encode continuation.
   `continuation_reason` distinguishes pagination, advancement to the next
   daily window, and catch-up; `truncated` plus `truncation_reason=page_budget`
   explicitly records a bounded run that stopped before that continuation.
   `pagination_started_at` anchors the provider session to its first page;
   checkpoints never extend that lifetime. [Salla's List Orders contract](https://docs.salla.dev/5394146e0)
   requires sequential pages within 15 minutes. V3 uses a conservative 14-minute
   budget: expired, missing, invalid or future session timestamps restart page 1
   of the same date window after repairing pending queue intents. A response
   arriving after that budget cannot mark the date complete, even when empty.
   It checkpoints a restart and reports `pagination_session_expired` instead.
   Replay remains idempotent and counts toward the existing page/request budget.
5. A queue claim increments attempts and `lease_epoch` atomically. Heartbeat and
   terminal/snapshot writes require the same token, epoch, and claimed
   `signal_revision`, so a newer webhook/discovery signal releases the stale
   revision without accepting its result.
   The post-Details/pre-Items fence uses the same identity, and the final write
   additionally requires an expiry later than the freshly sampled finalization
   time while periodic heartbeat is still running.
6. A verified event remains without a TTL until its job enqueue is durably
   marked. Queue terminal rows and delivered event/snapshot data have bounded
   retention; active jobs and pending event intents do not expire. Each outbox
   attempt increments its row lease epoch atomically. Final success and every
   failure transition require the same token, epoch, and a live expiry. Failed
   deliveries back off before re-entering a due-time-first bounded scan;
   exhausted or malformed intents are atomically quarantined with an audit
   reason and bounded TTL.
7. Event-to-job materialization spans two isolated collections and therefore
   runs only inside a Mongo transaction. The transaction rechecks the exact
   unexpired row token and epoch before the job mutation and at finalization;
   any concurrent takeover creates a write conflict and rolls back the job.
   There is no non-transactional fallback.

## Scope and real-data evidence status

On 2026-09-01 a signed-in, read-only inspection of the production Salla
integration page confirmed that the integration is connected and that its
stored scope includes `orders.read_write`. The scope diagnostic accepts either
the official read-only spelling `orders.read` or Salla's currently stored
combined spelling `orders.read_write`, reports which spelling was observed, and
never returns token fields. No OAuth reconnect is required for this change.

The same read-only page showed completed historical Orders sync runs, including
multi-page runs, but the V3 code is intentionally not deployed or enabled.
Therefore no real List/Details/Items triplet or Shadow-vs-current parity sample
exists yet. Those comparisons require enabling the isolated observer after
review and waiting for Shadow rows; they remain mandatory PR/cutover blockers
and are not inferred as passing from the existing sync history.

## 2026-09-16 release preparation checkpoint

The previously local cycle-5 source was recovered intact. Its reviewed tree
`ad138a8fed886df8055e171139c1839542c546c3` is now durable on
`recovery/salla-v3-cycle5-20260916` at
`ddf0a861dd6a63335d922a7fcd2d7a3b66584846`. The recovered original commit was
`54e4a0ecfc12ab27bd203fe92bf104657202f0bb`; the remote checkpoint consolidates
its unpublished history, with identical source bytes and different commit
metadata. The candidate integrates Production source
`67de138c57fc3204ba4af529b14460e99b4dbf9f` without replacing other work.

Two restart/shutdown defects are fixed in this candidate:

- Optional observer initialization runs on every process startup, outside the
  once-per-release startup lease. Index creation still precedes task creation.
- Shutdown awaits observer cancellation before closing Mongo. Cancelling an
  in-flight provider call also drains its job heartbeat, leaving interrupted
  leases available for bounded recovery after expiry.

`Salla Orders V3 acceptance` runs all V3 contracts plus six real Mongo replica
set tests: concurrent duplicate delivery, rollback and repair after a failure
between writes, rollback after lease expiry, exclusive queue ownership and
supersession, tenant isolation, and expired-cursor replay after pending-intent
repair. These use synthetic events and a disposable
loopback-only MongoDB, with no Salla credentials. A skipped Mongo case fails the
CI gate. Local contract success alone does not prove transaction readiness.

GitHub acceptance run `35122217190` passed all 156 V3 tests, including all five
real-Mongo cases, on recovered HEAD `4870a255cb84c998aa513c13385f92eb7b2452a1`.
The delivery branch starts with the identical source tree directly on the
current Production parent: the old merge history was rejected by the v5
full-history intent check. No release guard was changed and no original branch
was force-updated. Exact delivery-HEAD CI is required before freezing its intent.

Deployment is staged: this source can only introduce the disabled observer.
The sole Shadow switch remains false by default and cutover remains unavailable.
Deploying this stage does not repair existing operational order records. Before
calling the whole order-sync replacement ready, complete real same-runtime
List/Details/Items evidence, trusted parity-artifact production, Qoyod and
attribution comparisons, and a separately reviewed operational adapter. Do not
turn on Shadow merely because unit tests pass.

Before any separately authorized deployment: require green acceptance and
release-readiness checks on the exact candidate; freeze a fresh protocol-v5
source-A/intent-only-B pair; complete the clean-clone rehearsal. The inherited
release intent belongs to an earlier release and must not be reused for this
candidate. The current delivery PR linked from Issue #1006 records the actual
CI and preparation state; #1049 preserves the earlier frozen candidate.

The pagination-session follow-up starts from Production
`76542622073bba1c61e87038cb855a72907484c7`, which includes the auth startup,
Snapchat token-reuse, and inline budget/bid releases. Those changes have no file
overlap with the Salla patch and are preserved. Seven deterministic regression
cases cover expired/invalid sessions, the fixed first-page deadline, late empty
responses, and BSON UTC decoding. A sixth real-Mongo case verifies repair of a
pending discovery intent before replaying the expired date window. The previous
source/intent pair is not rewritten or reused for this source.

## Rollback

Rollback is operationally small because there is no cutover:

1. Set `SALLA_ORDERS_V3_SHADOW_ENABLED=false` and restart the backend.
2. Existing order readers and writers continue unchanged.
3. Retain the isolated shadow collections for audit until their TTL expires, or
   remove them later under a separately approved data-retention operation.
4. Do not delete or alter current order, accounting, preparation, or attribution
   records.
