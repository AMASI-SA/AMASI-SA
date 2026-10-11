# Ready acknowledgement and durable completion delivery

Scope: AMASI-SA/AMASI-SA. Measured baseline `d55b1b86`; final PR base is the
newer production source `094d0ef7fa001d3b3794f2600c77795190ddb1a8` (unrelated TikTok
changes, rebased without touching its release intent). Draft only:
no merge, release intent, deployment, OTA, production request, or financial write.
PR #1305 was inspected for the positive `in_progress` ready gate and completed
current-label print boundary; its unrelated branch contents were not merged.

## Root cause and API

Previously `POST /preparation-work-v1/assembly/pieces/{piece_id}/ready` committed
the owner transaction, then awaited `sync_completed_carrier_label`. A slow or
failed provider operation could therefore make the HTTP request fail after the
piece and component consumption had committed. Last-piece latency included
provider status, polling and label work; ordinary pieces did not.

The same POST now returns the local transaction result immediately. The piece,
component consumption, progress, shipping batch and `assembly_delivery` outbox
are in one existing operational owner transaction. No provider IO occurs there.
An outbox insertion failure rolls back the entire local completion.

Additive response contract:

* `piece_ready_confirmed: true` proves local piece commit, not provider success.
* `progress.order_completed` proves local assembly completion.
* `order_completion_status: pending | confirmed` describes Salla confirmation.
* `label_status: pending | available` describes the last verified current label.

`GET /preparation-work-v1/assembly/orders/{order_number}/completion` reads the
durable operation and label snapshot without provider IO or writes. Existing
`GET /preparation-work-v1/assembly/search?q=...` resolves ambiguous piece POST
responses. A client must GET after a lost response; it must not automatically
repeat the ready POST. An idempotent legacy retry still revalidates components
and cannot consume twice or reset provider attempt markers.

`POST /preparation-work-v1/assembly/orders/{order_number}/completion/resume`
requires shipping-label permission and resumes only the committed order. The
existing Android-allowlisted `/fulfillment-v2/completed/{order_number}/carrier-label`
POST invokes exactly the same lease/operation. No Android repository or OTA was
changed; the new URL requires a future Android allowlist change if adopted.
The old Order Engine shipping-label route also delegates completed assembly to
this operation; direct issuance for enrolled workflows shares the same markers.

The existing carrier-label `/refresh` remains read-only and verifies live Salla
`completed`, current active shipment and valid label before printing. A cached
`available` response alone is not a print authorization. Local completion is
also required by the fulfillment print route. Ready search/save now positively
require Salla `in_progress` (including its Arabic name), not just absence of a
terminal status.

## Recovery and safety

The process-local worker is only a scheduler: the work, per-effect markers,
claim token, expiration, attempts, errors and due time reside in Mongo. Each
replica polls a bounded batch of four orders. Atomic claims prevent concurrent
manual/background work; 90-second attempts fit inside 120-second leases. A
restart resumes expired leases. Eight attempts bound automatic retries; an
expired final claim becomes `requires_attention`, never a stranded pending row.
Authorized manual reads/resumption remain available after that bound.

Every attempt reads the current Salla order and checks identity first. A status
POST marker is committed before a single transport attempt (auth replay and
redirect replay disabled). Lost responses or cancellation never clear it. A
later run reads Salla: completed advances; unconfirmed does not repeat the POST.
After confirmed completion, the current label is read. A current, explicitly
`draft`, untracked shipment can receive one first AWB POST with its own durable
marker, after rechecking source/components. Pending/tracked/unknown shipments
are read only. The same run that changes status does not create an AWB, allowing
Salla's status-triggered issuance to settle first. AWB POST responses are never
accepted as print proof; current label refresh must succeed.

