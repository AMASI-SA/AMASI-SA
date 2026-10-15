# Android end-to-end operations extension — design, not activated

Owner priority: explain the employee's elapsed time, not only server time.
This PR implements backend phase 1. This document extends its design; no
Android/scanner code, permissions, transport, Ready logic or financial logic is
changed. No mobile telemetry ingestion endpoint is introduced in this phase.
Implementation of these mobile spans requires a separate review before rollout.

## Source seams and protected scope

Read-only local Android reference: `C:/Users/amasi/amasi-mobile`, HEAD
`ce94b5295b745c1a43b1fb74486d6e5a79bc1da2`. This is not proof of the installed APK.
Paths below are relative to its `frontend` directory:

* `src/features/assemblyShipping/AssemblyShippingStage.tsx` and `repository.ts`:
  Ready tap, request, response, reconciliation and UI confirmation.
* `src/features/assemblyShipping/carrierLabelPrint.ts` and
  `src/features/myProducts/supplierDispatchPrint.ts`: label preparation and
  device viewer handoff (opening intent is not proof that a PDF rendered).
* `src/features/preparationReceiving/PreparationReceivingStage.tsx` and
  `repository.ts`: piece resolution, receiving request and UI confirmation.
* `src/features/myProducts/SupplierReceivingFlow.tsx` and
  `SupplierReceivingNative.tsx`: verification/save lifecycle.
* `src/components/mobile/BarcodeCameraModal.tsx`: protected camera boundary.
  Observe existing public callbacks only after a separate RCA/scope review.
  If existing callbacks cannot distinguish camera-ready, detection and decode,
  mark those durations **unavailable**. Never infer decode time from camera open
  to an already decoded callback. No scanner library/config/thread/duplicate
  suppression changes are authorized by this design.

## Timing contract

Use Android monotonic elapsed time for native events, monotonic JS timestamps
within JS, and server monotonic duration within Python. Establish a measured
native/JS clock mapping before mixing their intervals; otherwise report them
separately. UTC is for coarse alignment only. Never subtract Android and server
wall clocks to claim one-way network latency.

For every sampled user action generate a random 128-bit operation correlation
ID; each HTTP attempt gets a distinct span ID and keeps the same parent operation
ID. Reconciliation links to that parent and has its own attempt/span. These IDs
are neither idempotency keys nor order/piece/invoice/session IDs. Do not replace
existing business request identifiers. IDs are permitted in sampled sanitized
traces only, never metric labels. Backend validates fixed length/hex, ignores
invalid/untrusted values and caps sampling independently of client input.
Return a validated correlation ID and optional allowlisted Server-Timing
durations. No trace context sent to Salla/carriers; keep provider spans local.
No synchronous telemetry export from transactions. Instrumenting the actual
commit boundary must distinguish commit attempt, retries and unknown outcome.

Backend phase1 histograms in this PR have **no correlation IDs or operation
spans yet**. They support diagnosis of shared resource pressure, not a claim
that a complete mobile trace currently exists.

## Span matrix

| Operation | Client milestones | Backend/local-provider milestones | Terminal classification |
|---|---|---|---|
| Page open | navigation start; first meaningful content; all required data loaded; committed render/next frame | each API request receive/response | loaded, error, timeout, cancelled, superseded |
| Assembly Ready | tap; request enqueued/sent; response; state committed to UI; confirmed Ready | receive; validation; local transaction; commit attempt/result; external work; response | committed+confirmed, committed+response lost, unknown, reconciled, rejected |
| Shipping | print tap; shipment lookup; label/PDF retrieval; viewer intent; viewer opened/first page if observable | Salla status; carrier issue/fetch; persistence | provider error, lookup failure, download timeout, viewer failure, unknown |
| Employee receiving | camera request; camera ready; barcode detected; valid decoded callback; piece resolved; receiving sent; UI updated | lookup; receiving validation/write/commit; confirmation | received, rejected, timeout, unknown, duplicate suppressed |
| Supplier invoice | verification start/end; save tap; response; UI saved | validation; save work; commit attempt/result; response | saved, validation failed, session_not_verified, unknown |

