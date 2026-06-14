"""Iter-189 — Shipping `payment_mode` (Prepaid / Deferred).

Verifies the new merchant-facing field that supersedes `is_deferred`:
  • `payment_mode="prepaid"`  ⇔ `is_deferred=False`
  • `payment_mode="deferred"` ⇔ `is_deferred=True`

Backward compat: existing UI code that reads/writes `is_deferred`
continues to work; new code can use either field.
"""
import os

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv("/app/backend/.env")
import sys
sys.path.insert(0, "/app/backend")
from server import app  # noqa: E402


@pytest.mark.asyncio
async def test_payment_mode_two_way_compat():
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        email = f"pm-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "P", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        h = {"Authorization": f"Bearer {token}"}

        # ── 1) Default settings: each company has payment_mode field
        r = await client.get("/api/settings", headers=h)
        body = r.json()
        comps = body["shipping_companies"]
        assert len(comps) > 0
        for c in comps:
            assert "payment_mode" in c
            assert c["payment_mode"] in ("prepaid", "deferred")
            # Consistency invariant.
            assert c["payment_mode"] == (
                "deferred" if c.get("is_deferred") else "prepaid")

        # ── 2) PUT with payment_mode="deferred" sets is_deferred=True
        comps[0]["payment_mode"] = "deferred"
        comps[0].pop("is_deferred", None)
        body["shipping_companies"] = comps
        r = await client.put("/api/settings", headers=h, json=body)
        assert r.status_code == 200

        r = await client.get("/api/settings", headers=h)
        c0 = r.json()["shipping_companies"][0]
        assert c0["is_deferred"] is True
        assert c0["payment_mode"] == "deferred"

        # ── 3) PUT with is_deferred=False resets payment_mode to prepaid
        new_body = r.json()
        new_body["shipping_companies"][0]["is_deferred"] = False
        new_body["shipping_companies"][0].pop("payment_mode", None)
        r = await client.put("/api/settings", headers=h, json=new_body)
        assert r.status_code == 200

        r = await client.get("/api/settings", headers=h)
        c0 = r.json()["shipping_companies"][0]
        assert c0["is_deferred"] is False
        assert c0["payment_mode"] == "prepaid"

        # ── 4) Invalid payment_mode rejected
        bad = r.json()
        bad["shipping_companies"][0]["payment_mode"] = "wat"
        r = await client.put("/api/settings", headers=h, json=bad)
        assert r.status_code in (400, 422), r.text

        # ── 5) /shipping-companies/discover also returns payment_mode
        r = await client.get("/api/shipping-companies/discover", headers=h)
        if r.status_code == 200:  # endpoint exists for this user
            for c in r.json().get("configured", []):
                assert "payment_mode" in c
                assert c["payment_mode"] in ("prepaid", "deferred")
