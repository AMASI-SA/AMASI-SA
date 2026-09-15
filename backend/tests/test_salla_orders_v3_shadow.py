import pytest
from pymongo.errors import DuplicateKeyError

from salla_orders_v3.shadow import SallaOrdersShadowEngine


class _InsertResult:
    acknowledged = True

    def __init__(self, *, matched_count=0, upserted_id=None):
        self.matched_count = matched_count
        self.upserted_id = upserted_id


class _ShadowCollection:
    def __init__(self):
        self.rows = {}

    async def find_one(self, query, projection=None):
        row = self.rows.get(query.get("_id"))
        return dict(row) if row else None

    async def update_one(self, query, update, upsert=False):
        key = query["_id"]
        created = key not in self.rows
        row = dict(self.rows.get(key) or {})
        row.update(update.get("$setOnInsert") or {})
        row.update(update.get("$set") or {})
        self.rows[key] = row
        return _InsertResult(
            matched_count=0 if created else 1,
            upserted_id=key if created else None,
        )


class _DB:
    def __init__(self):
        self.salla_orders_v3_shadow = _ShadowCollection()


class _Gateway:
    async def get_order_items(self, user_id, internal_id):
        assert user_id == "owner-1"
        assert internal_id == "901"
        return [{"id": 7, "sku": "A", "quantity": 1, "options": {"المقاس": "XL"}}]

    async def get_light_order_details(self, user_id, internal_id):
        return {
            "id": 901,
            "reference_id": "3001",
            "updated_at": "2026-08-30T10:00:00+03:00",
            "status": {"slug": "under_review", "name": "بانتظار المراجعة"},
        }


@pytest.mark.asyncio
async def test_shadow_sync_writes_only_dedicated_collection_and_is_idempotent():
    db = _DB()
    engine = SallaOrdersShadowEngine(db, gateway=_Gateway())

    first = await engine.sync_order(
        user_id="owner-1",
        store_id="store-1",
        light_order={"id": 901, "reference_id": "3001"},
        fetch_details=True,
    )
    second = await engine.sync_order(
        user_id="owner-1",
        store_id="store-1",
        light_order={"id": 901, "reference_id": "3001"},
        fetch_details=True,
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert len(db.salla_orders_v3_shadow.rows) == 1
    stored = next(iter(db.salla_orders_v3_shadow.rows.values()))
    assert stored["user_id"] == "owner-1"
    assert stored["store_id"] == "store-1"
    assert stored["compatibility_order"]["products"][0]["options"][0]["value"] == "XL"
    assert stored["shadow_only"] is True
    assert stored["excluded_from_operational_reads"] is True


class _ConflictShadowCollection(_ShadowCollection):
    def __init__(self):
        super().__init__()
        self.conflicted = False

    async def update_one(self, query, update, upsert=False):
        if not self.conflicted:
            self.conflicted = True
            self.rows[query["_id"]] = {
                "_id": query["_id"],
                "compatibility_order": {
                    "provider_updated_at": "2099-01-01T00:00:00+00:00",
                    "items_synced_at": "2099-01-01T00:01:00+00:00",
                    "items_sync_status": "succeeded",
                    "items_payload_valid": True,
                    "products": [{"order_item_id": "newer", "quantity": 3}],
                    "sync_revision": 7,
                },
            }
            raise DuplicateKeyError("simulated concurrent insert")
        return await super().update_one(query, update, upsert=upsert)


@pytest.mark.asyncio
async def test_shadow_write_retries_revision_conflict_without_losing_newer_items():
    db = _DB()
    db.salla_orders_v3_shadow = _ConflictShadowCollection()
    engine = SallaOrdersShadowEngine(db, gateway=_Gateway())

    result = await engine.sync_order(
        user_id="owner-1",
        store_id="store-1",
        light_order={"id": 901, "reference_id": "3001"},
        fetch_details=True,
    )

    stored = next(iter(db.salla_orders_v3_shadow.rows.values()))
    assert result["ok"] is True
    assert stored["compatibility_order"]["products"] == [
        {"order_item_id": "newer", "quantity": 3}
    ]
    assert stored["compatibility_order"]["sync_revision"] == 8
