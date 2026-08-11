import asyncio

from product_google_taxonomy_salla_publish import (
    PROVIDER_FIELD,
    _eligible_filter,
    publish_google_taxonomy_batch,
)


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, *_args, **_kwargs):
        return self

    async def to_list(self, length):
        return self.rows[:length]


class _Collection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.updates = []
        self.inserts = []

    def find(self, *_args, **_kwargs):
        return _Cursor(self.rows)

    async def update_one(self, query, update, **kwargs):
        self.updates.append((query, update, kwargs))

    async def insert_one(self, row):
        self.inserts.append(row)


class _DB(dict):
    def __missing__(self, key):
        value = _Collection()
        self[key] = value
        return value


def _product():
    return {
        "id": "mpv2_123",
        "mezan_product_id": "mpv2_123",
        "salla_product_id": "123",
        "name": "عباية",
        "google_category": "5388",
        "google_category_id": "5388",
        "google_category_path": "ملابس > ملابس تقليدية",
    }


def test_eligible_filter_uses_taxonomy_specific_sync_state():
    query = _eligible_filter("user-1")

    assert query["google_taxonomy_salla_sync_status"] == {"$ne": "synced"}
    assert "salla_sync_status" not in query


def test_publish_writes_isolated_field_then_verifies_readback():
    calls = []

    async def provider(_db, _user_id, method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        if method == "PUT":
            return {"status": 201, "success": True}
        if len(calls) == 1:
            return {"data": {"google_taxonomy": None}}
        return {"data": {"google_taxonomy": {"id": "5388"}}}

    db = _DB()
    db["mezan_products_v2"] = _Collection([_product()])
    result = asyncio.run(publish_google_taxonomy_batch(
        db,
        user_id="user-1",
        limit=1,
        stop_on_failure=True,
        provider_call=provider,
    ))

    assert calls == [
        ("GET", "/products/123", None),
        ("PUT", "/products/123", {PROVIDER_FIELD: "5388"}),
        ("GET", "/products/123", None),
    ]
    assert result["synced"] == 1
    assert result["failed"] == 0
    update = db["mezan_products_v2"].updates[-1][1]["$set"]
    assert update["google_taxonomy_salla_sync_status"] == "synced"
    assert update["google_taxonomy_authority"] == "salla"


def test_publish_stops_after_first_readback_mismatch():
    calls = []

    async def provider(_db, _user_id, method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        return {"data": {"google_taxonomy": None}}

    db = _DB()
    db["mezan_products_v2"] = _Collection([_product(), {**_product(), "salla_product_id": "456"}])
    result = asyncio.run(publish_google_taxonomy_batch(
        db,
        user_id="user-1",
        limit=2,
        stop_on_failure=True,
        provider_call=provider,
    ))

    assert result["selected"] == 2
    assert result["failed"] == 1
    assert result["stopped_early"] is True
    assert all(call[1] == "/products/123" for call in calls)
    update = db["mezan_products_v2"].updates[-1][1]["$set"]
    assert update["google_taxonomy_salla_sync_status"] == "failed"
    assert update["google_taxonomy_salla_sync_error"] == "google_taxonomy_readback_mismatch"


def test_publish_skips_write_when_salla_already_matches():
    calls = []

    async def provider(_db, _user_id, method, path, **kwargs):
        calls.append((method, path, kwargs.get("json")))
        return {"data": {"google_taxonomy": "5388"}}

    db = _DB()
    db["mezan_products_v2"] = _Collection([_product()])
    result = asyncio.run(publish_google_taxonomy_batch(
        db,
        user_id="user-1",
        limit=1,
        stop_on_failure=True,
        provider_call=provider,
    ))

    assert calls == [("GET", "/products/123", None)]
    assert result["already_matched"] == 1
    assert result["synced"] == 1
