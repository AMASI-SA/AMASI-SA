import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest

import store_delivery_driver_app_routes as driver_routes
import store_delivery_settlement_routes as settlement_routes
from store_delivery_driver_app_routes import (
    DELIVERY_EXCEPTION_CODES,
    DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_CANCEL,
    DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_DELAY,
    DELIVERY_EXCEPTION_CUSTOMER_UNREACHABLE,
    DRIVER_STATUS_TRANSITIONS,
    DriverReceiveScan,
    DriverStatusUpdate,
    _push_salla_delivery_status,
    _true_barcode_match,
)
from store_delivery_domain import (
    DELIVERY_STATUS_ASSIGNED,
    DELIVERY_STATUS_DELIVERED,
    DELIVERY_STATUS_OUT_FOR_DELIVERY,
)
from store_delivery_payment_evidence_routes import (
    CUSTOMER_CONVERSATION_EVIDENCE,
    DELIVERY_PROOFS,
    RECEIPTS,
)


def test_operational_v2_true_barcode_match_excludes_order_numbers():
    assert _true_barcode_match("ABC") == [
        {"barcode": "ABC"},
        {"shipping_barcode": "ABC"},
        {"tracking_number": "ABC"},
    ]


def test_operational_v2_receive_session_payload_is_barcode_only():
    payload = DriverReceiveScan(barcode="SHIP-123")
    assert payload.barcode == "SHIP-123"


def test_operational_v2_assigned_can_be_received_or_delivered_in_one_flow():
    allowed = DRIVER_STATUS_TRANSITIONS[DELIVERY_STATUS_ASSIGNED]
    assert DELIVERY_STATUS_OUT_FOR_DELIVERY in allowed
    assert DELIVERY_STATUS_DELIVERED in allowed


def test_operational_v2_exception_codes_are_internal_customer_outcomes():
    assert DELIVERY_EXCEPTION_CODES == {
        DELIVERY_EXCEPTION_CUSTOMER_UNREACHABLE,
        DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_DELAY,
        DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_CANCEL,
    }


def test_operational_v2_evidence_channels_are_distinct():
    assert len({RECEIPTS, DELIVERY_PROOFS, CUSTOMER_CONVERSATION_EVIDENCE}) == 3


def test_operational_v2_delivery_requires_independent_proof_field():
    payload = DriverStatusUpdate(
        barcode="SHIP-1",
        target_status=DELIVERY_STATUS_DELIVERED,
        payment_method="cash",
        delivery_proof_reference="proof-1",
        conversation_evidence_reference="conversation-1",
    )
    assert payload.delivery_proof_reference == "proof-1"
    assert payload.conversation_evidence_reference == "conversation-1"


def test_operational_v2_status_changes_can_bind_optional_conversation_evidence():
    source = inspect.getsource(driver_routes.make_store_delivery_driver_app_router)
    status_block = source.split('@router.post("/deliveries/status")', 1)[1]
    assert "conversation_evidence_reference" in status_block
    assert "_bind_status_conversation" in status_block
    assert "store_delivery_status_evidence_reference" in status_block


@pytest.mark.parametrize(
    ("slug", "actual"),
    [("delivering", "delivering"), ("delivered", "delivered")],
)
def test_operational_v2_salla_write_is_verified_by_readback(monkeypatch, slug, actual):
    call = AsyncMock(side_effect=[
        {"success": True},
        {"data": {"status": {"slug": actual}}},
    ])
    monkeypatch.setattr(driver_routes, "_call_salla", call)

    result = asyncio.run(_push_salla_delivery_status(
        object(),
        user_id="merchant-1",
        assignment={"order_id": "100"},
        order={"order_id": "100"},
        slug=slug,
    ))

    assert result["slug"] == slug
    assert result["verified_slug"] == actual
    assert call.await_count == 2
    first = call.await_args_list[0]
    second = call.await_args_list[1]
    assert first.args[2:4] == ("POST", "/orders/100/status")
    assert first.kwargs["json"]["slug"] == slug
    assert second.args[2:4] == ("GET", "/orders/100")


def test_operational_v2_router_exposes_simplified_courier_paths():
    source = inspect.getsource(driver_routes.make_store_delivery_driver_app_router)
    for path in (
        '/deliveries/home',
        '/deliveries/search/{query}',
        '/deliveries/report',
        '/deliveries/receive-sessions',
        '/deliveries/receive-sessions/{session_id}/scan',
        '/deliveries/receive-sessions/{session_id}/close',
        '/deliveries/exception',
        '/deliveries/status',
    ):
        assert path in source




