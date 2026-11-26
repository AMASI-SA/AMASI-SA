# Private payment-normalization read view

The collector opts into a synchronous internal read view only after the existing
payment/evidence admission checks. The public `settled_payment_snapshot` keeps
its original fully independent `deepcopy` result. The old admission/overlay body
is shared without changing its statements or financial rules.

The private view may borrow only root `items`, `customer`, `shipping` and
`shipments`, which neither the pure payment normalizer nor `order_fee_rail`
reads. Every other root is deep-copied with one memo, preserving aliases and
cycles. A borrowed branch also reachable through financial evidence uses that
independent financial copy. Unsupported/custom shapes keep the public full-copy
path. No borrowed input is returned in the constructed financial observation.

## Verification

The new 45-case suite passed in 1.08 seconds. Cases cover unchanged public-copy
behavior, complete fields, aliases/root cycles, copied financial evidence,
malformed/conflicting or ineligible evidence, custom/non-JSON fallbacks, output
mutation isolation and removal of 256 unnecessary opaque-item copies. Independent
static review traced both consumers, compared the refactored admission body by
AST against the original, and found no blocker.

A wider local payment/source-contract run produced **430 passed and 184 passed
subtests**, plus **four failures because the required real Mongo instance at
127.0.0.1:27305 was unavailable**. Its unchanged JUnit is retained as
`payment_contracts_local_mongo_unavailable.xml`: 618 JUnit cases, four failures,
zero errors or skips, 42.473 seconds. This is not an all-green integration result.
The four real-Mongo cases remain required in the actual CI environment:

- `test_actual_provider_payout_allocates_to_one_split_share_only_and_replay_is_idempotent`
- `test_frozen_split_order_does_not_turn_into_a_second_bank_receipt_or_cod_collection`
- `test_replayed_refresh_of_bank_and_bnpl_waits_for_original_bank_leg_date`
- `test_existing_provider_share_can_coexist_only_when_proved_same_payment`

Exact wider command, run from the repository root with the existing test venv:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 PYTHONPATH=backend:backend/tests \
/workspace/scratch/39c6dec7c37f/profit-test-venv/bin/python -B -m pytest \
--noconftest -q -p no:cacheprovider --asyncio-mode=auto --tb=short \
--junitxml=docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence/payment_contracts_green.xml \
backend/tests/test_operational_payment_read_view.py \
backend/tests/test_operational_balance_partial_paid_observation.py \
backend/tests/test_operational_customer_payment_basis.py \
backend/tests/test_operational_balance_source_stage_timing.py \
backend/tests/test_operational_balance_provider_original_dates.py \
backend/tests/test_operational_balance_order_page_recognition.py \
backend/tests/test_operational_balance_order_source_projection.py \
backend/tests/test_operational_balance_provider_capture_acquisition.py \
backend/tests/test_operational_balance_split_tenders.py \
backend/tests/test_operational_balance_payment_source_current.py \
backend/tests/test_operational_balance_provider_sar.py \
backend/tests/test_operational_balance_transaction_capture.py \
backend/tests/test_component_source_detail_recovery.py \
backend/tests/test_operational_balance_bank_tender_proof.py
```

The output was subsequently renamed to describe the actual environment failure;
its contents were not edited. No production database or financial write was
performed by this local run. Performance acceptance remains the separate paired
actual-runtime source measurement and eventual production saved-refresh check.

The new test file was subsequently renamed, with identical content, to
`backend/tests/test_operational_balance_payment_read_view.py`. This deliberately
matches both existing strict Operational Balance CI discovery lists, so all 45
new cases execute there instead of merely triggering unrelated workflows. The
historical command and JUnit above retain the original filename actually used.
