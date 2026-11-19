from tests.test_operational_balance_workspace import BASE, entry, grant, resource, run


def test_approved_home_is_bounded_backdate_safe_and_never_lists_other_recorders():
    async def scenario(db, client):
        for day in (11, 12, 13, 14, 15, 4):
            response = await client.post(BASE + "/entries", json=entry(
                amount="11", business_date=f"2026-11-{day:02}", reference=f"DAY-{day}"
            ))
            assert response.status_code == 200
        default = await client.get(BASE + "/context")
        assert "recent_movements" not in default.json()
        query = {"home_preview": True}
        owner = (await client.get(BASE + "/context", params=query)).json()
        rows = owner["recent_movements"]
        assert [row["reference"] for row in rows] == ["DAY-15", "DAY-14", "DAY-13", "DAY-12"]
        assert len({row["event_id"] for row in rows}) == 4
        assert all(row["recorded_by"]["id"] == "owner" for row in rows)

        await grant(db, operations=[], resources=[resource("bank", "bank", movements="own", use=False)])
        headers = {"x-test-user": "staff", "x-test-mobile": "yes"}
        employee = (await client.get(BASE + "/context", params=query, headers=headers)).json()
        assert employee["recent_movements"] == []
        assert all(r["balance"] is None for r in employee["resources"])

    run(scenario)
