"""Final expanded-scope review, real isolated Mongo only."""
from test_operational_balance_integration import run, started, NOW, movement
from operational_balance_service import refresh, report, create_movement
from operational_balance_sources import entities
from operational_balance_store import read


def test_native_branch_recurring_rent_has_a_selectable_financial_party_and_can_be_paid():
    async def scenario(db):
        await started(db)
        await db.operating_recurring_obligations_v2.insert_one({
            "id": "native-rent", "user_id": "owner", "status": "active", "title": "Branch rent",
            "expense_type": "rent", "entity_type": "branch", "entity_id": "warehouse-branch-1",
            "entity_name": "Main branch", "cycle": "monthly", "period_amount": 3100,
            "start_date": "2026-10-01", "auto_renew": True,
        })
        result = report(await refresh(db, "owner", clock=NOW))
        obligation = next(row for row in result["obligations"] if row["kind"] == "recurring")
        choices = await entities(db, "owner", obligation["party_type"])
        assert any(row["id"] == obligation["party_id"] for row in choices), (
            "Native recurring asset association was mistaken for an external payee",
            obligation["party_type"], obligation["party_id"], choices,
        )
        payment = movement()
        payment.update(request_id="pay-native-rent", kind="settlement", party_type=obligation["party_type"],
            party_id=obligation["party_id"], amount=obligation["available_to_pay"],
            allocations=[{"obligation_id": obligation["id"], "amount": obligation["available_to_pay"]}])
        await create_movement(db, "owner", "owner", payment)
        await create_movement(db, "owner", "owner", payment)  # transport retry
        final = report(await read(db, "owner"))
        assert final["summary"]["operating_expenses_paid"] == obligation["available_to_pay"] == "3100.00"
        assert final["summary"]["actual_liquidity"] == "-2100.00"
        paid = next(row for row in final["obligations"] if row["id"] == obligation["id"])
        assert paid["source_context"] == {"expense_type": "rent", "entity_type": "branch", "entity_id": "warehouse-branch-1", "obligation_id": "native-rent"}
        assert (paid["expected"], paid["confirmed"], paid["settled"], paid["outstanding"]) == ("0.00", "3100.00", "3100.00", "0.00")
        await db.operating_recurring_invoices_v2.insert_one({"id": "rent-final", "user_id": "owner", "obligation_id": "native-rent", "period_start": "2026-10-01", "period_end": "2026-10-31", "amount": "6200"})
        refreshed = report(await refresh(db, "owner", clock=NOW))
        paid = next(row for row in refreshed["obligations"] if row["id"] == obligation["id"])
        assert (paid["expected"], paid["confirmed"], paid["settled"], paid["outstanding"]) == ("3100.00", "3100.00", "3100.00", "0.00")
        assert refreshed["summary"]["operating_expenses_paid"] == "3100.00"
        assert refreshed["summary"]["actual_liquidity"] == "-2100.00"
    run(scenario)


def test_unknown_native_recurring_expense_type_does_not_guess_external_payee():
    async def scenario(db):
        await started(db)
        await db.operating_recurring_obligations_v2.insert_one({
            "id": "unknown", "user_id": "owner", "status": "active", "title": "Unknown recurring type",
            "expense_type": "invented_type", "entity_type": "business", "entity_id": "supplier",
            "entity_name": "Business", "cycle": "monthly", "period_amount": 3100,
            "start_date": "2026-10-01", "auto_renew": True,
        })
        result = report(await refresh(db, "owner", clock=NOW))
        assert not any(row["kind"] == "recurring" for row in result["obligations"])
        assert any(issue["code"] == "recurring_contract_incomplete" for issue in result["issues"])
    run(scenario)
