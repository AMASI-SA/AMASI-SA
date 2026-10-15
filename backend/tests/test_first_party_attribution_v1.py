from __future__ import annotations

import hashlib
from copy import deepcopy
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from first_party_attribution.core import (
    EVENT_COLLECTION,
    ORDER_ATTRIBUTION_COLLECTION,
    build_order_attribution,
    build_tracking_url,
    ensure_first_party_attribution_indexes,
    hash_customer_identity,
    link_order_attribution,
    persist_storefront_event,
    verify_link_token,
)


def _matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(row, branch) for branch in expected):
                return False
            continue
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$in" in expected:
                choices = expected["$in"]
                if isinstance(actual, list):
                    if not any(value in choices for value in actual):
                        return False
                elif actual not in choices:
                    return False
            if "$gte" in expected and not (actual and actual >= expected["$gte"]):
                return False
        elif actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, key, direction):
        self.rows.sort(key=lambda row: row.get(key) or "", reverse=direction < 0)
        return self

    async def to_list(self, limit):
        return deepcopy(self.rows[:limit])


class FakeCollection:
    def __init__(self):
        self.rows = []

    async def create_index(self, *args, **kwargs):
        return kwargs.get("name")

    async def insert_one(self, row):
        self.rows.append(deepcopy(row))
        return SimpleNamespace(inserted_id=len(self.rows))

    async def insert_many(self, rows):
        for row in rows:
            await self.insert_one(row)

    async def find_one(self, query, projection=None, **kwargs):
        for row in self.rows:
            if _matches(row, query):
                if projection:
                    included = [key for key, enabled in projection.items() if enabled and key != "_id"]
                    if included:
                        return {key: row.get(key) for key in included}
                    return {key: value for key, value in deepcopy(row).items() if projection.get(key, 1)}
                return deepcopy(row)
        return None

    def find(self, query, projection=None):
        rows = [deepcopy(row) for row in self.rows if _matches(row, query)]
        if projection:
            included = [key for key, enabled in projection.items() if enabled and key != "_id"]
            if included:
                rows = [{key: row.get(key) for key in included} for row in rows]
            else:
                rows = [
                    {key: value for key, value in row.items() if projection.get(key, 1)}
                    for row in rows
                ]
        return FakeCursor(rows)

    async def update_one(self, query, update, upsert=False):
        for row in self.rows:
            if _matches(row, query):
                row.update(deepcopy(update.get("$set") or {}))
                return SimpleNamespace(upserted_id=None, matched_count=1, modified_count=1)
        if not upsert:
            return SimpleNamespace(upserted_id=None, matched_count=0, modified_count=0)
        row = {key: value for key, value in query.items() if not key.startswith("$") and not isinstance(value, dict)}
        row.update(deepcopy(update.get("$setOnInsert") or {}))
        row.update(deepcopy(update.get("$set") or {}))
        self.rows.append(row)
        return SimpleNamespace(upserted_id=len(self.rows), matched_count=0, modified_count=0)

    async def count_documents(self, query):
        return sum(1 for row in self.rows if _matches(row, query))


class FakeDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())

    def __getattr__(self, name):
        return self[name]


@pytest.fixture(autouse=True)
def signing_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "test-secret-that-is-not-used-outside-tests")


def test_snapchat_tracking_url_is_signed_and_preserves_destination_query():
    tracked, record = build_tracking_url(
        "https://amasi-sa.com/p/728?color=gold",
        user_id="owner-1",
        provider="snapchat",
        product_id="728",
        account_id="snap-account-1",
        link_id="link-1",
        snapchat_macros=True,
    )
    query = parse_qs(urlsplit(tracked).query)

    assert query["color"] == ["gold"]
    assert query["utm_source"] == ["snapchat"]
    assert query["mz_campaign_id"] == ["~.~SERVER_CAMPAIGN_ID~.~"]
    assert query["mz_ad_squad_id"] == ["~.~SERVER_AD_SQUAD_ID~.~"]
    assert query["mz_ad_id"] == ["~.~SERVER_AD_ID~.~"]
    token = verify_link_token(query["mzt"][0])
    assert token == {
        "account": "snap-account-1",
        "h": "amasi-sa.com",
        "i": token["i"],
        "l": "link-1",
        "p": "snapchat",
        "product": "728",
        "u": "owner-1",
        "v": 1,
    }
    assert record["link_id"] == "link-1"


