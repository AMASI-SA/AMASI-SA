from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

from snapchat_v2.entity_pagination import (
    EntityPageSpec,
    build_entity_page_pipeline,
    read_entity_page,
)
from snapchat_v2.entities import SNAPCHAT_ENTITY_FACTS_COLLECTION, normalize_entity
from snapchat_v2.facts import SNAPCHAT_HOURLY_FACTS_COLLECTION


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length=None):
        return deepcopy(self.rows[:length])


def _matches(row, query):
    for key, value in query.items():
        if key == "$and":
            if not all(_matches(row, item) for item in value):
                return False
        elif key == "$or":
            if not any(_matches(row, item) for item in value):
                return False
        elif isinstance(value, dict) and "$regex" in value:
            import re

            if not re.search(value["$regex"], str(row.get(key) or ""), re.I):
                return False
        elif row.get(key) != value:
            return False
    return True


class FakeEntityCollection:
    """Small pipeline oracle that returns the shape emitted by Mongo $facet."""

    def __init__(self, rows):
        self.rows = rows
        self.pipelines = []

    def aggregate(self, pipeline, **_kwargs):
        self.pipelines.append(deepcopy(pipeline))
        base = pipeline[0]["$match"]
        rows = [deepcopy(row) for row in self.rows if _matches(row, base)]
        for row in rows:
            row.setdefault("performance", {})
            row["sort_name"] = str(row.get("name") or "").lower()
            row["operational_status"] = str(
                row.get("effective_status") or row.get("status") or ""
            ).upper()
            row["operationally_active"] = bool(
                row.get("missing_from_latest_sync") is not True
                and row["operational_status"] in {"ACTIVE", "ENABLED", "DELIVERING"}
            )
            row["catalogue_present"] = True
        facet = pipeline[-1]["$facet"]
        item_stages = facet["items"]
        if item_stages and "$match" in item_stages[0]:
            rows = [
                row
                for row in rows
                if _matches(row, item_stages[0]["$match"])
            ]
            item_stages = item_stages[1:]
        sort = item_stages[0]["$sort"]
        # Stable multi-key sorting, applying the lowest-priority key first.
        for field, direction in reversed(list(sort.items())):
            def value(row, path=field):
                current = row
                for part in path.split("."):
                    current = current.get(part) if isinstance(current, dict) else None
                return current if current is not None else 0

            rows.sort(key=value, reverse=direction < 0)
        skip = item_stages[1]["$skip"]
        limit = item_stages[2]["$limit"]
        items = rows[skip : skip + limit]
        summary = {
            "_id": None,
            "entity_count": len(rows),
            "source_fact_count": sum(
                int(row["performance"].get("source_fact_count") or 0)
                for row in rows
            ),
        }
        for field in (
            "spend_native",
            "impressions",
            "swipes",
            "video_views",
            "view_completion",
            "view_content",
            "add_to_cart",
            "start_checkout",
            "add_billing",
            "purchases",
            "purchase_value_native",
        ):
            summary[field] = sum(
                float(row["performance"].get(field) or 0) for row in rows
            )
        root = {
            "identity_count": [{"value": len([row for row in self.rows if _matches(row, base)])}],
            "items": items,
            "filtered_count": [{"value": len(rows)}],
            "filtered_summary": [summary] if rows else [],
        }
        return FakeCursor([root])


class FakeDB:
    def __init__(self, rows):
        self.entities = FakeEntityCollection(rows)

    def __getitem__(self, name):
        assert name == SNAPCHAT_ENTITY_FACTS_COLLECTION
        return self.entities


def _campaigns(count=5_000):
    return [
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "ad_account_id": "account-1",
            "entity_type": "campaign",
            "external_id": f"campaign-{index:05d}",
            "name": f"Campaign {index:05d}",
            "status": "ACTIVE" if index % 2 == 0 else "PAUSED",
            "active": index % 2 == 0,
            "performance": {
                "source_fact_count": 1,
                "spend_native": float(count - index),
                "purchases": index % 7,
                "purchase_value_native": float(index % 7) * 10,
            },
        }
        for index in range(count)
    ]


