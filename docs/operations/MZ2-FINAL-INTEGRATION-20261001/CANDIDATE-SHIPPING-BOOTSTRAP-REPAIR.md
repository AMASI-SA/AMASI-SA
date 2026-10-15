# Candidate shipping event bootstrap repair

Scope: C:/Users/amasi/mz2-final-integration-reviewed-20261001; synthetic replica-set databases only. No commit, production action, financial writer, atomic core, guard, or HTTP retry changed.

## Failure before

Original focused root run: 60 passed / 1 failed. `test_mz2_driver_payment_review.py::test_concurrent_accept_one_journal` returned `driver_payment_review_not_pending` for an identical concurrent approval (`candidate-root-driver.log`).

Read-only tracing reproduced twice: diagnostic round 8; full Mongo-command diagnostic round 3 (`driver-race-failure.json`, `driver-race-command-failure.json`). The failed session's transaction14 used snapshot readConcern and autocommit=false throughout. It read an approved review with revision1 and the exact expected event pointer, then the same-key event query returned an empty firstBatch. The events collection did not exist before or after COD recognition; the first approval created it transactionally (`driver-collection-presence.log`, Mongo8.0.12). Session wrapping and deterministic keys were intact.

Controlled experiment: provision only the empty events collection before reviews; 40 fresh databases gave 40 posted + 120 already_posted, no errors (`driver-race-precreated.log`). This identifies the missing schema-bootstrap boundary; the Mongo engine's internal catalog behavior was not independently audited.

## Repair

- accounting_shipping_native_setup.py: `ensure_shipping_native_indexes` provisions existing EVENTS using an idempotent owner/kind index, outside financial transactions. Errors propagate.
- financial_provider_apps.py: existing public startup initializer calls the domain initializer.
- test_mz2_shipping_native.py: shared functional fixture mirrors initialization and retains its NoLegacy command monitor. New real-Mongo tests verify repeat initialization preserves native data and public startup creates empty events, changes no paused/draft controls or other financial data, and preserves existing events/indexes.
- test_accounting_ledger_v2.py: expected bootstrap sequence includes shipping while preserving every previous call.
- Original concurrent acceptance test unchanged.

## Fresh verification

Environment: PYTHONPATH=backend;backend/tests, PYTHON_DOTENV_DISABLED=1, MZ2_TEST_MONGO_URI=mongodb://127.0.0.1:27128/?replicaSet=mz2test. Python: C:/Users/amasi/mz2-final-integration-owner-20261001/.venv/Scripts/python.exe.

Command: python -m pytest -q --tb=short backend/tests/test_mz2_shipping_native.py::test_shipping_schema_reinitialization_preserves_native_state backend/tests/test_mz2_shipping_native.py::test_production_bootstrap_provisions_empty_shipping_events_without_activation backend/tests/test_accounting_ledger_v2.py::test_startup_index_bootstrap_preserves_legacy_then_installs_v2 backend/tests/test_mz2_driver_payment_review.py::test_concurrent_accept_one_journal

Exit0; 4 passed in3.27s (`candidate-shipping-bootstrap-focused.log`).

Command: python C:/Users/amasi/mz2-final-integration-evidence-20261001/driver-race-diagnostic.py

Actual patched shared fixture; no explicit extra precreation in diagnostic. Exit0; 20 fresh databases, 80 concurrent requests: 20 posted + 60 already_posted; no errors (`candidate-shipping-bootstrap-concurrency-after.log`). This bounded diagnostic complements, rather than replaces, the unchanged assertion-based test.

`git diff --check`: exit0. Durable incremental patch: `candidate-shipping-bootstrap-repair.patch`. Root owns combined regression and release decisions; this evidence alone is not release readiness.

Root combined verification:108 passed in67.93s, exit0. Full driver-review, native shipping, driver security and startup wiring suites. Root log/XML are committed in evidence/governance/candidate-root-initializer.*. No remaining failure in this focused selection. Broad131-file regression and exact-source CI follow on the immutable source checkpoint.
