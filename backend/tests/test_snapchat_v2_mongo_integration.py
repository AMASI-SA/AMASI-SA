from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timezone

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from pymongo.errors import OperationFailure

motor = pytest.importorskip("motor.motor_asyncio")

from snapchat_v2.accounts import SNAPCHAT_ACCOUNTS_COLLECTION
from snapchat_v2.entities import (
    SNAPCHAT_ENTITY_FACTS_COLLECTION,
    normalize_entity,
)
from snapchat_v2.entity_pagination import (
    EntityPageSpec,
    build_entity_page_pipeline,
    read_entity_page,
)
from snapchat_v2.facts import SNAPCHAT_HOURLY_FACTS_COLLECTION
from snapchat_v2.models import (
    DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
    DEFAULT_VIEW_ATTRIBUTION_WINDOW,
    build_attribution_key,
)
from snapchat_v2.routes import attach_snapchat_v2_routes
from snapchat_v2.salla_outcomes import (
    load_salla_campaign_outcomes,
    load_salla_report_summary_aggregate,
)
from snapchat_v2.total_facts import SNAPCHAT_TOTAL_FACTS_COLLECTION


MONGO_URL = os.environ.get("SNAPCHAT_V2_TEST_MONGO_URL", "").strip()
pytestmark = pytest.mark.skipif(
    not MONGO_URL,
    reason="SNAPCHAT_V2_TEST_MONGO_URL is required for isolated real-Mongo tests",
)

USER_ID = "snap-v2-acceptance-owner"
ACCOUNT_ID = "snap-v2-acceptance-account"
REPORT_DATE = date(2026, 9, 1)
START_UTC = datetime(2026, 8, 31, 21, tzinfo=timezone.utc)
END_UTC = datetime(2026, 9, 1, 21, tzinfo=timezone.utc)
ATTRIBUTION_KEY = build_attribution_key(
    "conversion",
    {
        "swipe": DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
        "view": DEFAULT_VIEW_ATTRIBUTION_WINDOW,
    },
)


@pytest_asyncio.fixture
async def mongo_db():
    client = motor.AsyncIOMotorClient(MONGO_URL, serverSelectionTimeoutMS=5_000)
    await client.admin.command("ping")
    database_name = f"snap_v2_acceptance_{uuid.uuid4().hex}"
    db = client[database_name]
    try:
        yield db
    finally:
        await client.drop_database(database_name)
        client.close()


def _entity(entity_type: str, entity_id: str, status: str | None, **parents):
    row = normalize_entity(
        USER_ID,
        ACCOUNT_ID,
        entity_type,
        {
            "id": entity_id,
            "name": f"Name {entity_id}",
            "status": status,
            **parents,
        },
        sync_run_id="acceptance-run",
    )
    row.update(
        {
            "missing_from_latest_sync": False,
            "created_at": START_UTC,
            "updated_at": START_UTC,
        }
    )
    return row


def _fact(entity_type: str, entity_id: str, spend: float, **parents):
    return {
        "user_id": USER_ID,
        "provider": "snapchat_ads",
        "ad_account_id": ACCOUNT_ID,
        "entity_type": entity_type,
        "external_id": entity_id,
        "campaign_id": parents.get("campaign_id"),
        "ad_squad_id": parents.get("ad_squad_id"),
        "hour_start_utc": START_UTC,
        "hour_end_utc": END_UTC,
        "account_timezone": "Asia/Riyadh",
        "currency": "SAR",
        "action_report_time": "conversion",
        "attribution_key": ATTRIBUTION_KEY,
        "spend_native": spend,
        "impressions": 10,
        "swipes": 1,
        "video_views": 0,
        "view_completion": 0,
        "view_content": 0,
        "add_to_cart": 0,
        "start_checkout": 0,
        "add_billing": 0,
        "purchases": 1,
        "purchase_value_native": spend * 2,
    }


