from __future__ import annotations

import asyncio
from copy import deepcopy

import pytest

from integrations_control_center import campaign_product_associations as links


def _matches(row, query):
    return all(row.get(key) == value for key, value in query.items())


class Cursor:
    def __init__(self, rows):
        self.rows = [deepcopy(row) for row in rows]

    async def to_list(self, length=None):
        return deepcopy(self.rows if length is None else self.rows[:length])


class Collection:
    def __init__(self):
        self.rows = []
        self.indexes = []

    async def create_index(self, keys, **kwargs):
        self.indexes.append((deepcopy(keys), deepcopy(kwargs)))
        return kwargs.get("name")

    async def find_one(self, query, projection=None):
        for row in self.rows:
            if _matches(row, query):
                found = deepcopy(row)
                if projection and projection.get("_id") == 0:
                    found.pop("_id", None)
                return found
        return None

    def find(self, query, projection=None):
        return Cursor(row for row in self.rows if _matches(row, query))

    async def insert_one(self, row):
        for keys, options in self.indexes:
            if options.get("unique") is not True:
                continue
            partial = options.get("partialFilterExpression") or {}

            def included(candidate):
                for field, condition in partial.items():
                    if isinstance(condition, dict) and condition.get("$type") == "string":
                        if not isinstance(candidate.get(field), str):
                            return False
                return True

            if not included(row):
                continue
            identity = tuple(row.get(field) for field, _ in keys)
            if any(
                included(existing)
                and tuple(existing.get(field) for field, _ in keys) == identity
                for existing in self.rows
            ):
                raise RuntimeError(f"duplicate index: {options.get('name')}")
        for existing in self.rows:
            if (
                existing.get("user_id") == row.get("user_id")
                and existing.get("association_key") == row.get("association_key")
                and existing.get("predecessor_key") == row.get("predecessor_key")
            ):
                raise RuntimeError("forked history")
        self.rows.append(deepcopy(row))
        return object()


class RacingCollection(Collection):
    def __init__(self):
        super().__init__()
        self.waiting_inserts = 0
        self.release_inserts = asyncio.Event()

    async def insert_one(self, row):
        self.waiting_inserts += 1
        if self.waiting_inserts == 1:
            await self.release_inserts.wait()
        else:
            self.release_inserts.set()
        return await super().insert_one(row)


class DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())

    def __getattr__(self, name):
        return self[name]


def association(
    *,
    campaign_id="campaign-1",
    ad_squad_id=None,
    ad_id=None,
    product_id="product-1",
    verification_status="verified",
    valid_from="2026-08-12T08:00:00+00:00",
):
    return {
        "provider": "snapchat_ads",
        "account_id": "account-1",
        "campaign_id": campaign_id,
        "ad_squad_id": ad_squad_id,
        "ad_id": ad_id,
        "product_id": product_id,
        "product_name": f"Product {product_id}",
        "valid_from": valid_from,
        "evidence": {
            "source": "management_proposal",
            "verification_status": verification_status,
            "observed_at": valid_from,
            "source_ref": "proposal-1",
            "confidence": 1.0,
        },
    }


@pytest.mark.asyncio
async def test_tenant_scoping_and_verified_default_are_fail_closed():
    db = DB()
    await links.attach_campaign_product(
        db,
        "owner-1",
        association(),
        idempotency_key="owner-1-link-1",
    )
    await links.attach_campaign_product(
        db,
        "owner-2",
        association(product_id="other-owner-product"),
        idempotency_key="owner-2-link-1",
    )
    await links.attach_campaign_product(
        db,
        "owner-1",
        association(product_id="possible-product", verification_status="inferred"),
        idempotency_key="owner-1-link-2",
    )

    confirmed = await links.list_effective_campaign_products(
        db,
        "owner-1",
        provider="snapchat",
        account_id="account-1",
        campaign_id="campaign-1",
        as_of="2026-08-12T09:00:00+00:00",
    )
    review = await links.list_effective_campaign_products(
        db,
        "owner-1",
        provider="snapchat_ads",
        account_id="account-1",
        campaign_id="campaign-1",
        as_of="2026-08-12T09:00:00+00:00",
        include_unverified=True,
    )

    assert [row["product_id"] for row in confirmed] == ["product-1"]
    assert {row["product_id"] for row in review} == {
        "product-1",
        "possible-product",
    }
    inferred = next(row for row in review if row["product_id"] == "possible-product")
    assert inferred["confirmed"] is False
    assert inferred["decision_fact_eligible"] is False
    assert "user_id" not in inferred


