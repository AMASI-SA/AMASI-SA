# MZ2_SALLA_CURRENT_CARRIER — operational handoff

Operational implementation complete and verified. PR #1231 remains a Draft,
operational-only change. No merge, release, deployment, or Production change
was performed. Production financial writes performed by this task: **0**.

## Immutable source and scope

| Identity | Value |
| --- | --- |
| Repository | AMASI-SA/AMASI-SA |
| Task branch | fix/salla-current-carrier-20261001 |
| PR | https://github.com/AMASI-SA/AMASI-SA/pull/1231 |
| Production comparison baseline | 901568ccaaf510dc1f84d9c28f38d368d07dc64d |
| Baseline TREE | eeb12440116fe3b77da9bbe5687f550aff424c82 |
| Verified implementation HEAD | 6507c813a73ea71a5eab2a8d39c5bb92a4075c70 |
| Verified implementation TREE | cb9507cbf79ce401a3381c3311355c0cd3effe05 |

This handoff checkpoint changes this document only. The final checkpoint
HEAD/TREE, exact-SHA CI read-back, and next action are recorded in the PR and
the canonical continuation ledger, Issue #1006. Runtime source is unchanged
from the verified implementation above.

## Completed operational behavior

- Current carrier observations from Salla update the canonical order shipping
  group independently of Make's fill-empty policy. Sparse responses preserve
  valid current facts; a carrier/shipment replacement clears obsolete AWB,
  label, carrier code, and logo rather than carrying them into the replacement.
- Carrier/order and shipment metadata have independent provider clocks. Older
  concurrent intake, old cancellations, return shipments, and known superseded
  shipment IDs cannot restore the previous carrier or label. An update to an
  unknown old shipment cannot establish a replacement carrier; current order
  identity or a qualifying creation observation can.
- Existing restricted operational transaction infrastructure is reused
  unchanged. Intake uses owner serialization and CAS, including concurrent
  first intake. Shipping intake works while financial writes are paused;
  standalone Mongo fails closed without a partial order update.
- Order Engine detail and list project the same canonical shipping group.
  Shipment identity reaches the DTO. Detail polling reads local Mezan data
  every 3 seconds; list polling reads every 10 seconds. Request ownership
  prevents an old response from overwriting another order/current request.
- Label verification and issuance capture identity before provider I/O and
  revalidate carrier, shipment, status, superseded IDs, and clocks inside
  serialization. CAS guards both roots and metadata. Stale results fail with
  `shipping_snapshot_changed` / HTTP 409 before persistence or printing.
- A confirmed fresh POST replacement can be accepted before its webhook.
  GET-only responses with an unresolved different shipment ID wait for current
  canonical confirmation. Cached labels and late print responses are invalid
  after replacement/cancellation; archived/stale shipments cannot supply the
  current order's printable label. Local verification time is never substituted
  for a provider clock.

## Accounting boundary and separate integration note

No accounting writer, ledger, journal, accounting evidence, fee calculation,
accounting rate, write-control, or cutover file is changed. No accounting guard
is implemented by this PR. No historical financial record is rewritten.

**Separate MZ2 Accounting Integration requirement, documented only here:**
`prepare_courier_fee` and every accounting fee path must not treat old
`mz2_salla_order_evidence` as valid fee evidence when the order's current carrier
has changed. A current-carrier/evidence guard belongs in a separate Accounting
Integration change using its latest authorized writer. This operational PR
does not establish that stale evidence is financially safe and does not solve
the financial problem.

## Exact changed files versus the baseline

```text
backend/order_engine/mapper.py
backend/order_engine/models.py
backend/order_engine/repository.py
backend/order_engine/salla_refresh.py
backend/order_engine/shipping_label_service.py
backend/orders_db.py
backend/salla_integration/sync.py
backend/salla_integration/webhook_order_sync.py
backend/salla_shipping.py
backend/tests/test_g47_current_shipping.py
backend/tests/test_order_engine_salla_refresh.py
backend/tests/test_salla_current_shipping.py
backend/tests/test_shipping_label_current_guard.py
docs/operations/SALLA-CURRENT-CARRIER-20261001/STATUS.md
frontend/src/hooks/useOrders.js
frontend/src/hooks/useOrders.shippingRefresh.test.jsx
frontend/src/pages/OrderDetailsV2.jsx
frontend/src/pages/OrderDetailsV2.shippingIdentity.test.jsx
```

18 files total: 11 runtime files, 6 focused test files, and this document.
No CI workflow, dependency, release, or accounting file changed.

## Accounting unchanged proof

Compared Git blob IDs for all tracked backend/frontend/scripts/release files
matching the protected accounting/financial/ledger/journal/write-control/
cutover/rate/fee/payment/COD/balance/settlement/receivable/bank scope, plus
`backend/operational_atomic.py` and `backend/shipping_companies.py`.