async def _read(db, spec, **kwargs):
    return await read_entity_page(
        db,
        user_id="owner-1",
        ad_account_id="account-1",
        entity_type=kwargs.pop("entity_type", "campaign"),
        source_collection=SNAPCHAT_HOURLY_FACTS_COLLECTION,
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 1),
        start_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end_utc=datetime(2026, 8, 2, tzinfo=timezone.utc),
        timezone_name="UTC",
        action_report_time="conversion",
        level_status="complete",
        spec=spec,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_five_thousand_campaigns_materialize_only_first_twenty_five():
    db = FakeDB(_campaigns())
    result = await _read(db, EntityPageSpec())

    assert len(result["rows"]) == 25
    assert result["pagination"] == {
        "page": 1,
        "page_size": 25,
        "total": 5_000,
        "filtered_total": 5_000,
        "pages": 200,
        "has_more": True,
        "rows_are_page": True,
        "collection_complete": False,
        "identity_scope": "catalogue_union_historical_facts",
        "sort": {
            "by": "default",
            "direction": "desc",
            "stable_tiebreaker": "external_id",
        },
        "filters": {
            "search": "",
            "active_only": False,
            "campaign_id": None,
            "ad_squad_id": None,
        },
    }
    assert result["read_diagnostics"]["python_entity_rows_materialized"] == 25
    assert result["read_diagnostics"]["max_entity_rows_materialized"] == 25
    assert result["rows"][0]["campaign_name"] == "Campaign 00000"


@pytest.mark.asyncio
async def test_page_two_is_stable_without_duplicates_or_missing_boundary_rows():
    db = FakeDB(_campaigns())
    first = await _read(db, EntityPageSpec(page=1))
    second = await _read(db, EntityPageSpec(page=2))
    first_ids = [row["external_id"] for row in first["rows"]]
    second_ids = [row["external_id"] for row in second["rows"]]

    assert len(second_ids) == 25
    assert set(first_ids).isdisjoint(second_ids)
    assert first_ids + second_ids == [
        f"campaign-{index:05d}" for index in range(0, 100, 2)
    ]


@pytest.mark.asyncio
async def test_active_search_and_sort_apply_before_pagination():
    db = FakeDB(_campaigns())
    result = await _read(
        db,
        EntityPageSpec(
            page=1,
            page_size=25,
            search="campaign-049",
            active_only=True,
            sort_by="name",
            sort_direction="asc",
        ),
    )

    assert result["pagination"]["filtered_total"] == 50
    assert result["pagination"]["collection_complete"] is False
    assert len(result["rows"]) == 25
    assert all(row["active"] is True for row in result["rows"])
    assert all("campaign-049" in row["external_id"] for row in result["rows"])
    assert [row["external_id"] for row in result["rows"]] == sorted(
        row["external_id"] for row in result["rows"]
    )


@pytest.mark.asyncio
async def test_collection_complete_requires_the_full_unfiltered_identity_scope():
    result = await _read(FakeDB(_campaigns(5)), EntityPageSpec())

    assert result["pagination"]["total"] == 5
    assert result["pagination"]["collection_complete"] is True


@pytest.mark.asyncio
async def test_active_only_uses_normalized_operational_status_not_catalogue_presence():
    source_rows = [
        ("campaign-active", {"status": "ACTIVE"}),
        ("campaign-paused", {"status": "PAUSED"}),
        ("campaign-effective", {"status": "PAUSED", "effective_status": "ACTIVE"}),
        ("campaign-unknown", {"status": None}),
        ("campaign-missing", {"status": "ACTIVE"}),
    ]
    rows = [
        normalize_entity(
            "owner-1",
            "account-1",
            "campaign",
            {"id": entity_id, "name": entity_id, **status_fields},
            sync_run_id="run-1",
        )
        for entity_id, status_fields in source_rows
    ]
    for row in rows:
        row["performance"] = {"source_fact_count": 1, "spend_native": 1}
        row["missing_from_latest_sync"] = False
    rows[-1].update(
        {
            "active": False,
            "missing_from_latest_sync": True,
            "observed_in_latest_sync": False,
            "operationally_active": False,
        }
    )

    result = await _read(
        FakeDB(rows),
        EntityPageSpec(active_only=True),
    )

    assert [row["external_id"] for row in result["rows"]] == [
        "campaign-active",
        "campaign-effective",
    ]
    assert result["rows"][0]["operational_status"] == "ACTIVE"
    assert result["rows"][0]["observed_in_latest_sync"] is True


@pytest.mark.asyncio
async def test_summary_is_filtered_report_wide_not_current_page_only():
    rows = _campaigns(125)
    db = FakeDB(rows)
    result = await _read(db, EntityPageSpec(page=2, page_size=25))

    assert len(result["rows"]) == 25
    assert result["totals"]["entity_count"] == 125
    assert result["totals"]["spend_native"] == sum(
        row["performance"]["spend_native"] for row in rows
    )


