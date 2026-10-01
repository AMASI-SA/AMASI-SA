# Salla current carrier synchronization

State: operational-only source candidate after explicit user scope correction.
Local validation passed; revised Draft PR CI pending. No merge or deployment.

The user explicitly required this fix to stay outside accounting. The prior
candidate's P02 current-carrier review and public accounting error codes have
been removed. Every accounting production file now matches the unchanged
Production baseline byte for byte. No accounting writer, posting, fee
eligibility, rate, balance, journal, cutover, or financial control is changed.
This fix updates current operational shipping data and label safety only;
it does not add a guard against stale imported fee evidence or correct existing
financial records. Those accounting behaviors remain exactly as delivered.

Repository: AMASI-SA/AMASI-SA
Task branch: fix/salla-current-carrier-20261001
Production source baseline: 901568ccaaf510dc1f84d9c28f38d368d07dc64d
Baseline tree: eeb12440116fe3b77da9bbe5687f550aff424c82

Current Salla carrier observations update the canonical order shipping group
independently of Make's fill-empty policy. Sparse responses preserve current
facts; obsolete shipments, return shipments, cancellation of an older shipment,
and late concurrent intake cannot restore the old carrier or its label. Order
Engine detail/list projections use the canonical group. Shipment IDs reach the
DTO so cached labels and late printing responses cannot cross replacements.

Existing restricted operational transaction infrastructure is reused without
modification. It allows shipping intake while financial writes are paused and
does not permit a financial writer to run within this shipping transaction.

Carrier/order and shipment metadata clocks are independent. Newer sparse order
envelopes do not suppress current-shipment updates, and shipment delivery clocks
do not suppress a later carrier assignment. Updates of an unknown old shipment
cannot establish a replacement carrier; creation or order identity can.

Label verification/issuance captures canonical identity before provider I/O,
validates it inside owner serialization, and CAS-fences both metadata and roots.
Stale results return shipping_snapshot_changed/409 before printing. A confirmed
fresh POST replacement is accepted before its webhook; a GET-only unresolved
different ID waits for canonical confirmation. Structured carrier identities,
provider clocks and AWB aliases stay consistent. Replacements without AWB/PDF
clear the previous label. Verification time is local evidence, never a provider
clock. All known superseded IDs remain fenced.

Fresh validation of this narrowed candidate:
- Backend affected selection: 257 passed, 561 subtests passed; exit 0.
- Frontend shipping/polling/printing: 28 passed in 3 suites; exit 0.
- git diff --check: exit 0; all 13 changed Python files parse.
- Final source diff has zero accounting, ledger, rate or financial-control
  files; accounting files and operational infrastructure match the baseline.

Previous candidate 95d6d7d783fabd5257e7dd8efb206f5e443aa040 passed all
19 applicable CI checks. Those results do not establish the revised candidate's
final CI status. Fresh exact-SHA CI must be inspected after the checkpoint.

Commands (run from repository root, with isolated test dependencies on PYTHONPATH):

```sh
PYTHONPATH=backend:backend/tests python -m pytest \
  backend/tests/test_salla_current_shipping.py \
  backend/tests/test_order_engine_mapper.py \
  backend/tests/test_order_engine_repository.py \
  backend/tests/test_order_engine_salla_refresh.py \
  backend/tests/test_salla_raw_snapshot_preservation.py \
  backend/tests/test_salla_resync_raw_shape.py \
  backend/tests/test_order_engine_models.py \
  backend/tests/test_order_engine_service.py \
  backend/tests/test_order_engine_cod_fee_source.py \
  backend/tests/test_shipping_cost_ssot.py \
  backend/tests/test_salla_single_order_status_resync.py \
  backend/tests/test_mz2_shipping_calculator.py \
  backend/tests/test_mz2_shipping_contracts.py \
  backend/tests/test_mz2_shipping_contract_isolation.py \
  backend/tests/test_mz2_shipping_payment_evidence.py \
  backend/tests/test_mz2_legacy_shipping_gate.py::LegacyShippingGateUnitTests \
  backend/tests/test_shipping_label_current_guard.py \
  backend/tests/test_fulfillment_carrier_label.py \
  backend/tests/test_recipient_delivery_projection.py \
  -q -p no:cacheprovider --asyncio-mode=auto --tb=short
```

Frontend was rendered/tested with Jest 29, React 19, jsdom and Babel React/env
presets, selecting useOrders.shippingRefresh.test.jsx,
OrderDetailsV2.shippingIdentity.test.jsx and storeCourierLabelPrint.test.js.
No governed release build was produced locally.

The broader pre-existing attribution bridge tests (2) and Order Engine route
tests (9) also fail on exact unchanged Production baseline; confirmed with an
isolated archive. Their stale collaborator fixtures/AST assertions are outside
this carrier patch. Mongo-dependent migrations and /app-dependent source tests
were not used as local acceptance evidence.

Validation limits: Mongo mock tests exercise intake/CAS/read projection, not
real replica-set transaction serialization. No transactional
Mongo URI is available locally. The new test_g47_current_shipping.py runs under
the existing G47 CI against real isolated loopback replica/standalone fixtures;
3 cases skip locally and are not counted as passes. The accounting fee-preparation
case was removed with the accounting guard, since that behavior is out of scope.
Live changed-carrier webhook payload has not been
observed. Available order/webhook permissions cannot reveal a company identity
that Salla omits; this change adds no shipping permission or Shipments API calls.
Detail polling reads local Mezan data every 3 seconds; list polling stays at
10 seconds. No guaranteed zero-latency Salla event delivery is claimed.

Unrelated accounting integration PRs #1229 and #1230 and their release workflow
are preserved. This shipping candidate is separate and does not merge,
rewrite, activate, or publish their accounting work.
Production changed: no. No /app changes, backfill, release intent, frontend
release artifact, lease, merge, or publication was performed.

Previous verified remote checkpoint: 95d6d7d783fabd5257e7dd8efb206f5e443aa040.
The exact candidate SHA, PR and CI state are recorded in Issue #1006 after
remote read-back. Next safe action: inspect candidate CI, especially G47's
no-skip transaction gate, then verify a real changed-carrier payload through
the authorized event monitor before release planning. Do not deploy this
candidate or bypass omitted provider identity.
