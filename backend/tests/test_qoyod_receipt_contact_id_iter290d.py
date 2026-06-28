"""Iter-290d — Qoyod /receipts requires `contact_id` at root.

Why this test exists
────────────────────
After Iter-290c shipped the corrected invoice payload shape, production
order 268756329 finally reached INVOICE_CREATED ✅ (Qoyod returned id 51).
But the next stage failed with:

    POST /receipts → 422
    {"error": "Invalid resource",
     "messages": {"contact": ["Can't be blank"]}}

The receipt builder never stamped `contact_id` — Qoyod's `/receipts`
validator requires it just like `/invoices` does.

Coverage
────────
1. `build_receipt_payload` stamps `contact_id` from the customer arg.
2. All Qoyod ids on the receipt are integers (`invoice_id`,
   `contact_id`, `account_id`).
3. Missing customer_id → contact_id omitted (None coerces to None).
4. Existing behaviour (account_id resolution, amount, currency) intact.
"""
from __future__ import annotations

from integrations.qoyod.invoice_builder import build_receipt_payload


_DTO = {
    "order_id":       "268756329",
    "order_number":   "268756329",
    "total_amount":   290.63,
    "currency":       "SAR",
    "payment_method": "mada",
}

_SETTINGS = {
    "payment_method_mapping": [
        {"salla_method": "mada", "qoyod_account_id": "94"},
    ],
}


def test_receipt_payload_carries_contact_id_at_root():
    pl = build_receipt_payload(
        qoyod_invoice_id="51", qoyod_customer_id="109",
        dto_dict=_DTO, invoice_date=None, settings=_SETTINGS,
    )
    assert pl["receipt"]["contact_id"] == 109
    assert isinstance(pl["receipt"]["contact_id"], int)


def test_receipt_payload_all_ids_are_integers():
    pl = build_receipt_payload(
        qoyod_invoice_id="51", qoyod_customer_id="109",
        dto_dict=_DTO, invoice_date=None, settings=_SETTINGS,
    )
    r = pl["receipt"]
    assert r["invoice_id"] == 51 and isinstance(r["invoice_id"], int)
    assert r["contact_id"] == 109 and isinstance(r["contact_id"], int)
    assert r["account_id"] == 94 and isinstance(r["account_id"], int)


def test_receipt_payload_omits_contact_id_when_customer_id_missing():
    pl = build_receipt_payload(
        qoyod_invoice_id="51", qoyod_customer_id=None,
        dto_dict=_DTO, invoice_date=None, settings=_SETTINGS,
    )
    # Pipeline should never call this with a missing customer_id, but
    # be defensive: an int(None) coercion returns None, and Qoyod will
    # then reject the receipt with a clear error.
    assert pl["receipt"]["contact_id"] is None


def test_receipt_payload_preserves_amount_currency_and_payment_method():
    pl = build_receipt_payload(
        qoyod_invoice_id="51", qoyod_customer_id="109",
        dto_dict=_DTO, invoice_date=None, settings=_SETTINGS,
    )
    r = pl["receipt"]
    assert r["amount"] == 290.63
    assert r["currency"] == "SAR"
    assert r["payment_method"] == "mada"
    assert r["external_reference"] == "268756329"


def test_receipt_payload_omits_account_id_when_no_mapping():
    pl = build_receipt_payload(
        qoyod_invoice_id="51", qoyod_customer_id="109",
        dto_dict=_DTO, invoice_date=None,
        settings={"payment_method_mapping": []},
    )
    # _resolve_payment_account returns None for unknown methods; the
    # coercion keeps None as None (preflight blocks upstream).
    assert pl["receipt"]["account_id"] is None
