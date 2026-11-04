"""Net fee presentation must preserve the writer's gross allocation authority."""
from copy import deepcopy

import pytest

from operational_balance_service import create_movement, refresh, report
from operational_balance_store import read
from test_operational_balance_integration import NOW, START, movement, run, started
from test_operational_balance_order_page_payment_flow import order_document


@pytest.mark.parametrize("kind,received,actual_fee,automatic", [
    ("settlement", "90.00", "10.00", False),
    ("collection", "100.00", "0.00", True),
])
def test_provider_net_display_preserves_gross_settlement_and_collection_once(kind, received, actual_fee, automatic):
    async def scenario(db):
        await started(db)
        document = order_document("tamara_installment", "100.00", None, number="provider-net-synthetic")
        await db.unified_orders.insert_one(document)
        await db.settings.insert_one({"user_id": "owner", "updated_at": START,
            "payment_methods": [{"name": "تمارا", "commission_percent": "10", "fixed_fee": "0", "vat_percent": "0"}]})
        before = await refresh(db, "owner", clock=NOW)
        claim_id = "provider:" + document["salla_order_id"]
        assert before["engine"]["obligations"][claim_id]["confirmed"] == "100.00"
        gross_party = next(p for p in report(before)["parties"] if p["party_type"] == "provider")
        net_party = next(p for p in report(before, provider_balances_net=True)["parties"] if p["party_type"] == "provider")
        assert gross_party["outstanding_receivable"] == "100.00"
        assert net_party["outstanding_receivable"] == "90.00"
        original_facts = deepcopy(before["engine"]["facts"])

        payload = movement()
        payload.update(request_id="synthetic-provider-" + kind, party_type="provider", party_id="tamara",
                       direction="incoming", kind=kind, amount=received, actual_fee_amount=actual_fee,
                       allocations=[] if automatic else [{"obligation_id": claim_id, "amount": "100.00"}])
        first = await create_movement(db, "owner", "owner", payload, clock=NOW)
        assert await create_movement(db, "owner", "owner", payload, clock=NOW) == first
        saved = await read(db, "owner")
        assert len(saved["movements"]) == 1
        assert saved["movements"][0]["allocations"] == [{"obligation_id": claim_id, "amount": "100.00"}]
        assert saved["movements"][0]["amount"] == received
        assert saved["movements"][0]["actual_fee_amount"] == actual_fee
        assert saved["engine"]["facts"] == original_facts
        assert saved["openings"] == before["openings"]

        for snapshot in (saved, await refresh(db, "owner", clock=NOW)):
            for net in (False, True):
                current = report(snapshot, provider_balances_net=net)
                party = next(p for p in current["parties"] if p["party_type"] == "provider")
                assert party["outstanding_receivable"] == party["outstanding_payable"] == "0.00"
                assert current["summary"]["actual_liquidity"] == ("1100.00" if automatic else "1090.00")
                summary = next(p for p in current["details"]["provider_balances"]["SAR"] if p["provider"] == "tamara")
                assert summary["net"] == "0.00" and summary["fees"] == actual_fee
            assert snapshot["movements"] == saved["movements"]
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)
