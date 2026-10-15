import pytest

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
async def test_shadow_engine_builds_repeatable_candidate_without_persistence():
    db = _DB()
    engine = SallaOrdersShadowEngine(db, gateway=_Gateway())

    first = await engine.sync_order(
        user_id="owner-1",
        store_id="store-1",
        light_order={"id": 901, "reference_id": "3001"},
    )
    second = await engine.sync_order(
        user_id="owner-1",
        store_id="store-1",
        light_order={"id": 901, "reference_id": "3001"},
    )

    assert first["ok"] is True
    assert second["ok"] is True
    assert db.salla_orders_v3_shadow.rows == {}
    first_order = dict(first["compatibility_order"])
    second_order = dict(second["compatibility_order"])
    for field in (
        "ingested_at",
        "items_last_attempt_at",
        "items_last_success_at",
        "items_synced_at",
    ):
        first_order.pop(field)
        second_order.pop(field)
    assert first_order == second_order
    assert first["compatibility_order"]["products"][0]["options"][0]["value"] == "XL"


@pytest.mark.asyncio
async def test_details_are_the_canonical_snapshot_not_a_webhook_payload_merge():
    engine = SallaOrdersShadowEngine(_DB(), gateway=_Gateway())

    result = await engine.prepare_order_snapshot(
        user_id="owner-1",
        store_id="store-1",
        light_order={
            "id": 901,
            "reference_id": "3001",
            "status": {"slug": "stale-webhook"},
            "utm_campaign": "stale-webhook-value",
            "customer": {"name": "stale-webhook-value"},
        },
    )

    snapshot = result["compatibility_order"]
    assert snapshot["order_status_slug"] == "under_review"
    assert "utm_campaign" not in snapshot
    assert "customer" not in snapshot


@pytest.mark.asyncio
async def test_shadow_accepts_order_id_alias_but_rejects_mismatched_details_identity():
    class _MismatchedGateway(_Gateway):
        async def get_light_order_details(self, user_id, internal_id):
            return {"id": 902, "reference_id": "3001"}

    db = _DB()
    alias_engine = SallaOrdersShadowEngine(db, gateway=_Gateway())
    accepted = await alias_engine.sync_order(
        user_id="owner-1",
        store_id="store-1",
        light_order={"order_id": 901, "order_number": "3001"},
    )
    assert accepted["ok"] is True
    assert accepted["compatibility_order"]["order_status_slug"] == "under_review"

    mismatch_engine = SallaOrdersShadowEngine(_DB(), gateway=_MismatchedGateway())
    with pytest.raises(RuntimeError, match="internal identity mismatch"):
        await mismatch_engine.sync_order(
            user_id="owner-1",
            store_id="store-1",
            light_order={"id": 901, "reference_id": "3001"},
        )


@pytest.mark.asyncio
async def test_shadow_engine_never_touches_snapshot_collection():
    class _NoPersistence:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected persistence access: {name}")

    db = _DB()
    db.salla_orders_v3_shadow = _NoPersistence()
    engine = SallaOrdersShadowEngine(db, gateway=_Gateway())

    result = await engine.sync_order(
        user_id="owner-1",
        store_id="store-1",
        light_order={"id": 901, "reference_id": "3001"},
    )

    assert result["ok"] is True
    assert result["compatibility_order"]["items_authoritative"] is True