def test_tampered_link_token_is_rejected():
    tracked, _ = build_tracking_url(
        "https://amasi-sa.com/p/728",
        user_id="owner-1",
        provider="snapchat",
    )
    token = parse_qs(urlsplit(tracked).query)["mzt"][0]
    with pytest.raises(ValueError, match="invalid attribution link token"):
        verify_link_token(token[:-1] + ("A" if token[-1] != "A" else "B"))


def test_attribution_keeps_acquisition_and_return_touch_separate():
    result = build_order_attribution(
        [
            {
                "event_name": "view_item",
                "occurred_at": "2026-08-14T10:00:00+00:00",
                "source": "snapchat",
                "campaign_id": "campaign-1",
                "ad_group_id": "squad-1",
                "ad_id": "ad-1",
                "link_id": "link-1",
            },
            {
                "event_name": "view_item",
                "occurred_at": "2026-08-15T10:00:00+00:00",
                "source": "google_organic",
                "link_id": "google-return",
            },
        ],
        order_number="277000001",
        matched_by="identity_hash",
    )

    assert result["acquisition_touch"]["source"] == "snapchat"
    assert result["last_paid_touch"]["source"] == "snapchat"
    assert result["last_touch"]["source"] == "google_organic"
    assert result["confidence"] == "deterministic"


@pytest.mark.asyncio
async def test_event_is_idempotent_and_order_links_by_hashed_identity():
    db = FakeDB()
    await ensure_first_party_attribution_indexes(db)
    await db.mezan_snapchat_entities_v2.insert_many([
        {"user_id": "owner-1", "ad_account_id": "acc-1", "entity_type": "campaign", "external_id": "campaign-1"},
        {"user_id": "owner-1", "ad_account_id": "acc-1", "entity_type": "ad_squad", "external_id": "squad-1"},
        {"user_id": "owner-1", "ad_account_id": "acc-1", "entity_type": "ad", "external_id": "ad-1"},
    ])
    tracked, _ = build_tracking_url(
        "https://amasi-sa.com/p/728",
        user_id="owner-1",
        provider="snapchat",
        account_id="acc-1",
        link_id="link-1",
        snapchat_macros=True,
    )
    link_token = parse_qs(urlsplit(tracked).query)["mzt"][0]
    identity_hash = hash_customer_identity("phone", "+966 55 123 4567")
    event = {
        "event_id": "event-00000001",
        "visitor_id": "visitor-0001",
        "session_id": "session-0001",
        "event_name": "view_item",
        "occurred_at": "2026-08-14T10:00:00+00:00",
        "store_id": "123",
        "link_token": link_token,
        "campaign_id": "campaign-1",
        "ad_group_id": "squad-1",
        "ad_id": "ad-1",
        "product_id": "728",
        "identity_hashes": [identity_hash],
    }

    first = await persist_storefront_event(db, event)
    second = await persist_storefront_event(db, event)
    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert await db[EVENT_COLLECTION].count_documents({}) == 1
    stored = await db[EVENT_COLLECTION].find_one({"event_id": "event-00000001"})
    assert stored["provider_ids_verified"] is True
    assert stored["campaign_id"] == "campaign-1"
    assert "+966" not in repr(stored)

    await db.unified_orders.insert_one({
        "user_id": "owner-1",
        "order_number": "277000001",
    })
    result = await link_order_attribution(
        db,
        user_id="owner-1",
        order_number="277000001",
        order_payload={"customer": {"mobile": "+966 55 123 4567"}},
    )
    assert result == {
        "linked": True,
        "matched_by": "identity_hash",
        "confidence": "deterministic",
        "source": "snapchat",
        "campaign_id": "campaign-1",
    }
    order = await db.unified_orders.find_one({"order_number": "277000001"})
    assert order["mezan_attribution"]["acquisition_touch"]["ad_id"] == "ad-1"
    assert await db[ORDER_ATTRIBUTION_COLLECTION].count_documents({}) == 1


@pytest.mark.asyncio
async def test_pilot_store_records_attribution_without_becoming_an_integration(
    monkeypatch,
):
    monkeypatch.setenv("SALLA_ATTRIBUTION_PILOT_STORE_ID", "748155538")
    db = FakeDB()
    await db.salla_integrations.insert_one({
        "user_id": "amasi-owner",
        "store_id": "amasi-production-store",
        "status": "connected",
    })

    result = await persist_storefront_event(db, {
        "event_id": "pilot-event-1",
        "visitor_id": "pilot-visitor-1",
        "session_id": "pilot-session-1",
        "event_name": "page_view",
        "store_id": "748155538",
        "source": "direct",
    })

    stored = await db[EVENT_COLLECTION].find_one({"event_id": "pilot-event-1"})
    assert result["accepted"] is True
    assert stored["user_id"] == "amasi-owner"
    assert stored["store_scope"] == "attribution_pilot"
    assert stored["environment"] == "pilot"
    assert await db.unified_orders.count_documents({}) == 0


