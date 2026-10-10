# Supplier scan outcome contract

Backend and Android changes are separate Draft PRs. No production rollout,
APK, OTA, merge or restart is authorized by this work.

## One immutable attempt

`POST /supplier-receiving-v1/sessions/{session_id}/scan`

```json
{
  "barcode": "MEZAN-PIECE:<physical-piece-id>",
  "client_request_id": "<persisted-unique-attempt-id>",
  "client_expires_at": "2026-10-11T12:02:00Z",
  "quantity": 1,
  "confirm_supplier_reassignment": false
}
```

Persist barcode, quantity, reassignment decision and ID before network work.
Persist the send intent and `client_expires_at` at first dispatch, not capture:
Android uses device time +120 seconds. POST exactly once; preserve
`authRetry:false`. Barcode capture may continue while that attempt is pending.
Do not change a sent attempt's body or deadline. Reusing an ID with changed
body/deadline returns `supplier_receiving_scan_request_conflict`.

Deadlines must be timezone aware, no more than five minutes ahead of server
time, and accompanied by an ID. Invalid device time can produce a definitive
`supplier_receiving_scan_deadline_invalid` rejection. Legacy clients can omit
the new deadline and retain their old ID contract. They cannot conclusively
recover a request which never reached the server from absence alone.

## Read-only reconciliation

`GET /supplier-receiving-v1/sessions/{session_id}/scan-requests/{client_request_id}?client_expires_at=<same-encoded-ISO-deadline>`

Authorization is the same tenant/employee/session ownership check as scanning.
GET never inserts a request, acquires a lock, repairs data or replays POST.
Existing response fields remain; the following additive fields are authoritative:

| outcome | Meaning | Client action |
| --- | --- | --- |
| `confirmed` | Exact receipt events and physical piece links (or finalized invoice history) prove receipt | Count/merge exact piece IDs once, then send next queued attempt |
| `in_progress` | Durable accepted attempt has not settled | Show «بانتظار تأكيد الاستلام», retain queue, repeat GET |
| `unresolved` | Incomplete/inconsistent proof, or absent legacy request without deadline | Retain it, repeat GET; never count or automatically POST it again |
| `rejected` | Persisted rejection, explicit cancellation, quantity preview, or expired absent immutable request | Retain visible rejection/decision; proceed to next queued entry; explicit new attempt only after user action |

`committed:true` accompanies confirmed results. `retry_after_ms:1500` is returned
for nonterminal results. The existing `found` field describes receipt evidence,
not request admission: `found:false` can accompany `in_progress`.
`rejection` carries the code/details for a recorded rejection. Cancellation uses
`cancelled:true`, including partial removal of an original quantity group.
Do not count surviving partial-group `scans` as a fully confirmed original attempt.

Quantity discovery remains `requires_quantity_selection:true` with the existing
top-level product/quantity fields. It creates no receipt and has
`outcome:rejected, committed:false`. An explicit quantity selection creates a new
attempt. Supplier reassignment still needs explicit confirmation and a new body.

Same-session physical-piece re-scans return the requested attempt ID and exact
existing event identities, without incrementing count. Recovery remains valid
after invoice approval through the piece's matching session/invoice history.

## Failure and fencing rules

Admission atomically stores an attempt and its session lease. Receipt pieces,
counter, events, outcome and lease release then commit together. Execution is
bounded to 45 seconds; the 120-second lease is unchanged. A five-second startup
worker sweep fences expired durable attempts by a transaction on the same
request document. A stale writer cannot subsequently commit. Multiple replicas
may sweep safely. Cancellation, timeout and unknown commit acknowledgements
settle the request without compensating/deleting a committed receipt.

Deadline checks use the Mongo primary clock (`hello.localTime`/`$$NOW`), not application-worker clocks. Recovery and transactions explicitly use PRIMARY.

An absent request is rejected by GET only when the read **starts after** its
immutable deadline. A POST arriving or completing admission later cannot start
receipt execution. If admission is already visible, its durable state takes
precedence over the deadline; never infer failure from age alone.

Old pre-change orphan locks have no new attempt record; existing lease expiry
allows a new transaction to acquire them. No recovery claim is made for partial
historical writes: incomplete receipt evidence stays unresolved and cannot be
silently invoiced. A future owner-approved rollout must avoid concurrent old
writers, which lack the new transaction fence.

## Android Build44 handoff

The active flow uses `SupplierReceivingCompactNative.tsx`; the older Native
screen must share the same queue contract. Use existing AsyncStorage; key by
API origin, authenticated employee/account and session. Persist send intent before
POST and restore sent entries with GET only. Reconcile on foreground/reconnect
and bounded polling. Scanner close/unmount must not own or delete the queue.

Pause dispatch during invoice review/cancel. Approval requires a settled local
queue **and** existing fresh Backend quantity/service/price/signature and financial
integrity checks. Backend refresh/approval reject pending attempts in their
transaction. Whole-session cancellation is atomic and rejects pending attempts;
it does not erase an approved financial invoice. Surface terminal rejected items
for explicit correction/exclusion, never silently discard them.

Android Draft PR: https://github.com/AMASI-SA/amasi-mobile/pull/265

Independent Android source base: Build43 baseline
`54b0ecc9daba92769b128c3d2d47cae42fac2f7e`. Do not edit the blocked Build44 security
branches. Build44's owner can review/adapt the separate patch after its security
gates. JavaScript tests do not establish camera/device/process-death acceptance.
