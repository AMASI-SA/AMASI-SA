from datetime import date

import pytest

import campaign_ai_gcc_market_expansion_service as service


@pytest.mark.asyncio
async def test_refresh_wires_governed_evidence_into_planner(monkeypatch):
    captured = {}

    async def fake_load(db, user_id, *, observed_days):
        captured["load"] = (db, user_id, observed_days)
        return [{"market": "Saudi Arabia", "evidence_status": "partial"}]

    async def fake_refresh(db, user_id, *, as_of, opportunity_plan, market_evidence):
        captured["refresh"] = {
            "db": db,
            "user_id": user_id,
            "as_of": as_of,
            "opportunity_plan": opportunity_plan,
            "market_evidence": market_evidence,
        }
        return {"strategy": "collect_gcc_evidence_before_expansion"}

    monkeypatch.setattr(service, "load_gcc_market_evidence", fake_load)
    monkeypatch.setattr(service, "refresh_gcc_market_expansion_plan", fake_refresh)

    db = object()
    result = await service.refresh_gcc_market_expansion_from_sources(
        db,
        "owner-1",
        as_of=date(2026, 8, 23),
        opportunity_plan={"monthly_profit_gap_sar": 50000},
        observed_days=30,
    )

    assert captured["load"] == (db, "owner-1", 30)
    assert captured["refresh"]["market_evidence"][0]["market"] == "Saudi Arabia"
    assert result["strategy"] == "collect_gcc_evidence_before_expansion"
