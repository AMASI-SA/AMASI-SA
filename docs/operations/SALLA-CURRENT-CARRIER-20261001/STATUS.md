# MZ2_SALLA_CURRENT_CARRIER — operational handoff

Operational implementation preserved and re-established on the current
Production source, 2026-10-02. Fresh focused tests pass. The exact final
checkpoint CI and real-Mongo results are recorded in the successor Draft PR
and Issue #1006 after remote read-back; prior-source CI is not new-source
evidence. Original PR #1231 and its branch remain unchanged and Draft.
No merge, release, deployment, or Production change was performed.
Production financial writes performed by this task: **0**.

## Immutable source and scope

| Identity | Value |
| --- | --- |
| Repository | AMASI-SA/AMASI-SA |
| Final task branch | fix/salla-current-carrier-prod-20261002 |
| Original PR, preserved | https://github.com/AMASI-SA/AMASI-SA/pull/1231 |
| Successor review record | Draft PR linked from #1231 and Issue #1006 |
| Production comparison baseline | 83363097d48e034dc7140a60c290efc684e1ffde |
| Baseline TREE | 1053db45d38aac9092a2cf55989cb6349fceffcd |
| Original operational HEAD, preserved | 46e4d0bf8aa2e7d8da921e8126509b0f69c251d1 |
| Original operational TREE | f402dbcf827437e47fdb690a2384b7e7b8b91b1c |

The new source commit has the current Production commit as its direct parent.
Its net operational patch comes from #1231's final reviewed diff against the
previous base, 901568ccaaf510dc1f84d9c28f38d368d07dc64d. No intermediate
accounting candidate commit was replayed. No force push was used.
Final HEAD/TREE and exact-HEAD checks are recorded externally in the Draft PR
and Issue #1006 to avoid a self-referential tracked commit identity.

## Re-establishment and conflicts

Production introduced 11 changed files since the previous base. None overlaps
the 18-file operational patch. `git apply --check --index` and the actual
indexed application passed without a conflict or manual source resolution.
All 17 runtime/test files retain exactly the original operational Git blob
IDs; only this report changes for the new base and handoff. All 3,106 existing
baseline files outside the operational allowlist are identical, including
every newly delivered driver/COD/evidence file and the release intent.

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

Compared Git blob IDs against the new Production base for all tracked
backend/frontend/scripts/release files matching the protected accounting/
financial/ledger/journal/write-control/cutover/opening/activation/rate/fee/
payment/COD/balance/settlement/receivable/bank scope, plus
`backend/operational_atomic.py`, `backend/shipping_companies.py`, and
`release/release-intent-v5.json`. The full non-allowlist comparison additionally
protects files regardless of their names.

- Protected files compared: **344**.
- Protected files with different/missing/added blobs: **0**.
- SHA-256 of the canonical sorted `{path, blob}` baseline manifest:
  `79d5a5d4a6d6b6053b0d81792c94a4be4603933bb83f1c515482164c9e652533`.
- The candidate has the same manifest hash. No financial source method or
  imported accounting evidence is adapted by this operational patch.

Reproduce from the repository root, with HEAD at the operational checkpoint:

```python
import hashlib, json, re, subprocess

baseline = '83363097d48e034dc7140a60c290efc684e1ffde'
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
    r'accounting|ledger|journal|write[_-]?control|cutover|financial|opening|activation|'
    r'(^|[/_])(rates?|fees?|payment|cod|balances?|settlement|receivable|bank)([/_.-]|$)',
    re.I,
)
protected = sorted({
    p for p in old.keys() | new.keys()
    if p.startswith(('backend/', 'frontend/', 'scripts/', 'release/'))
    and pattern.search(p)
} | {'backend/operational_atomic.py', 'backend/shipping_companies.py',
     'release/release-intent-v5.json'})
assert len(protected) == 344
assert all(old.get(p) == new.get(p) for p in protected)
manifest = [{'path': p, 'blob': old[p]} for p in protected]
print(hashlib.sha256(json.dumps(
    manifest, sort_keys=True, separators=(',', ':')
).encode()).hexdigest())
```

## Tests and CI evidence

Fresh execution on the rebased runtime/test source, 2026-10-02:

| Verification | Result |
| --- | --- |
| Affected backend selection below | 257 passed + 561 subtests passed; exit 0 |
| Shipping identity, polling and print frontend tests | 28 passed in 3 suites; exit 0 |
| Changed Python AST parsing | 13 files passed |
| `git diff --check` against baseline | exit 0 |
| New Production delivery compatibility | 51 passed in five unchanged test files; exit 0 |
| Exact final HEAD CI | Final read-back and job links in successor PR / Issue #1006 |
| Real isolated Mongo on new source | Final G47 run, artifact hash and executed test names in successor PR / Issue #1006 |

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

Additional unchanged current-Production delivery tests:

```sh
PYTHONPATH=backend:backend/tests python -m pytest --noconftest \
  backend/tests/test_mobile_app_request_context.py \
  backend/tests/test_store_courier_dispatch.py \
  backend/tests/test_store_delivery_driver_security.py \
  backend/tests/test_store_delivery_handover_routes.py \
  backend/tests/test_store_delivery_payment_evidence.py \
  -q -p no:cacheprovider --asyncio-mode=auto --tb=short
```

The unchanged G47 workflow selects `test_g47*.py` and runs against disposable
Mongo 8.0.12 replica/standalone instances bound to loopback in CI. Its XML gate
requires executed cases with zero skips, failures, or errors. Final acceptance
must explicitly confirm execution of the three preserved operational tests:

- `test_carrier_update_remains_operational_when_finance_is_paused`
- `test_concurrent_first_intake_keeps_latest_carrier`
- `test_standalone_rejects_current_carrier_write_without_partial_order`

Historical source #1231 passed 19 CI checks with 4 unrelated skips and all
three real-Mongo operational cases. Those prior results are provenance only
and are not a substitute for final exact-HEAD CI/Mongo execution. No local
real-Mongo pass is claimed. CI skip/failure details, if any, must remain visible
in the final handoff.

Previously identified attribution bridge (2) and Order Engine route (9) test
failures were reproduced on the previous unchanged baseline. Those collaborator
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

Next safe action after final exact-HEAD CI/Mongo read-back: stop for review
of the new operational-only Draft PR. If a real changed-carrier event becomes available through the authorized
event monitor, capture a redacted fixture and verify the same behavior before
release planning. Merge or Production deployment requires independent user
authorization. Accounting evidence eligibility remains a separate MZ2 task.
