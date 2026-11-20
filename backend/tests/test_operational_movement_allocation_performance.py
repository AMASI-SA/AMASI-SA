"""A supplier payment only reconciles its allocation basis once per owner revision."""
import operational_balance_service as service
from operational_balance_store import STATES, read
from test_operational_balance_integration import NOW, movement, run, started


def test_many_unrelated_invoices_do_not_repeat_history_scan(monkeypatch):
    def invoice(identity, party, amount):
        return {"id": identity, "kind": "supplier", "party_type": "supplier", "party_id": party,
                "currency": "SAR", "direction": "payable", "business_date": NOW[:10],
                "expected": "0.00", "confirmed": amount, "evidence_ids": []}

    async def scenario(db):
        await started(db, supplier="10")
        state = await read(db, "owner")
        baseline = next(key for key, item in state["openings"].items() if item["party_type"] == "supplier")
        invoices = {key: invoice(key, "other-supplier", "9.00") for key in (f"other-{i}" for i in range(600))}
        invoices["first"] = invoice("first", "supplier", "20.00")
        invoices["second"] = invoice("second", "supplier", "30.00")
        state["engine"]["obligations"] = invoices
        await db[STATES].replace_one({"_id": "owner"}, state)

        reconciliations = []
        original = service.reconcile_credits

        def count(state, **kwargs):
            reconciliations.append(len(state["engine"]["obligations"]))
            return original(state, **kwargs)

        monkeypatch.setattr(service, "reconcile_credits", count)
        for request_id, amount, expected in (
            ("auto-first", "25", [(baseline, "10.00"), ("first", "15.00")]),
            ("auto-second", "18", [("first", "5.00"), ("second", "13.00")]),
            ("auto-final", "17", [("second", "17.00")]),
        ):
            reconciliations.clear()
            payload = {**movement(), "request_id": request_id, "amount": amount}
            saved = await service.create_movement(db, "owner", "owner", payload, clock=NOW)
            assert saved["allocations"] == [{"obligation_id": key, "amount": value} for key, value in expected]
            # Includes the party check, allocation preparation and audit balance projections.
            assert 0 < len(reconciliations) <= 4
            assert await service.create_movement(db, "owner", "owner", payload, clock=NOW) == saved

        state = await read(db, "owner")
        assert len(state["movements"]) == 3
        report = service.report(state)
        supplier = next(row for row in report["parties"] if row["party_id"] == "supplier")
        assert supplier["outstanding_payable"] == "0.00"
        assert report["summary"]["actual_liquidity"] == "940.00"

    run(scenario)