@pytest.mark.asyncio
async def test_pilot_store_can_never_link_or_enrich_an_order(monkeypatch):
    monkeypatch.delenv("SALLA_ATTRIBUTION_PILOT_STORE_ID", raising=False)
    db = FakeDB()
    await db[EVENT_COLLECTION].insert_one({
        "user_id": "amasi-owner",
        "store_id": "748155538",
        "store_scope": "attribution_pilot",
        "event_id": "pilot-purchase-event",
        "event_name": "purchase",
        "order_number": "DEMO-ORDER-1",
        "visitor_id": "pilot-visitor",
        "source": "snapchat",
        "occurred_at": "2026-08-15T10:00:00+00:00",
    })
    await db.unified_orders.insert_one({
        "user_id": "amasi-owner",
        "order_number": "DEMO-ORDER-1",
    })

    result = await link_order_attribution(
        db,
        user_id="amasi-owner",
        order_number="DEMO-ORDER-1",
        order_payload={"reference_id": "DEMO-ORDER-1"},
        store_id="748155538",
    )

    assert result == {
        "linked": False,
        "reason": "attribution_pilot_store_order_link_blocked",
    }
    order = await db.unified_orders.find_one({"order_number": "DEMO-ORDER-1"})
    assert "mezan_attribution" not in order
    assert await db[ORDER_ATTRIBUTION_COLLECTION].count_documents({}) == 0


@pytest.mark.asyncio
async def test_order_attribution_is_scoped_to_the_order_store():
    db = FakeDB()
    identity_hash = hash_customer_identity("phone", "+966 55 123 4567")
    await db[EVENT_COLLECTION].insert_many([
        {
            "user_id": "amasi-owner",
            "store_id": "748155538",
            "event_id": "pilot-event",
            "event_name": "page_view",
            "visitor_id": "pilot-visitor",
            "identity_hashes": [identity_hash],
            "source": "snapchat",
            "occurred_at": "2026-08-15T10:00:00+00:00",
        },
        {
            "user_id": "amasi-owner",
            "store_id": "amasi-production-store",
            "event_id": "amasi-event",
            "event_name": "page_view",
            "visitor_id": "amasi-visitor",
            "identity_hashes": [identity_hash],
            "source": "google_organic",
            "occurred_at": "2026-08-15T11:00:00+00:00",
        },
    ])
    await db.unified_orders.insert_one({
        "user_id": "amasi-owner",
        "order_number": "AMASI-ORDER-1",
    })

    result = await link_order_attribution(
        db,
        user_id="amasi-owner",
        order_number="AMASI-ORDER-1",
        order_payload={"customer": {"mobile": "+966 55 123 4567"}},
        store_id="amasi-production-store",
    )

    assert result["linked"] is True
    assert result["source"] == "google_organic"
    assert await db[ORDER_ATTRIBUTION_COLLECTION].count_documents({}) == 1


@pytest.mark.asyncio
async def test_unverified_snapchat_ids_are_not_persisted_as_truth():
    db = FakeDB()
    tracked, _ = build_tracking_url(
        "https://amasi-sa.com/p/728",
        user_id="owner-1",
        provider="snapchat",
        account_id="acc-1",
    )
    event = {
        "event_id": "event-00000002",
        "visitor_id": "visitor-0002",
        "session_id": "session-0002",
        "event_name": "view_item",
        "link_token": parse_qs(urlsplit(tracked).query)["mzt"][0],
        "campaign_id": "spoofed-campaign",
        "ad_id": "spoofed-ad",
    }
    await persist_storefront_event(db, event)
    stored = await db[EVENT_COLLECTION].find_one({"event_id": "event-00000002"})
    assert stored["source"] == "snapchat"
    assert stored["campaign_id"] is None
    assert stored["ad_id"] is None
    assert stored["provider_ids_verified"] is False


def test_identity_hash_is_normalized_and_never_plaintext():
    first = hash_customer_identity("email", " CUSTOMER@Example.com ")
    second = hashlib.sha256(b"email:customer@example.com").hexdigest()
    assert first == second
    assert "customer" not in first
