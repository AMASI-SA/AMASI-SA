# Shipping eligibility from the dated order status

Base: 845f75599ecdaedf608b082f4265a676023a7b62 (PR #1828). PR #1831 is not included. Both earlier heads remain untouched.

## Approved contract
The user's latest instruction is binding: deferred carriers/drivers earn on delivered, prepaid earns on completed, with the actual event date after the saved operational start. A waybill, tracking number, receipt, extra label or negative proof of never having completed is not an additional earning condition. No updated_at date is invented as an event date.

## Changes
- Prepaid source accepts completed + explicit dated status/history with the approved tariff/cash binding even without logistics labels; the existing order-scoped expense/payment IDs remain unchanged.
- Deferred source accepts delivered + its actual dated status with approved carrier identity without requiring an original label or requiring label creation before delivery.
- Canonical order shipping identity can supply the carrier when the normalized shipment is absent.
- Logistics fields remain optional metadata, but conflicting present metadata remains a blocker: mismatching carrier/AWB, ambiguous split/replaced shipment, malformed metadata, owner/order/time conflicts and duplicate shared shipment are not ignored.
- Native COD collection evidence remains separate; earning a fee without a label does not recognize customer cash.
- New unlabeled deferred facts use order+carrier identity, with an explicit `shipping_identity_source=order_carrier` marker. Later label enrichment retains that fact's ID/metadata. Existing labeled facts/obligation IDs are not migrated or rewritten. Losing their label holds the changed source for review while preserving booked amounts.

## Tests
Seven intended failures on the unchanged parent demonstrate absent-label/absent-normalized-shipment gaps. New suite also covers old/new creation, actual event before start/missing/future, once-only prepaid 34.50, later delivered state, label enrichment, existing fact preservation when labels disappear, changed carrier review and conflicting prepaid metadata.
Only assertions whose requirement explicitly changed were updated in existing suites: no AWB, label created later, absent normalized label when canonical carrier is known, and absent duplicate raw label. Existing split/superseded/carrier/waybill conflicts still reject.

Run the 13 shipping/courier suites plus test_operational_shipping_status_eligibility.py using PYTHONPATH=backend:backend/tests, PYTHONDONTWRITEBYTECODE=1, PYTHON_DOTENV_DISABLED=1, `python -m pytest --noconftest ... -q --tb=short`. Integration requires a private localhost Mongo replica set via OPERATIONAL_TEST_MONGO_URI and MZ2_TEST_MONGO_URI; used port 27433. Initial Mongo startup failed because the environment requires --nounixsocket and the process must remain in the test wrapper; corrected without changing tests. No production environment was read.

## Release boundary
No /app edits, tariff approvals, opening changes, API/UI changes, manual financial backfill, merge, release intent or deployment. Before any historical reprocessing, root must establish the affected live orders and amounts in a read-only impact preview and verify no previously recognized duplicate expense. Tests prove synthetic behavior, not live Aramex completion.

Final verification: **529 passed**, zero skips/failures, three existing warnings, 13.94 seconds. `git diff --check` passed. All final source files restored after the deliberate parent regression run.