def test_parent_filters_are_in_first_match_before_lookup_and_page_limit():
    pipeline = build_entity_page_pipeline(
        user_id="owner-1",
        ad_account_id="account-1",
        entity_type="ad",
        source_collection=SNAPCHAT_HOURLY_FACTS_COLLECTION,
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 1),
        start_utc=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end_utc=datetime(2026, 8, 2, tzinfo=timezone.utc),
        timezone_name="UTC",
        action_report_time="conversion",
        spec=EntityPageSpec(page=3, page_size=25),
        campaign_id="campaign-1",
        ad_squad_id="squad-1",
    )

    assert pipeline[0]["$match"]["campaign_id"] == "campaign-1"
    assert pipeline[0]["$match"]["ad_squad_id"] == "squad-1"
    fact_match = pipeline[2]["$unionWith"]["pipeline"][0]["$match"]
    assert fact_match["campaign_id"] == "campaign-1"
    assert fact_match["ad_squad_id"] == "squad-1"
    item_pipeline = pipeline[-1]["$facet"]["items"]
    assert item_pipeline[1:] == [
        {"$skip": 50},
        {"$limit": 25},
        {"$project": {"performance_rows": 0, "sort_name": 0}},
    ]
    assert not any(
        "$facet" in nested_stage
        for branches in pipeline[-1]["$facet"].values()
        for nested_stage in branches
    )


def test_salla_dependent_sorts_fail_closed_instead_of_claiming_support():
    with pytest.raises(ValueError, match="unsupported Snapchat entity sort"):
        EntityPageSpec(sort_by="salla_sales")


@pytest.mark.asyncio
async def test_unified_reader_marks_first_page_incomplete_and_bounds_management_context(
    monkeypatch,
):
    from unified_marketing.readers import snapchat_v2 as reader

    class IdentityCursor:
        def __init__(self, rows):
            self.rows = rows

        async def to_list(self, length=None):
            return list(self.rows[:length])

    class IdentityCollection:
        def __init__(self, rows):
            self.rows = rows
            self.queries = []

        def find(self, query, _projection):
            self.queries.append(query)
            requested = set(query["external_id"]["$in"])
            return IdentityCursor(
                [row for row in self.rows if row["external_id"] in requested]
            )

    rows = [
        {
            "external_id": f"campaign-{index:05d}",
            "campaign_id": f"campaign-{index:05d}",
            "campaign_name": f"Campaign {index:05d}",
            "name": f"Campaign {index:05d}",
            "status": "ACTIVE",
            "active": True,
            "spend_native": 1,
            "source_fact_count": 1,
            "performance_sync_status": "complete",
        }
        for index in range(25)
    ]
    identities = IdentityCollection(
        [{**row, "raw": {"daily_budget_micro": 1_000_000}} for row in rows]
    )

    class ReaderDB:
        def __getitem__(self, _name):
            return identities

    async def account(*_args, **_kwargs):
        return {
            "ad_account_id": "account-1",
            "display_name": "Account",
            "currency": "SAR",
            "timezone": "Asia/Riyadh",
            "active": True,
        }

    async def performance(*_args, **_kwargs):
        return {
            "rows": deepcopy(rows),
            "totals": {"spend_native": 25, "source_fact_count": 25},
            "performance_sync_status": "complete",
            "pagination": {
                "page": 1,
                "page_size": 25,
                "total": 5_000,
                "filtered_total": 5_000,
                "pages": 200,
                "has_more": True,
                "rows_are_page": True,
                "collection_complete": False,
            },
        }

    async def sar(_db, *, rows, totals, **_kwargs):
        for row in rows:
            row["spend_sar"] = row["spend_native"]
        totals["spend_sar"] = totals["spend_native"]
        return 1.0, {"status": "complete"}

    async def salla(*_args, **_kwargs):
        return {
            "by_campaign": {},
            "orders": [],
            "orders_total": 0,
            "orders_returned": 0,
            "truncated": False,
            "summary": {"coverage_status": "complete"},
        }

    async def carts(*_args, **_kwargs):
        return {"by_campaign": {}, "coverage": {"status": "complete"}}

    monkeypatch.setattr(reader, "get_selected_account", account)
    monkeypatch.setattr(reader, "_entity_performance_report", performance)
    monkeypatch.setattr(reader, "_add_sar_spend", sar)
    monkeypatch.setattr(reader, "load_salla_campaign_outcomes", salla)
    monkeypatch.setattr(reader, "load_abandoned_cart_outcomes", carts)

    report = await reader.load_snapchat_v2_entity_report(
        ReaderDB(),
        "owner-1",
        entity_level="campaign",
        date_from=date(2026, 9, 1),
        date_to=date(2026, 9, 1),
        timezone_name="Asia/Riyadh",
    )

    assert report["entity_collection_complete"] is False
    assert report["pagination"]["filtered_total"] == 5_000
    assert report["decision_eligibility"] == {
        "eligible": False,
        "reason": "paginated_entity_sample_not_account_complete",
    }
    assert len(report["management_context"]) == 25
    assert identities.queries[0]["external_id"]["$in"] == [
        f"campaign-{index:05d}" for index in range(25)
    ]
