"""Synthetic fixture generated through actual HTTP plan writer and prior reader."""
import asyncio
import json
from pathlib import Path
import httpx
from pytest import MonkeyPatch
from tests.test_google_prior_season_routes import DB, app_for, payload, get

async def fixture():
    patches = MonkeyPatch()
    try:
        db = DB()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app_for(patches, db)), base_url="http://test") as client:
            saved = await client.put("/google_ads/commercial-plans/winter", json=payload())
            assert saved.status_code == 200
            response = await get(client)
            assert response.status_code == 200
        assert "acknowledge_review" not in saved.json()["fields"]
        return {"plan": saved.json(), "response": response.json(), "origin": "synthetic actual HTTP PUT then GET; no provider or customer data"}
    finally:
        patches.undo()

if __name__ == "__main__":
    target = Path(__file__).resolve().parents[2] / "frontend/src/services/__fixtures__/googlePriorSeasonReview.json"
    target.write_text(json.dumps(asyncio.run(fixture()), ensure_ascii=False, indent=2) + "\n")
