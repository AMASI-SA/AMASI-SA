import asyncio

import pytest

import product_control_center_routes as control_center
from product_category_publish_support import install_product_category_publish_support


TARGET = "5388"


class _FakeCollection:
    def __init__(self):
        self.updates = []

    async def update_one(self, query, update, **kwargs):
        self.updates.append((query, update, kwargs))
        return None


class _FakeDB(dict):
    def __missing__(self, key):
        value = _FakeCollection()
        self[key] = value
        return value


def _install(monkeypatch, fake_call):
    monkeypatch.setattr(control_center, "call_salla", fake_call)
    install_product_category_publish_support()
    return control_center.call_salla


def test_taxonomy_publish_reads_salla_once_then_keeps_value_mezan_managed(monkeypatch):
    calls = []

    async def fake_call(db, user_id, method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        assert method == "GET"
        return {"data": {"google_taxonomy": None}}

    db = _FakeDB()
    wrapped = _install(monkeypatch, fake_call)
    result = asyncio.run(wrapped(
        db,
        "user-1",
        "PUT",
        "/products/123",
        json={"google_product_category": TARGET},
    ))

    assert [call[0] for call in calls] == ["GET"]
    assert result["skipped"] is True
    assert result["reason"] == "google_taxonomy_mezan_managed"
    verification = result["google_taxonomy_verification"]
    assert verification["attempted_write"] is False
    assert verification["provider_write_supported"] is False
    assert verification["authority"] == "mezan"

    update = db[control_center.PRODUCTS].updates[-1][1]["$set"]
    assert update["salla_sync_status"] == "mezan_managed"
    assert update["salla_synced_at"] is None
    assert update["salla_sync_error"] is None
    assert update["salla_sync_reason"] == "salla_public_api_google_taxonomy_writer_not_supported"
    assert update["google_taxonomy_authority"] == "mezan"


def test_taxonomy_no_change_skips_salla_write_and_marks_verified(monkeypatch):
    calls = []

    async def fake_call(db, user_id, method, path, **kwargs):
        calls.append(method)
        assert method == "GET"
        return {"data": {"google_taxonomy": TARGET}}

    db = _FakeDB()
    wrapped = _install(monkeypatch, fake_call)
    result = asyncio.run(wrapped(
        db,
        "user-1",
        "PUT",
        "/products/123",
        json={"google_product_category": TARGET},
    ))

    assert calls == ["GET"]
    assert result["skipped"] is True
    assert result["reason"] == "google_taxonomy_already_matches"
    assert result["google_taxonomy_verification"]["attempted_write"] is False
    assert result["google_taxonomy_verification"]["verified"] is True

    update = db[control_center.PRODUCTS].updates[-1][1]["$set"]
    assert update["salla_sync_status"] == "synced"
    assert update["google_taxonomy_authority"] == "salla"


def test_taxonomy_publish_isolated_from_other_product_changes(monkeypatch):
    async def fake_call(db, user_id, method, path, **kwargs):
        raise AssertionError("Salla must not be called for a blocked mixed change")

    db = _FakeDB()
    wrapped = _install(monkeypatch, fake_call)

    with pytest.raises(control_center.SallaError) as exc:
        asyncio.run(wrapped(
            db,
            "user-1",
            "PUT",
            "/products/123",
            json={"google_product_category": TARGET, "name": "changed"},
        ))

    assert exc.value.status_code == 409


def test_non_taxonomy_product_update_still_reaches_salla(monkeypatch):
    calls = []

    async def fake_call(db, user_id, method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        return {"status": 200, "success": True}

    db = _FakeDB()
    wrapped = _install(monkeypatch, fake_call)
    result = asyncio.run(wrapped(
        db,
        "user-1",
        "PUT",
        "/products/123",
        json={"name": "changed"},
    ))

    assert result["success"] is True
    assert calls == [("PUT", "/products/123", {"name": "changed"})]
