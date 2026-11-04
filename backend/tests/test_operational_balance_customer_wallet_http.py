"""Owner-bound wallet closing evidence over isolated Mongo and HTTP."""
from operational_balance_store import digest, mutate, read
from test_operational_balance_app_permissions import client
from test_operational_balance_integration import NOW, run, started


BASE = "/api/operational-balances/customer-wallet"


async def seed_wallet_store(db):
    await started(db)

    async def completed_report(state):
        state["engine"]["as_of"] = NOW

    await mutate(db, "owner", completed_report)
    await db.salla_integrations.insert_many([
        {"user_id": "owner", "store_id": "wallet-store-101", "currency": "SAR"},
        {"user_id": "other", "store_id": "wallet-store-202", "currency": "SAR"},
    ])


def snapshot(**changes):
    return {
        "request_id": "wallet-closing-first",
        "expected_session_scope": digest(["owner", "owner"]),
        "merchant_id": "wallet-store-101",
        "currency": "SAR",
        "as_of": "2026-10-06T12:00:00+00:00",
        "remaining_balance": "120.50",
        "source_reference": "synthetic-wallet-statement-101",
        **changes,
    }


def test_wallet_http_requires_web_owner_and_matching_session_and_store():
    async def scenario(db):
        await seed_wallet_store(db)
        before = await read(db, "owner")
        async with client(db, "owner", mobile=False) as http:
            response = await http.get(BASE + "/context")
            assert response.status_code == 200, response.text
            context = response.json()
            assert context["session_scope"] == digest(["owner", "owner"])
            assert context["merchant_id"] == "wallet-store-101"
            assert context["currency"] == "SAR"
            assert context["status"] == "active"
            assert context["can_manage"] is True
            assert context["balances"] == {}

            forged_session = await http.post(BASE + "/snapshot", json=snapshot(
                expected_session_scope=digest(["other", "other"])))
            assert forged_session.status_code == 403, forged_session.text
            assert forged_session.json()["detail"]["code"] == "operational_actor_scope_changed"

            forged_store = await http.post(BASE + "/snapshot", json=snapshot(
                merchant_id="wallet-store-202"))
            assert forged_store.status_code == 409, forged_store.text
            assert forged_store.json()["detail"]["code"] == "customer_wallet_merchant_mismatch"

            foreign_currency = await http.post(BASE + "/snapshot", json=snapshot(currency="USD"))
            assert foreign_currency.status_code == 409, foreign_currency.text
            assert foreign_currency.json()["detail"]["code"] == "customer_wallet_merchant_mismatch"

        for actor, mobile in [("staff", False), ("owner", True), ("staff", True)]:
            async with client(db, actor, mobile=mobile) as http:
                response = await http.get(BASE + "/context")
                assert response.status_code == 403, (actor, mobile, response.text)
                response = await http.post(BASE + "/snapshot", json=snapshot(
                    expected_session_scope=digest(["owner", actor])))
                assert response.status_code == 403, (actor, mobile, response.text)

        assert await read(db, "owner") == before
        assert (await read(db, "other"))["status"] == "draft"
        assert await db.general_ledger.count_documents({}) == 0

    run(scenario)


def test_wallet_http_replay_and_new_closing_replace_one_read_only_liability():
    async def scenario(db):
        await seed_wallet_store(db)
        before = await read(db, "owner")
        async with client(db, "owner", mobile=False) as http:
            first = await http.post(BASE + "/snapshot", json=snapshot())
            assert first.status_code == 200, first.text
            after_first = await read(db, "owner")
            replayed = await http.post(BASE + "/snapshot", json=snapshot())
            assert replayed.status_code == 200, replayed.text
            assert replayed.json() == first.json()
            assert await read(db, "owner") == after_first

            latest = snapshot(
                request_id="wallet-closing-second",
                as_of="2026-10-07T12:00:00+00:00",
                remaining_balance="85.25",
                source_reference="synthetic-wallet-statement-102",
                flow={
                    "period_start": "2026-10-05T09:00:00+00:00",
                    "opening_balance": "150.00", "added": "0.00",
                    "used": "64.75", "other_deductions": "0.00",
                    "opening_verified": True,
                },
            )
            replaced = await http.post(BASE + "/snapshot", json=latest)
            assert replaced.status_code == 200, replaced.text
            assert replaced.json()["id"] != first.json()["id"]
            assert replaced.json()["session_scope"] == digest(["owner", "owner"])
            after_replacement = await read(db, "owner")

            # An old request may replay its receipt without restoring its old closing.
            old_replay = await http.post(BASE + "/snapshot", json=snapshot())
            assert old_replay.status_code == 200, old_replay.text
            assert old_replay.json() == first.json()
            assert await read(db, "owner") == after_replacement

            response = await http.get(BASE + "/context")
            assert response.status_code == 200, response.text
            balance = response.json()["balances"]["SAR"]
            assert balance["remaining"] == "85.25"
            assert balance["as_of"] == latest["as_of"]
            assert balance["merchant_id"] == latest["merchant_id"]
            assert balance["flow"] == latest["flow"]
            assert balance["complete"] is True

            response = await http.get("/api/operational-balances/reports")
            assert response.status_code == 200, response.text
            report = response.json()
            wallet_rows = [row for row in report["parties"] if row["party_type"] == "customer_wallet"]
            assert len(wallet_rows) == 1
            assert wallet_rows[0]["outstanding_payable"] == "85.25"
            assert wallet_rows[0]["read_only"] is True
            assert report["summary"]["payable"] == "585.25"
            assert report["summary"]["actual_liquidity"] == "1000.00"

        after = await read(db, "owner")
        assert len(after["customer_wallet_snapshots"]) == 1
        assert len(after["audit"]) == len(before["audit"]) + 2
        assert len(after["requests"]) == len(before["requests"]) + 2
        for key in ("openings", "movements", "engine"):
            assert after[key] == before[key]
        assert "customer_wallet_snapshots" not in await read(db, "other")
        assert await db.general_ledger.count_documents({}) == 0

    run(scenario)
