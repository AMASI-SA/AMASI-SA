"""Order edits do not erase independently proved customer payments."""
from copy import deepcopy

import pytest

from operational_balance_engine import reconcile
from operational_provider_transaction_evidence import normalize_operational_payment


CREATED = "2026-11-01T10:00:00+00:00"
UPDATED = "2026-11-02T10:00:00+00:00"
AS_OF = "2026-11-03T10:00:00+00:00"


def document(total="199.20", paid="200.00", refund="0.80", *, captured=False):
    amount = lambda value: {"amount": value, "currency": "SAR"}
    raw = {
        "id": "synthetic-adjustment", "reference_id": "synthetic-order",
        "currency": "SAR", "total": amount(total), "status": "delivered",
        "created_at": CREATED, "updated_at": UPDATED, "payment_method": "mada",
        "payment_methods": [{"payment_method": "mada", "amount": amount(paid),
                             "transaction_reference": "synthetic-capture"}],
        "payment_actions": {
            "refund_action": {"paid_amount": amount(paid), "refund_amount": amount(refund),
                              "refunded_transactions": []},
            "remaining_action": {"paid_amount": amount(paid), "remaining_amount": amount("0.00"),
                                 "has_remaining_amount": False},
        },
    }
    if captured:
        raw["payment"] = {"method": "mada", "status": "paid", "paid_amount": amount(paid),
                          "reference": "synthetic-capture", "paid_at": CREATED}
    source = {"user_id": "owner", "order_id": raw["id"], "order_number": raw["reference_id"],
              "raw_by_source": {"salla_direct": raw}}
    return raw, source


def normalize(raw, source):
    return normalize_operational_payment(raw, "SAR", raw["total"], CREATED, AS_OF,
        owner="owner", order_number=raw["reference_id"], provider_order_id=raw["id"],
        source=source, rows=[])


@pytest.mark.parametrize("captured", [False, True])
def test_refund_hint_keeps_original_paid_principal_and_is_not_execution(captured):
    raw, source = document(captured=captured)
    before = deepcopy((raw, source))
    payment, issues = normalize(raw, source)
    assert payment is not None, issues
    assert payment["gross"] == "200.00"
    assert payment["refunds"] == []
    assert payment["customer_adjustment"]["suggested_refund_amount"] == "0.80"
    assert payment["customer_adjustment"]["suggested_collection_amount"] == "0.00"
    assert (raw, source) == before
    state = {"owner_id": "owner", "status": "active", "started_at": "2026-10-31T10:00:00+00:00",
             "openings": {}, "movements": []}
    order = {"id": raw["id"], "order_number": raw["reference_id"], "currency": "SAR",
             "created_at": CREATED, "updated_at": UPDATED, "payment": payment}
    result = reconcile(state, {"orders": [order]}, AS_OF)
    assert result["engine"]["obligations"]["provider:" + raw["id"]]["confirmed"] == "200.00"
    assert not any(key.startswith("refund:") for key in result["engine"]["facts"])
    repeated = reconcile(result, {"orders": [order]}, AS_OF)
    assert repeated == result


def test_added_product_keeps_paid_basis_and_exposes_only_remaining_amount():
    raw, source = document(total="300.00", refund="0.00", captured=True)
    payment, issues = normalize(raw, source)
    assert payment is not None, issues
    assert payment["gross"] == "200.00"
    assert payment["customer_adjustment"]["suggested_collection_amount"] == "100.00"
    assert payment["customer_adjustment"]["suggested_refund_amount"] == "0.00"


@pytest.mark.parametrize("mutation", ["currency", "reference", "paid_conflict", "owner", "stale", "unpaid"])
def test_independent_payment_fallback_preserves_identity_and_evidence_guards(mutation):
    raw, source = document(captured=True)
    if mutation == "currency":
        raw["payment_methods"][0]["amount"]["currency"] = "USD"
    elif mutation == "reference":
        raw["payment_methods"][0]["transaction_reference"] = "different-capture"
    elif mutation == "paid_conflict":
        raw["payment_actions"]["remaining_action"]["paid_amount"]["amount"] = "201.00"
    elif mutation == "owner":
        source["user_id"] = "other"
    elif mutation == "stale":
        source["g47_salla_snapshot"] = {"source_updated_at": AS_OF}
    elif mutation == "unpaid":
        raw["payment"]["status"] = "authorized"
    payment, issues = normalize(raw, source)
    assert payment is None and issues


def test_same_reference_total_edit_keeps_existing_claim_and_does_not_create_second_capture():
    raw, source = document(total="200.00", refund="0.00")
    initial_payment, issues = normalize(raw, source)
    assert initial_payment is not None, issues
    state = {"owner_id": "owner", "status": "active", "started_at": "2026-10-31T10:00:00+00:00",
             "openings": {}, "movements": []}
    order = {"id": raw["id"], "order_number": raw["reference_id"], "currency": "SAR",
             "created_at": CREATED, "updated_at": UPDATED, "payment": initial_payment}
    first = reconcile(state, {"orders": [order]}, UPDATED)
    edited, source = document(total="199.20", refund="0.80")
    edited["updated_at"] = AS_OF
    payment, issues = normalize(edited, source)
    assert payment is not None, issues
    second = reconcile(first, {"orders": [{**order, "updated_at": AS_OF, "payment": payment}]}, AS_OF)
    claim = second["engine"]["obligations"]["provider:" + raw["id"]]
    assert claim["confirmed"] == "200.00" and not claim.get("incomplete")
    assert not any(key.startswith("refund:") for key in second["engine"]["facts"])
    assert second["movements"] == []
