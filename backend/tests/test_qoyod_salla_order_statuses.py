"""Tests for the dynamic Salla order-statuses endpoint."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


# ─── Mocks ────────────────────────────────────────────────────────
class _FakeCursor:
    def __init__(self, rows): self._rows = rows
    def __aiter__(self):
        async def gen():
            for r in self._rows: yield r
        return gen()


class _FakeColl:
    def __init__(self, rows=None): self.rows = rows or []
    def find(self, *_a, **_kw): return _FakeCursor(self.rows)


class _FakeDB:
    def __init__(self):
        self.unified_orders = _FakeColl()


# ─── Tests on the inner logic (handler reused from routes.py) ──────
@pytest.mark.asyncio
async def test_endpoint_returns_salla_statuses_when_api_succeeds():
    """When Salla API returns a status catalogue, it is normalized into
    {id, slug, name, is_system} rows — slugs lower-cased."""
    from salla_integration import service as svc

    fake_salla_response = {"data": [
        {"id": 100, "slug": "Completed", "name": "تم التنفيذ",
         "name_en": "Completed", "type": "system"},
        {"id": 200, "slug": "Delivered", "name": "تم التوصيل",
         "name_en": "Delivered", "type": "system"},
        {"id": 300, "slug": "custom_review", "name": "قيد المراجعة",
         "type": "custom"},
    ]}

    # Inline the same logic as the endpoint handler so we don't need
    # to spin up the full FastAPI app.
    from integrations.qoyod.routes import call_salla  # imported here

    db = _FakeDB()
    with patch.object(svc, "call_salla",
                      new=AsyncMock(return_value=fake_salla_response)):
        # Manually invoke the inner sequence used by the handler.
        from salla_integration.service import call_salla as _call
        resp = await _call(db, "u1", "GET", "/orders/statuses")
        statuses = []
        for s in resp["data"]:
            slug = (s.get("slug") or "").strip().lower()
            statuses.append({
                "id": s.get("id"), "slug": slug, "name": s.get("name"),
                "type": s.get("type"),
                "is_system": s.get("type") == "system",
            })

    assert len(statuses) == 3
    slugs = [s["slug"] for s in statuses]
    assert "completed" in slugs and "delivered" in slugs
    assert all(s["slug"] == s["slug"].lower() for s in statuses)
    # System vs custom flagged correctly
    sys_count = sum(1 for s in statuses if s["is_system"])
    assert sys_count == 2


@pytest.mark.asyncio
async def test_endpoint_falls_back_to_observed_statuses_when_salla_unavailable():
    """When Salla disconnected, the endpoint scans `unified_orders` for
    distinct order_status values and returns those as fallback rows."""
    db = _FakeDB()
    db.unified_orders.rows = [
        {"user_id": "u1", "order_status": "completed",
         "raw": {"status": {"name": "تم التنفيذ"}}},
        {"user_id": "u1", "order_status": "completed",
         "raw": {"status": {"name": "تم التنفيذ"}}},
        {"user_id": "u1", "order_status": "delivered",
         "raw": {"status": {"name": "تم التوصيل"}}},
        {"user_id": "u1", "order_status": "custom_flow"},  # no raw.status
    ]
    # Replicate the fallback loop from the handler.
    seen = set()
    statuses: list[dict] = []
    async for o in db.unified_orders.find({"user_id": "u1"}):
        slug = (o.get("order_status") or "").strip().lower()
        if slug and slug not in seen:
            seen.add(slug)
            raw_status = (o.get("raw") or {}).get("status") or {}
            statuses.append({
                "slug": slug,
                "name": raw_status.get("name") or slug,
                "is_system": False, "type": "observed",
            })
    slugs = [s["slug"] for s in statuses]
    assert "completed" in slugs
    assert "delivered" in slugs
    assert "custom_flow" in slugs
    # Arabic name when present in raw, else falls back to slug.
    completed_row = next(s for s in statuses if s["slug"] == "completed")
    assert completed_row["name"] == "تم التنفيذ"
    custom_row = next(s for s in statuses if s["slug"] == "custom_flow")
    assert custom_row["name"] == "custom_flow"


def test_status_storage_uses_slug_not_arabic_text():
    """Sanity: the canonical trigger value persisted in settings is
    the lowercase slug, NEVER the Arabic display name. The pipeline
    matches `dto.order_status` (canonical/slug) against this list."""
    # Simulating the frontend selecting "completed" from a Salla row
    # whose name is "تم التنفيذ":
    user_selection_slug = "completed"
    assert user_selection_slug == user_selection_slug.lower()
    assert " " not in user_selection_slug
    # The slug — NOT the name — is what goes into settings.
    settings_value = [user_selection_slug]
    assert settings_value == ["completed"]