Before either effect, an operational transaction checks material workflow evidence,
claim/lease, stable source fingerprint and current component execution evidence,
then records that effect's marker atomically. Generated provider tracking/status
metadata is excluded from the fingerprint; material items, recipient/address,
carrier identity, payment evidence and source-refresh blockers are retained.
Uncertain network outcomes favor safety over automatic liveness. A crash after
the marker but before dispatch may require owner reconciliation in Salla; this
code deliberately offers no force-reset/repost endpoint.

Historical completed workflows without an outbox cannot prove that an older
provider write was never delivered. Manual enrollment and idempotent ready
enrollment are conservatively readback-only, including prior store-courier
attempts. They can recover an existing completed status/current label but never
replay an uncertain historical status or AWB POST.

## Eligibility and performance choices

Search now pre-evaluates piece instructions for the current actor. Save retains
fresh backend permission/responsibility reads inside the owner transaction,
piece identity/idempotency, current source/component gates and instruction
checks. The authenticated dependency remains responsible for token/account and
trusted Android principal validation; Android button state is not authority.

`_assembly_progress` reuses the already validated OrderDTO from the same owner
transaction, eliminating a duplicate order load and full eligibility pass.
It retains the final fulfillment decision rebuild. Existing approval proof has
no cohesive revision covering catalog, instruction, inventory and all source
writers, so cross-request cached eligibility or classification would be unsafe.
The requested fully shortened evidence-token save path is therefore not claimed
as implemented. The known status-only canonical writer concurrency gap tracked
separately in PR #1300 is not fixed by this change. No unproven safety check was
removed, and financial calculations/ledger/history/inventory policy are unchanged.

## Evidence

`PERFORMANCE.json` contains actual local ASGI/Mongo timings with synthetic
products and a real awaited 20-second provider delay. The baseline ready module
is loaded from Git `d55b1b86`; measurements are not production latency claims.
Ordinary ready: 144.6 ms before, 145.2 ms after. Last piece: 20,192.0 ms before,
108.8 ms after. Provider calls on ready: one before, zero after. Aggregate
progress time for two requests: 138.6 ms before, 42.2 ms after; its included
decision time: 61.5 ms before, 23.3 ms after. One run per scenario; these figures
are mechanism evidence, not a percentile or load-capacity claim.

Reproduce with a disposable local replica set only, `PYTHON_DOTENV_DISABLED=1`,
`PYTHONPATH` including `backend` and `backend/tests`, and `MZ2_TEST_MONGO_URI`:

```
python -m pytest --noconftest -q backend/tests/test_assembly_completion_delivery.py
python backend/tests/benchmark_assembly_delivery.py docs/operations/READY-DURABLE-DELIVERY/PERFORMANCE.json
```

Tests exercise real operational transactions, stock balances, concurrent ready,
lost-response GET recovery, revoked role assignment, rollback when outbox
creation fails, repeated manual completion, one-shot AWB recovery, interrupted
worker/new-client recovery, exhausted leases, material-source invalidation and
expected generated-metadata changes. Provider transport is synthetic; live
Salla integration and a native Android binary run were deliberately not performed.

Recorded verification: delivery/current-print/carrier suites: 277 passed in
107.40 s; final Order Engine/fulfillment HTTP print routes: 8 passed in 4.50 s;
component lifecycle suite: 19 passed in 102.66 s. The coordinator independently
reran the ten durable-delivery cases (76.95 s) and both frontend suites (74 passed,
14.444 s). Legacy async fixture suites require `--asyncio-mode=auto` and
`BUILD37_TEST_MONGO_URI` pointing to the same disposable loopback replica set.
CI includes the durable module/tests in the existing real-Mongo review workflow
and asserts execution of all nine asynchronous delivery scenarios.

Assembly/cross-task regressions: 57 passed plus 70 subtests in 508.47 s.

## Independent CI correction

The original PR head failed six CI workflows and was not accepted. See [CI-CORRECTION.md](CI-CORRECTION.md) for individual causes, historical reprint policy, batched search measurements, workflow revision interleavings and the final-head CI gate. Prior local evidence alone is not final acceptance.