def test_operational_v2_report_contract_exposes_receipt_and_delivery_proof_rows():
    source = inspect.getsource(driver_routes.make_store_delivery_driver_app_router)
    report_block = source.split('@router.get("/deliveries/report")', 1)[1].split(
        'async def _move_out_for_delivery', 1
    )[0]
    for field in (
        '"receipt_reference": 1',
        '"receipt_url": 1',
        '"delivery_proof_reference": 1',
        '"delivery_proof_url": 1',
        '"payment_method": 1',
        '"amount": 1',
    ):
        assert field in report_block


def test_operational_v2_delivery_finance_is_operational_only_until_dedicated_mz2_link():
    source = inspect.getsource(driver_routes)
    router_source = inspect.getsource(driver_routes.make_store_delivery_driver_app_router)
    assert "financial_cutover_is_active" not in source
    assert "post_delivery_journal" not in source
    assert "require_delivery_order_creation" not in source
    assert '"accounting_status": "operational_only"' in router_source
    assert '"financial_handoff_status": "pending_mz2_driver_balance_link"' in router_source
    assert '"financial_source": "store_delivery_operational"' in router_source
    assert '"balance_source": "store_delivery_operational"' in router_source
    assert '"accounting_link_status": "pending_mz2_driver_balance_link"' in router_source


def test_operational_v2_settlements_do_not_post_accounting_or_mutate_bank_balance():
    source = inspect.getsource(settlement_routes)
    assert "post_settlement_journal" not in source
    assert "store_driver_ledger_balances" not in source
    assert "require_p02_shipping_financial_writes" not in source
    assert '"posting_scope": "operational_balance"' in source
    assert '"accounting_status": "operational_only"' in source
    assert '"financial_handoff_status": "pending_mz2_driver_balance_link"' in source
    assert '"balance_source": "store_delivery_operational"' in source
    assert '@router.get("/accounts")' in source


def test_operational_v2_internal_exceptions_never_call_salla():
    source = inspect.getsource(driver_routes.make_store_delivery_driver_app_router)
    block = source.split('@router.post("/deliveries/exception")', 1)[1].split(
        '@router.post("/deliveries/status")', 1
    )[0]
    assert "_push_salla_delivery_status" not in block
    assert "_call_salla" not in block


class _RowsCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def to_list(self, length):
        return [dict(row) for row in self.rows[:length]]


class _RowsCollection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, projection):
        driver_id = query.get("driver_id")
        status = query.get("status")
        matched = [
            row for row in self.rows
            if (not driver_id or row.get("driver_id") == driver_id)
            and (not isinstance(status, str) or row.get("status") == status)
        ]
        return _RowsCursor(matched)


class _TotalsDb:
    def __init__(self, earnings, collections, settlements):
        self.rows = {
            driver_routes.DRIVER_EARNINGS: earnings,
            driver_routes.DRIVER_COLLECTIONS: collections,
            settlement_routes.SETTLEMENTS: settlements,
        }

    def __getitem__(self, name):
        return _RowsCollection(self.rows.get(name, []))


@pytest.mark.asyncio
async def test_build37_pending_driver_settlement_does_not_change_operational_balance():
    db = _TotalsDb(
        earnings=[{"driver_id": "d1", "amount": 100}],
        collections=[{"driver_id": "d1", "cod_custody_amount": 500}],
        settlements=[
            {
                "driver_id": "d1",
                "settlement_type": "cod_remittance",
                "amount": 200,
                "cod_settled_amount": 200,
                "delivery_fee_settled_amount": 0,
                "status": "pending_driver_confirmation",
            },
        ],
    )
    totals = await settlement_routes._totals(db, "merchant", "d1")
    assert totals["cod_cash_custody"] == 500
    assert totals["delivery_earnings_due"] == 100
    assert totals["net_due_from_driver"] == 400
    assert totals["net_due_to_driver"] == 0
    assert totals["pending_driver_confirmation_count"] == 1
    assert totals["pending_cod_remittance"] == 200

    db.rows[settlement_routes.SETTLEMENTS][0]["status"] = "posted"
    posted = await settlement_routes._totals(db, "merchant", "d1")
    assert posted["cod_cash_custody"] == 300
    assert posted["delivery_earnings_due"] == 100
    assert posted["net_due_from_driver"] == 200
    assert posted["pending_driver_confirmation_count"] == 0


def test_build37_driver_confirmation_and_receipt_review_contracts_are_operational_only():
    settlement_source = inspect.getsource(settlement_routes)
    driver_source = inspect.getsource(driver_routes.make_store_delivery_driver_app_router)
    assert '"status": "pending_driver_confirmation"' in settlement_source
    assert '"status": final_status' in driver_source
    assert '"driver_rejection_reason"' in driver_source
    assert '"ledger_txn_group_id": None' in driver_source
    assert "/settlements/pending" in driver_source
    assert "/settlements/{settlement_id}/decision" in driver_source
    assert "/payment-reviews" in driver_source
    assert "post_settlement_journal" not in driver_source