- Protected files compared: **338**.
- Protected files with different/missing/added blobs: **0**.
- SHA-256 of the canonical sorted `{path, blob}` baseline manifest:
  `f51103ae63b79d4e296f064ca8458992c551b607fcc1b496d38eae02ba4774ea`.
- The candidate has the same manifest hash. The final documentation checkpoint
  cannot change these blobs because its sole delta is this document.

Reproduce from the repository root, with HEAD at the operational checkpoint:

```python
import hashlib, json, re, subprocess

baseline = '901568ccaaf510dc1f84d9c28f38d368d07dc64d'
def blobs(ref):
    result = {}
    for row in subprocess.check_output(
        ['git', 'ls-tree', '-r', ref], text=True
    ).splitlines():
        meta, path = row.split('\t', 1)
        result[path] = meta.split()[2]
    return result

old, new = blobs(baseline), blobs('HEAD')
pattern = re.compile(
    r'accounting|ledger|journal|write[_-]?control|cutover|financial|'
    r'(^|[/_])(rates?|fees?|payment|cod|balances?|settlement|receivable|bank)([/_.-]|$)',
    re.I,
)
protected = sorted({
    p for p in old.keys() | new.keys()
    if p.startswith(('backend/', 'frontend/', 'scripts/', 'release/'))
    and pattern.search(p)
} | {'backend/operational_atomic.py', 'backend/shipping_companies.py'})
assert len(protected) == 338
assert all(old.get(p) == new.get(p) for p in protected)
manifest = [{'path': p, 'blob': old[p]} for p in protected]
print(hashlib.sha256(json.dumps(
    manifest, sort_keys=True, separators=(',', ':')
).encode()).hexdigest())
```

## Tests and CI evidence

Re-run on the exact verified implementation, 2026-10-01:

| Verification | Result |
| --- | --- |
| Affected backend selection below | 257 passed + 561 subtests passed; exit 0 |
| Shipping identity, polling and print frontend tests | 28 passed in 3 suites; exit 0 |
| Changed Python AST parsing | 13 files passed |
| `git diff --check` against baseline | exit 0 |
| Exact implementation HEAD CI | 19 success, 4 unrelated skipped, 0 failed/pending |
| G47 real isolated Mongo CI | 128 tests + 64 subtests passed; 0 skipped |

Backend command (isolated test dependencies supplied on PYTHONPATH):

```sh
PYTHONPATH=backend:backend/tests python -m pytest --noconftest \
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

Frontend selection: `useOrders.shippingRefresh.test.jsx`,
`OrderDetailsV2.shippingIdentity.test.jsx`, `storeCourierLabelPrint.test.js`.
Executed with isolated Jest 29, React 19, jsdom, Babel React/env, `--runInBand`,
`--no-cache`, and `--runTestsByPath`. No governed release build was made locally.

Exact-source CI: https://github.com/AMASI-SA/AMASI-SA/commit/6507c813a73ea71a5eab2a8d39c5bb92a4075c70/checks

Real-Mongo run: https://github.com/AMASI-SA/AMASI-SA/actions/runs/36889565329

Backend artifact ID: `11176111824`; ZIP SHA-256:
`0c223fc3455d4878372676d63392eb7d9b6f2ce0be3bdc07ebdf90a4b13f8bde`.
The downloaded `g47-backend.xml` has 192 testcase entries, zero errors,
failures, and skips. It explicitly records all three operational tests as
executed passes:

- `test_carrier_update_remains_operational_when_finance_is_paused`
- `test_concurrent_first_intake_keeps_latest_carrier`
- `test_standalone_rejects_current_carrier_write_without_partial_order`

The four skipped CI checks are unrelated manual redeployment/host rehearsal
and Snapchat settings checks. They are not counted as passes. No operational
Mongo test was skipped in CI. No local real-Mongo pass is claimed.

Previously identified attribution bridge (2) and Order Engine route (9) test
failures were reproduced on the unchanged baseline. Those collaborator
fixtures/AST expectations are outside this patch and are not acceptance
evidence for it.

## Limits and final handoff

A real changed-carrier Salla webhook has not been observed or made available.
Synthetic fixtures and real-Mongo serialization tests are verified; they do
not prove the provider's live payload or delivery latency. This change adds
no Salla shipping permission or Shipments API call. If Salla omits carrier
identity, the order/webhook cannot reveal that omitted fact. Polling refreshes
local Mezan data and does not guarantee immediate provider delivery.

No Production DB operation, backfill, financial write, `/app` change, release
intent, lease, Preview, merge, or publish was performed by this task. All tests
used isolated local/CI fixtures. Unrelated accounting and Final Integration
branches/PRs are preserved.

Next safe action: review this operational-only PR and its final exact-SHA
checks. If a real changed-carrier event becomes available through the authorized
event monitor, capture a redacted fixture and verify the same behavior before
release planning. Merge or Production deployment requires independent user
authorization. Accounting evidence eligibility remains a separate MZ2 task.