First content excludes skeleton/spinner. Fully loaded names the page's required
data contract, not every optional/background request. UI render duration is
data/state-ready to committed visible frame; it is not the remaining backend
time subtracted from total. Abort on navigation/unmount as cancelled/superseded;
a late response must not falsely confirm the new page. The instrumentation
observes this behavior; it must not change reconciliation or retry policy.

Camera startup = ready minus camera request. Detection = detection minus ready
only if detection callback exists. Decode = decoded minus detected only if both
exist. Time to first valid decode = valid decode minus camera ready (includes
aiming/lighting, not pure decoder CPU). Decode-to-confirm = receiving confirmation
minus valid decode; split local resolution, API round trip and UI update.
API = client send to response/failure, including transport. Backend = server
receive to response; provider spans are nested and may overlap. Do not sum
parallel spans. Network residual includes transport queueing and bridge overhead,
not a precise network-only measurement.

Scanner ratios use explicit denominators: successful scan sessions / camera
sessions with a scan attempt; decode failures / observable decode attempts;
duplicate suppression / decoded callbacks; retries / scan sessions. User
cancel/no visible code is separate from decode failure. Do not collect images,
frames, barcode text/length/hash, camera coordinates or scanner payloads.

## Aggregation, privacy and cost

100% bounded local counters/histograms for fixed operation/stage/outcome enums;
trace sampling initially 1%, at most 10 traces per app session and 20/day.
No mandatory error-trace override that bypasses the cap. p50/p95/p99 derive from
merged histogram buckets, never averages of device percentiles. Suppress p99
below 100 observations and show count/coverage. Camera detection/decode absent
from public API stays missing, not zero.

Aggregate dimensions: allowlisted operation, stage, app build (current and prior
two plus other), Android major band, and coarse low/mid/high performance tier
defined locally. No serial, Android ID, advertising ID, IP, employee/customer ID,
exact model/fingerprint or persistent installation ID. Do not group all dimensions
as a Cartesian product; use separate operation/build and operation/device-tier
views. Device tier is not individual device tracking. No raw exception text,
URLs, request/response bodies, invoice values or session IDs. Cohort publication
needs a minimum sample count; anonymous samples are not a count of unique devices.

Buffers <=64KiB RAM, no disk persistence initially; drop on overflow/background
or process death. Batch <=16KiB compressed only while foreground with an existing
network opportunity, no more than once/5min and <=128KiB/day/app session budget
(a restart resets a session budget; a real daily cap would require a separately
reviewed non-identifying persistent counter). Prefer explicit daily cap before
fleet rollout. Never wake camera/radio or add polling, permissions, retries or a
background service. Offline drops are counted locally; do not create retry storms.
Future ingest must reject >16KiB, enforce fixed schemas/rates and retain clean
traces <=7days/capped store; no Mongo business collections for telemetry.

## Acceptance and rollout gates

Before mobile implementation: establish actual APK/build and public callback
availability, scanner protected-scope RCA, and transport interception contract.
Test low/mid/high devices with camera cold/warm startup, poor light, invalid code,
duplicate scans, offline/reconnect, slow API, loop stall, app background/navigation,
late Ready response, successful commit with lost response, reconciliation,
session_not_verified and PDF viewer unavailable. Use synthetic codes/invoices.
Prove no changed permissions and no extra business requests or retries; record
CPU, memory, frame drops, transferred bytes and battery impact A/B with monitoring
off/on. Mobile overhead targets require measurement: <=1% additional CPU budget,
<=2MiB RSS, <=1% battery-energy delta over a controlled hour, no statistically
credible frame-time regression; otherwise LIMITATION/FAIL, not PASS.

Roll out later to a small approved cohort with remote disable already available
through an existing configuration mechanism, no new polling. If none exists,
rapid mobile disable is a prerequisite, not an assumed capability. Sampling can
be disabled independently of backend counters. Compare #1312 using actual server
SHA and mobile build cohorts, matching operation mix and data sizes. Better
backend lag is not proof of better camera/decode/UI latency.
