from types import SimpleNamespace

import pytest

from integrations.qoyod_manual import diagnose


class _Inbox:
    def __init__(self, row):
        self.row = row
        self.selector = None
        self.sort = None

    async def find_one(self, selector, *args, **kwargs):
        self.selector = selector
        self.sort = kwargs.get("sort")
        return self.row


@pytest.mark.asyncio
async def test_diagnose_reads_same_newest_owner_set_as_final_sender(monkeypatch):
    inbox = _Inbox({
        "user_id": "orders-owner",
        "salla_order_number": "277674576",
        "canonical_payload": {
            "order_number": "277674576",
            "currency": "SAR",
            "total_amount": 100.0,
            "items": [],
        },
    })
    db = SimpleNamespace(integration_inbox=inbox)

    async def payment_facts(*_args, **_kwargs):
        return {}

    async def prepare(_db, *, canon, **_kwargs):
        return canon

    monkeypatch.setattr(diagnose, "get_order_payment_facts", payment_facts)
    monkeypatch.setattr(
        diagnose, "_prepare_sar_invoice_canon_from_inbox", prepare)
    monkeypatch.setattr(diagnose, "_assert_sar_currency", lambda _canon: None)

    result = await diagnose.diagnose_totals(
        db,
        user_id="main",
        orders_user_id="orders-owner",
        order_number="277674576",
        allow_verified_salla_recovery=False,
    )

    assert result["code"] == "no_items"
    assert inbox.selector == {
        "user_id": {"$in": ["main", "orders-owner"]},
        "salla_order_number": "277674576",
    }
    assert inbox.sort == [("received_at", -1)]
