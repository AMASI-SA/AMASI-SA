from datetime import datetime, timezone

from growth_intelligence.season_liquidity_intelligence import build_season_liquidity_intelligence


def test_verified_event_creates_preparation_window():
    result = build_season_liquidity_intelligence(
        as_of=datetime(2026, 8, 23, tzinfo=timezone.utc),
        events=[{
            "event_id": "eid-1",
            "country": "SA",
            "name": "National Event",
            "starts_at": "2026-09-20T00:00:00+00:00",
            "lead_days": 30,
            "confidence": "high",
            "evidence": [{"source": "official"}],
        }],
    )
    assert result["events"][0]["confidence"] == "high"
    assert result["read_only"] is True


def test_missing_evidence_is_not_promoted():
    result = build_season_liquidity_intelligence(
        as_of=datetime(2026, 8, 23, tzinfo=timezone.utc),
        events=[{
            "event_id": "eid-2",
            "name": "Unknown Event",
            "starts_at": "2026-09-20T00:00:00+00:00",
        }],
    )
    assert "missing_evidence:Unknown Event" in result["limitations"]