@pytest.mark.asyncio
async def test_campaign_squad_and_ad_hierarchy_is_inherited_without_cross_scope_leak():
    db = DB()
    rows = [
        association(product_id="campaign-product"),
        association(
            ad_squad_id="squad-1",
            product_id="squad-product",
        ),
        association(
            ad_squad_id="squad-1",
            ad_id="ad-1",
            product_id="ad-product",
        ),
        association(
            ad_squad_id="squad-2",
            product_id="other-squad-product",
        ),
    ]
    for index, row in enumerate(rows):
        await links.attach_campaign_product(
            db,
            "owner-1",
            row,
            idempotency_key=f"hierarchy-{index}",
        )

    campaign = await links.list_campaign_product_ids(
        db,
        "owner-1",
        provider="snapchat_ads",
        account_id="account-1",
        campaign_id="campaign-1",
        as_of="2026-08-12T09:00:00+00:00",
    )
    squad = await links.list_campaign_product_ids(
        db,
        "owner-1",
        provider="snapchat_ads",
        account_id="account-1",
        campaign_id="campaign-1",
        ad_squad_id="squad-1",
        as_of="2026-08-12T09:00:00+00:00",
    )
    ad = await links.list_campaign_product_ids(
        db,
        "owner-1",
        provider="snapchat_ads",
        account_id="account-1",
        campaign_id="campaign-1",
        ad_squad_id="squad-1",
        ad_id="ad-1",
        as_of="2026-08-12T09:00:00+00:00",
    )

    assert campaign == ["campaign-product"]
    assert squad == ["campaign-product", "squad-product"]
    assert ad == ["ad-product", "campaign-product", "squad-product"]


@pytest.mark.asyncio
async def test_append_only_restatement_detach_and_as_of_history():
    db = DB()
    first = await links.attach_campaign_product(
        db,
        "owner-1",
        association(valid_from="2026-08-12T08:00:00+00:00"),
        idempotency_key="history-first",
        reason="Selected at campaign intake",
    )
    restated_input = association(valid_from="2026-08-12T10:00:00+00:00")
    restated_input["evidence"] = {
        "source": "catalog_item",
        "verification_status": "verified",
        "observed_at": "2026-08-12T10:00:00+00:00",
        "source_ref": "catalog:item:product-1",
        "confidence": 1.0,
    }
    second = await links.attach_campaign_product(
        db,
        "owner-1",
        restated_input,
        idempotency_key="history-second",
        expected_latest_event_id=first["event_id"],
        reason="Catalog identity verified",
    )
    detached_input = deepcopy(restated_input)
    detached_input["valid_from"] = "2026-08-12T12:00:00+00:00"
    detached_input["evidence"]["observed_at"] = "2026-08-12T12:00:00+00:00"
    detached = await links.detach_campaign_product(
        db,
        "owner-1",
        detached_input,
        idempotency_key="history-detach",
        expected_latest_event_id=second["event_id"],
        reason="Product removed from the campaign",
    )

    before_restatement = await links.list_effective_campaign_products(
        db,
        "owner-1",
        provider="snapchat_ads",
        account_id="account-1",
        campaign_id="campaign-1",
        as_of="2026-08-12T09:00:00+00:00",
    )
    after_detach = await links.list_effective_campaign_products(
        db,
        "owner-1",
        provider="snapchat_ads",
        account_id="account-1",
        campaign_id="campaign-1",
        as_of="2026-08-12T12:01:00+00:00",
    )
    history = await links.get_campaign_product_history(
        db, "owner-1", first["association_key"]
    )

    assert before_restatement[0]["event_id"] == first["event_id"]
    assert after_detach == []
    assert [row["event_type"] for row in history] == [
        "attached",
        "restated",
        "detached",
    ]
    assert [row["revision"] for row in history] == [1, 2, 3]
    assert history[0]["event_reason"] == "Selected at campaign intake"
    assert detached["supersedes_event_id"] == second["event_id"]
    assert db[links.CAMPAIGN_PRODUCT_LINK_COLLECTION].rows[0]["state"] == "active"