async def _seed_hierarchy(db):
    active = _entity("campaign", "campaign-active", "ACTIVE")
    paused = _entity("campaign", "campaign-paused", "PAUSED")
    unknown = _entity("campaign", "campaign-unknown", None)
    missing = _entity("campaign", "campaign-missing", "ACTIVE")
    missing.update(
        {
            "active": False,
            "missing_from_latest_sync": True,
            "observed_in_latest_sync": False,
            "operationally_active": False,
        }
    )
    entities = [
        active,
        paused,
        unknown,
        missing,
        _entity(
            "ad_squad",
            "squad-active",
            "ACTIVE",
            campaign_id="campaign-active",
        ),
        _entity(
            "ad_squad",
            "squad-other",
            "ACTIVE",
            campaign_id="campaign-paused",
        ),
        _entity(
            "ad",
            "ad-active",
            "ACTIVE",
            campaign_id="campaign-active",
            ad_squad_id="squad-active",
        ),
        _entity(
            "ad",
            "ad-other",
            "ACTIVE",
            campaign_id="campaign-paused",
            ad_squad_id="squad-other",
        ),
    ]
    await db[SNAPCHAT_ENTITY_FACTS_COLLECTION].insert_many(entities)
    facts = [
        _fact("campaign", "campaign-active", 30),
        _fact("campaign", "campaign-paused", 20),
        _fact("campaign", "campaign-unknown", 10),
        _fact("campaign", "campaign-missing", 5),
        # Historical spend must survive without a current catalogue row.
        _fact("campaign", "campaign-historical", 40),
        _fact(
            "ad_squad",
            "squad-active",
            15,
            campaign_id="campaign-active",
        ),
        _fact(
            "ad_squad",
            "squad-other",
            9,
            campaign_id="campaign-paused",
        ),
        _fact(
            "ad",
            "ad-active",
            7,
            campaign_id="campaign-active",
            ad_squad_id="squad-active",
        ),
        _fact(
            "ad",
            "ad-other",
            4,
            campaign_id="campaign-paused",
            ad_squad_id="squad-other",
        ),
    ]
    await db[SNAPCHAT_HOURLY_FACTS_COLLECTION].insert_many(facts)