@pytest.mark.asyncio
async def test_idempotency_and_stale_version_cannot_fork_history():
    db = DB()
    first = await links.attach_campaign_product(
        db,
        "owner-1",
        association(),
        idempotency_key="stable-request",
    )
    retry = await links.attach_campaign_product(
        db,
        "owner-1",
        association(),
        idempotency_key="stable-request",
    )
    assert retry["event_id"] == first["event_id"]

    changed = association()
    changed["product_name"] = "Different payload"
    with pytest.raises(links.CampaignProductAssociationConflict):
        await links.attach_campaign_product(
            db,
            "owner-1",
            changed,
            idempotency_key="stable-request",
        )

    second = await links.attach_campaign_product(
        db,
        "owner-1",
        association(valid_from="2026-08-12T10:00:00+00:00"),
        idempotency_key="new-revision",
        expected_latest_event_id=first["event_id"],
    )
    with pytest.raises(links.CampaignProductAssociationConflict):
        await links.attach_campaign_product(
            db,
            "owner-1",
            association(valid_from="2026-08-12T11:00:00+00:00"),
            idempotency_key="stale-revision",
            expected_latest_event_id=first["event_id"],
        )
    history = await links.get_campaign_product_history(
        db, "owner-1", first["association_key"]
    )
    assert [row["event_id"] for row in history] == [
        first["event_id"],
        second["event_id"],
    ]


@pytest.mark.asyncio
async def test_concurrent_reuse_of_idempotency_key_cannot_commit_two_events():
    db = DB()
    collection = RacingCollection()
    db.collections[links.CAMPAIGN_PRODUCT_LINK_COLLECTION] = collection
    await links.ensure_campaign_product_association_indexes(db)

    first = association(product_id="product-a")
    second = association(product_id="product-b")
    results = await asyncio.gather(
        links.attach_campaign_product(
            db,
            "owner-1",
            first,
            idempotency_key="same-racing-request",
        ),
        links.attach_campaign_product(
            db,
            "owner-1",
            second,
            idempotency_key="same-racing-request",
        ),
        return_exceptions=True,
    )

    assert len(collection.rows) == 1
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(
        isinstance(result, links.CampaignProductAssociationConflict)
        for result in results
    ) == 1


@pytest.mark.asyncio
async def test_management_proposal_products_are_known_then_adopted_to_provider_campaign():
    db = DB()
    proposal_links = await links.attach_products_to_management_proposal(
        db,
        "owner-1",
        proposal_id="proposal-create-1",
        provider="snapchat_ads",
        account_id="account-1",
        products=[
            {"id": "comb-1", "name": "Personalized metal comb"},
            "chain-1",
        ],
        actor_id="owner-1",
        observed_at="2026-08-12T08:00:00+00:00",
        idempotency_prefix="proposal-products",
    )
    adopted = await links.adopt_management_proposal_products(
        db,
        "owner-1",
        proposal_id="proposal-create-1",
        provider="snapchat_ads",
        account_id="account-1",
        campaign_id="snap-campaign-99",
        actor_id="owner-1",
        provider_verified_at="2026-08-12T08:05:00+00:00",
        provider_entity_verified=True,
        idempotency_prefix="campaign-products",
    )
    campaign_products = await links.list_campaign_product_ids(
        db,
        "owner-1",
        provider="snapchat_ads",
        account_id="account-1",
        campaign_id="snap-campaign-99",
        as_of="2026-08-12T09:00:00+00:00",
    )

    assert len(proposal_links) == 2
    assert len(adopted) == 2
    assert campaign_products == ["chain-1", "comb-1"]
    assert {row["evidence"]["source"] for row in adopted} == {"campaign_creation"}
    assert {row["origin_event_id"] for row in adopted} == {
        row["event_id"] for row in proposal_links
    }


@pytest.mark.asyncio
async def test_indexes_cover_event_identity_linear_history_and_hierarchy():
    db = DB()
    await links.ensure_campaign_product_association_indexes(db)
    indexes = {
        options.get("name"): options
        for _, options in db[links.CAMPAIGN_PRODUCT_LINK_COLLECTION].indexes
    }
    assert {
        "mezan_campaign_product_links_v2_event_unique",
        "mezan_campaign_product_links_v2_idempotency_unique",
        "mezan_campaign_product_links_v2_linear_history",
        "mezan_campaign_product_links_v2_hierarchy_history",
        "mezan_campaign_product_links_v2_proposal_history",
    } <= set(indexes)
    idempotency = indexes["mezan_campaign_product_links_v2_idempotency_unique"]
    assert idempotency["unique"] is True
    assert idempotency["partialFilterExpression"] == {
        "idempotency_key": {"$type": "string"}
    }