async def _page(db, entity_type: str, **kwargs):
    return await read_entity_page(
        db,
        user_id=USER_ID,
        ad_account_id=ACCOUNT_ID,
        entity_type=entity_type,
        source_collection=SNAPCHAT_HOURLY_FACTS_COLLECTION,
        date_from=REPORT_DATE,
        date_to=REPORT_DATE,
        start_utc=START_UTC,
        end_utc=END_UTC,
        timezone_name="Asia/Riyadh",
        action_report_time="conversion",
        level_status="complete",
        spec=kwargs.pop("spec", EntityPageSpec()),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_real_mongo_reproduces_legacy_nested_facet_rejection(mongo_db):
    await mongo_db["nested_facet_reproduction"].insert_one({"value": 1})
    legacy_pipeline = [
        {
            "$facet": {
                "filtered": [
                    {"$facet": {"items": [{"$limit": 1}]}}
                ]
            }
        }
    ]
    with pytest.raises(OperationFailure, match=r"\$facet.*not allowed"):
        await mongo_db["nested_facet_reproduction"].aggregate(
            legacy_pipeline
        ).to_list(length=1)


@pytest.mark.asyncio
async def test_real_mongo_runs_production_pipeline_for_all_levels_and_explain(mongo_db):
    await _seed_hierarchy(mongo_db)

    campaigns = await _page(mongo_db, "campaign")
    active = await _page(
        mongo_db,
        "campaign",
        spec=EntityPageSpec(active_only=True),
    )
    historical = await _page(
        mongo_db,
        "campaign",
        spec=EntityPageSpec(search="campaign-historical"),
    )
    squads = await _page(
        mongo_db,
        "ad_squad",
        campaign_id="campaign-active",
    )
    ads = await _page(
        mongo_db,
        "ad",
        campaign_id="campaign-active",
        ad_squad_id="squad-active",
    )

    assert campaigns["pagination"]["total"] == 5
    assert [row["external_id"] for row in active["rows"]] == ["campaign-active"]
    assert historical["rows"][0]["spend_native"] == 40
    assert historical["rows"][0]["catalogue_present"] is False
    assert historical["rows"][0]["active"] is False
    assert [row["external_id"] for row in squads["rows"]] == ["squad-active"]
    assert [row["external_id"] for row in ads["rows"]] == ["ad-active"]

    pipeline = build_entity_page_pipeline(
        user_id=USER_ID,
        ad_account_id=ACCOUNT_ID,
        entity_type="campaign",
        source_collection=SNAPCHAT_HOURLY_FACTS_COLLECTION,
        date_from=REPORT_DATE,
        date_to=REPORT_DATE,
        start_utc=START_UTC,
        end_utc=END_UTC,
        timezone_name="Asia/Riyadh",
        action_report_time="conversion",
        spec=EntityPageSpec(page_size=25),
    )
    explain = await mongo_db.command(
        {
            "explain": {
                "aggregate": SNAPCHAT_ENTITY_FACTS_COLLECTION,
                "pipeline": pipeline,
                "cursor": {},
            },
            "verbosity": "executionStats",
        }
    )

    def _execution_stats(value, path="$"):
        records = []
        if isinstance(value, dict):
            if "totalDocsExamined" in value and "nReturned" in value:
                records.append({
                    "path": path,
                    "stage": value.get("stage"),
                    "total_docs_examined": int(value.get("totalDocsExamined") or 0),
                    "n_returned": int(value.get("nReturned") or 0),
                })
            for key, child in value.items():
                records.extend(_execution_stats(child, f"{path}.{key}"))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                records.extend(_execution_stats(child, f"{path}[{index}]"))
        return records

    nodes = _execution_stats(explain)

    evidence = {
        "environment": "isolated_ci_mongodb",
        "execution_stats_nodes": nodes,
        "campaign_rows_returned": len(campaigns["rows"]),
        "python_rows_materialized": campaigns["read_diagnostics"][
            "python_entity_rows_materialized"
        ],
    }
    print("SNAP_V2_MONGO_EXPLAIN=" + json.dumps(evidence, sort_keys=True))
    assert any(node["total_docs_examined"] > 0 for node in nodes)
    assert evidence["campaign_rows_returned"] == 5


@pytest.mark.asyncio
async def test_http_routes_are_parent_scoped_and_preserve_page_completeness(
    mongo_db,
    monkeypatch,
):
    await _seed_hierarchy(mongo_db)
    await mongo_db[SNAPCHAT_ACCOUNTS_COLLECTION].insert_one(
        {
            "user_id": USER_ID,
            "provider": "snapchat_ads",
            "ad_account_id": ACCOUNT_ID,
            "display_name": "Acceptance account",
            "currency": "SAR",
            "timezone": "Asia/Riyadh",
            "selected": True,
            "active": True,
        }
    )
    await mongo_db["mezan_snapchat_sync_runs_v2"].insert_one(
        {
            "user_id": USER_ID,
            "ad_account_id": ACCOUNT_ID,
            "campaign_sync_status": "partial",
            "ad_squad_sync_status": "partial",
            "ad_sync_status": "partial",
            "started_at": START_UTC,
        }
    )

    async def _sar(_db, *, rows, totals, **_kwargs):
        for row in rows:
            row["spend_sar"] = row.get("spend_native")
        totals["spend_sar"] = totals.get("spend_native")
        return 1.0, {"status": "complete"}

    async def _salla_detail(*_args, **_kwargs):
        return {
            "by_campaign": {},
            "summary": {"coverage_status": "complete"},
            "orders": [],
            "orders_total": 0,
            "orders_returned": 0,
            "truncated": False,
        }

    async def _salla_summary(*_args, **_kwargs):
        return {
            "coverage_status": "complete",
            "snapchat_attributed_orders": 0,
            "snapchat_attributed_sales_sar": 0,
            "campaign_matched_orders": 0,
            "campaign_matched_financial_sales_sar": 0,
            "snapchat_attribution_gap_orders": 0,
            "campaign_match_coverage_pct": None,
            "profitability": {"coverage_status": "complete"},
        }

    async def _carts(*_args, **_kwargs):
        return {"by_campaign": {}, "coverage": {"status": "complete"}}

    monkeypatch.setattr("snapchat_v2.routes._add_sar_spend", _sar)
    monkeypatch.setattr(
        "snapchat_v2.routes.load_salla_campaign_outcomes",
        _salla_detail,
    )
    monkeypatch.setattr(
        "snapchat_v2.routes.load_salla_report_summary_aggregate",
        _salla_summary,
    )
    monkeypatch.setattr("snapchat_v2.routes.load_abandoned_cart_outcomes", _carts)

    async def current_user():
        return {"id": USER_ID}

    router = APIRouter(prefix="/api/integrations-v2")
    attach_snapchat_v2_routes(router, mongo_db, current_user, lambda user: user)
    app = FastAPI()
    app.include_router(router)
    transport = ASGITransport(app=app)
    common = "date_from=2026-09-01&date_to=2026-09-01&timezone=account"
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        campaign_response = await client.get(
            f"/api/integrations-v2/snapchat-v2/campaigns?{common}&page_size=1"
        )
        missing_squad_parent = await client.get(
            f"/api/integrations-v2/snapchat-v2/ad-squads?{common}"
        )
        squad_response = await client.get(
            f"/api/integrations-v2/snapchat-v2/ad-squads?{common}"
            "&campaign_id=campaign-active"
        )
        missing_ad_parent = await client.get(
            f"/api/integrations-v2/snapchat-v2/ads?{common}"
            "&campaign_id=campaign-active"
        )
        ad_response = await client.get(
            f"/api/integrations-v2/snapchat-v2/ads?{common}"
            "&campaign_id=campaign-active&ad_squad_id=squad-active"
        )

    assert campaign_response.status_code == 200
    campaign_body = campaign_response.json()
    assert len(campaign_body["campaigns"]) == 1
    assert campaign_body["pagination"]["collection_complete"] is False
    assert campaign_body["unified"]["entity_collection_complete"] is False
    assert missing_squad_parent.status_code == 422
    assert squad_response.status_code == 200
    assert [row["external_id"] for row in squad_response.json()["ad_squads"]] == [
        "squad-active"
    ]
    assert missing_ad_parent.status_code == 422
    assert ad_response.status_code == 200
    assert [row["external_id"] for row in ad_response.json()["ads"]] == [
        "ad-active"
    ]


@pytest.mark.asyncio
async def test_real_mongo_row_and_summary_share_exact_cost_profit_scope(mongo_db):
    await mongo_db[SNAPCHAT_ENTITY_FACTS_COLLECTION].insert_one(
        _entity("campaign", "campaign-cost", "ACTIVE")
    )
    await mongo_db.settings.insert_one(
        {
            "user_id": USER_ID,
            "report_included_statuses": ["DELIVERED"],
            "hide_inferred_date_orders": False,
        }
    )
    orders = [
        {
            "user_id": USER_ID,
            "order_number": f"cost-order-{index}",
            "created_at": datetime(2026, 9, 1, 7 + index, tzinfo=timezone.utc),
            "order_date": "2026-09-01",
            "order_status": "DELIVERED",
            "total_amount": 100,
            "total_product_cost": stored,
            "products": [
                {
                    "product_id": "product-cost",
                    "quantity": 1,
                    "total": 100,
                    "options": [{"name": "Wrap", "value": "Premium"}],
                }
            ],
            "raw_by_source": {
                "salla_direct": {
                    "ad_platform_source": "snapchat",
                    "campaign_id": "campaign-cost",
                }
            },
        }
        for index, stored in enumerate((None, 999))
    ]
    await mongo_db.unified_orders.insert_many(orders)
    await mongo_db.mezan_products_v2.insert_one(
        {
            "user_id": USER_ID,
            "id": "product-cost",
            "salla_product_id": "product-cost",
            "cost_price_from_salla": 90,
            "variants": [],
        }
    )
    await mongo_db.mezan_product_cost_profiles_v2.insert_one(
        {
            "user_id": USER_ID,
            "salla_product_id": "product-cost",
            "base_cost": 10,
        }
    )
    await mongo_db.mezan_product_resource_bindings_v2.insert_many(
        [
            {
                "user_id": USER_ID,
                "id": "component-binding",
                "salla_product_id": "product-cost",
                "resource_id": "component-resource",
                "quantity": 2,
            },
            {
                "user_id": USER_ID,
                "id": "service-binding",
                "salla_product_id": "product-cost",
                "resource_id": "service-resource",
                "quantity": 1,
            },
        ]
    )
    await mongo_db.mezan_cost_resources_v2.insert_many(
        [
            {
                "user_id": USER_ID,
                "id": "component-resource",
                "kind": "component",
                "unit_cost": 5,
            },
            {
                "user_id": USER_ID,
                "id": "service-resource",
                "kind": "service",
                "unit_cost": 4,
            },
        ]
    )
    await mongo_db.mezan_product_option_cost_bindings_v2.insert_one(
        {
            "user_id": USER_ID,
            "id": "option-binding",
            "salla_product_id": "product-cost",
            "option_name": "Wrap",
            "value_name": "Premium",
            "mode": "direct",
            "direct_amount": 3,
        }
    )

    detail = await load_salla_campaign_outcomes(
        mongo_db,
        USER_ID,
        account_id=ACCOUNT_ID,
        date_from=REPORT_DATE,
        date_to=REPORT_DATE,
        timezone_name="Asia/Riyadh",
        identities=[{
            "account_id": ACCOUNT_ID,
            "campaign_id": "campaign-cost",
            "campaign_name": "Name campaign-cost",
        }],
        campaign_spend_sar={"campaign-cost": 20},
        restrict_to_identities=True,
    )
    summary = await load_salla_report_summary_aggregate(
        mongo_db,
        USER_ID,
        account_id=ACCOUNT_ID,
        date_from=REPORT_DATE,
        date_to=REPORT_DATE,
        timezone_name="Asia/Riyadh",
        spend_sar=20,
    )

    row_profit = detail["by_campaign"]["campaign-cost"]["profitability"]
    summary_profit = summary["profitability"]
    assert row_profit["orders"] == summary_profit["orders"] == 2
    assert row_profit["sales_sar"] == summary_profit["sales_sar"] == 200
    # Each order uses the same current engine: 10 base + 10 component +
    # 4 service + 3 selected option = 27, irrespective of stored None/999.
    assert row_profit["product_cost_sar"] == summary_profit["product_cost_sar"] == 54
    assert row_profit["contribution_profit_sar"] == summary_profit[
        "contribution_profit_sar"
    ] == 126
    assert summary_profit["stored_cost_missing_orders"] == 1
    assert summary_profit["stored_cost_mismatch_orders"] == 1
    assert summary_profit["stored_total_product_cost_used"] is False
    assert summary["coverage_status"] == "complete"
    assert summary["cost_read_diagnostics"]["products_materialized"] == 1
    assert summary["cost_read_diagnostics"]["product_bindings_materialized"] == 2
    assert summary["cost_read_diagnostics"]["option_bindings_materialized"] == 1
    assert summary["cost_read_diagnostics"]["resources_materialized"] == 2
    print(
        "SNAP_V2_COST_PARITY="
        + json.dumps(
            {
                "environment": "isolated_ci_mongodb",
                "row": {
                    "orders": row_profit["orders"],
                    "sales_sar": row_profit["sales_sar"],
                    "product_cost_sar": row_profit["product_cost_sar"],
                    "contribution_profit_sar": row_profit[
                        "contribution_profit_sar"
                    ],
                },
                "summary": {
                    "orders": summary_profit["orders"],
                    "sales_sar": summary_profit["sales_sar"],
                    "product_cost_sar": summary_profit["product_cost_sar"],
                    "contribution_profit_sar": summary_profit[
                        "contribution_profit_sar"
                    ],
                    "stored_cost_missing_orders": summary_profit[
                        "stored_cost_missing_orders"
                    ],
                    "stored_cost_mismatch_orders": summary_profit[
                        "stored_cost_mismatch_orders"
                    ],
                    "stored_total_product_cost_used": summary_profit[
                        "stored_total_product_cost_used"
                    ],
                },
                "cost_read_diagnostics": summary["cost_read_diagnostics"],
            },
            sort_keys=True,
        )
    )
