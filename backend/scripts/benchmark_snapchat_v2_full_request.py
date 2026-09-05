"""Real-Mongo acceptance benchmark for the Snapchat V2 campaign page.

This script creates and drops a uniquely named database and rejects every
non-loopback Mongo URL. CI supplies its disposable Mongo service. The measured
chain is the production campaign-page HTTP route followed by the visible-ID
settings read made by ``/snapchat-accounts``.

Run from ``backend``:

    SNAPCHAT_V2_TEST_MONGO_URL=mongodb://127.0.0.1:27017 \
      python scripts/benchmark_snapchat_v2_full_request.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import tracemalloc
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import monitoring
from pymongo.errors import OperationFailure

from integrations_control_center.snapchat_campaign_profitability import (
    _load_cost_context,
)
from integrations_control_center.snapchat_entity_settings import (
    SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
    list_financial_management_settings,
)
from integrations_control_center.snapchat_native_data_common import (
    SNAPCHAT_ENTITY_COLLECTION,
)
from product_v2_routes import PRODUCTS
from snapchat_v2.accounts import SNAPCHAT_ACCOUNTS_COLLECTION
from snapchat_v2.entities import (
    SNAPCHAT_ENTITY_FACTS_COLLECTION,
    ensure_entity_indexes,
    normalize_entity,
)
from snapchat_v2.entity_pagination import (
    EntityPageSpec,
    build_entity_page_pipeline,
)
from snapchat_v2.facts import SNAPCHAT_HOURLY_FACTS_COLLECTION, ensure_fact_indexes
from snapchat_v2.models import (
    DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
    DEFAULT_VIEW_ATTRIBUTION_WINDOW,
    build_attribution_key,
)
from snapchat_v2.read_indexes import ensure_snapchat_v2_read_indexes
from snapchat_v2.routes import attach_snapchat_v2_routes


CAMPAIGNS = 5_000
AD_SQUADS = 10_000
ADS = 20_000
PRODUCTS_IN_CATALOG = 5_000
VISIBLE_ORDERS = 25
PAGE_SIZE = 25
USER_ID = "snap-v2-full-request-benchmark"
ACCOUNT_ID = "snap-v2-full-request-account"
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


class ReadCommandCounter(monitoring.CommandListener):
    """Count only read commands issued against the disposable database."""

    def __init__(self) -> None:
        self.database_name = ""
        self.enabled = False
        self._commands: Counter[str] = Counter()
        self._collections: Counter[str] = Counter()
        self._lock = threading.Lock()

    def started(self, event: Any) -> None:
        if not self.enabled or event.database_name != self.database_name:
            return
        name = str(event.command_name)
        if name not in {"aggregate", "find", "getMore", "count", "distinct"}:
            return
        collection = str(event.command.get(name) or "")
        with self._lock:
            self._commands[name] += 1
            if collection:
                self._collections[f"{name}:{collection}"] += 1

    def succeeded(self, _event: Any) -> None:
        return None

    def failed(self, _event: Any) -> None:
        return None

    def reset(self) -> None:
        with self._lock:
            self._commands.clear()
            self._collections.clear()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            commands = dict(sorted(self._commands.items()))
            collections = dict(sorted(self._collections.items()))
        return {
            "commands": commands,
            "collections": collections,
            "read_commands": sum(commands.values()),
        }


async def _insert_batches(collection: Any, rows: Any, *, batch_size: int = 1_000) -> None:
    batch: list[dict[str, Any]] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            await collection.insert_many(batch, ordered=False)
            batch = []
    if batch:
        await collection.insert_many(batch, ordered=False)


def _entity_rows() -> Any:
    for index in range(CAMPAIGNS):
        yield normalize_entity(
            USER_ID,
            ACCOUNT_ID,
            "campaign",
            {
                "id": f"campaign-{index:05d}",
                "name": f"Campaign {index:05d}",
                "status": "ACTIVE",
            },
            sync_run_id="benchmark-run",
        )
    for index in range(AD_SQUADS):
        yield normalize_entity(
            USER_ID,
            ACCOUNT_ID,
            "ad_squad",
            {
                "id": f"squad-{index:05d}",
                "name": f"Ad Squad {index:05d}",
                "status": "ACTIVE",
                "campaign_id": f"campaign-{index // 2:05d}",
            },
            sync_run_id="benchmark-run",
        )
    for index in range(ADS):
        yield normalize_entity(
            USER_ID,
            ACCOUNT_ID,
            "ad",
            {
                "id": f"ad-{index:05d}",
                "name": f"Ad {index:05d}",
                "status": "ACTIVE",
                "campaign_id": f"campaign-{index // 4:05d}",
                "ad_squad_id": f"squad-{index // 2:05d}",
            },
            sync_run_id="benchmark-run",
        )


def _hourly_rows() -> Any:
    def fact(entity_type: str, entity_id: str, spend: float, **parents: Any) -> dict[str, Any]:
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
            "impressions": 100,
            "swipes": 5,
            "video_views": 2,
            "view_completion": 1,
            "view_content": 1,
            "add_to_cart": 1,
            "start_checkout": 1,
            "add_billing": 1,
            "purchases": 1,
            "purchase_value_native": spend * 2,
        }

    for index in range(CAMPAIGNS):
        yield fact("campaign", f"campaign-{index:05d}", float(CAMPAIGNS - index))
    for index in range(AD_SQUADS):
        yield fact(
            "ad_squad",
            f"squad-{index:05d}",
            1.0,
            campaign_id=f"campaign-{index // 2:05d}",
        )
    for index in range(ADS):
        yield fact(
            "ad",
            f"ad-{index:05d}",
            0.5,
            campaign_id=f"campaign-{index // 4:05d}",
            ad_squad_id=f"squad-{index // 2:05d}",
        )


def _native_setting_rows(observed_at: datetime) -> Any:
    for index in range(CAMPAIGNS):
        entity_id = f"campaign-{index:05d}"
        yield {
            "user_id": USER_ID,
            "provider": "snapchat_ads",
            "ad_account_id": ACCOUNT_ID,
            "entity_type": "campaign",
            "external_id": entity_id,
            "display_name": f"Campaign {index:05d}",
            "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
            "last_observed_at": observed_at,
            "deleted": False,
            "provider_snapshot": {
                "id": entity_id,
                "name": f"Campaign {index:05d}",
                "status": "ACTIVE",
            },
        }
    for index in range(AD_SQUADS):
        entity_id = f"squad-{index:05d}"
        campaign_id = f"campaign-{index // 2:05d}"
        yield {
            "user_id": USER_ID,
            "provider": "snapchat_ads",
            "ad_account_id": ACCOUNT_ID,
            "entity_type": "ad_squad",
            "external_id": entity_id,
            "campaign_id": campaign_id,
            "display_name": f"Ad Squad {index:05d}",
            "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
            "last_observed_at": observed_at,
            "deleted": False,
            "provider_snapshot": {
                "id": entity_id,
                "campaign_id": campaign_id,
                "name": f"Ad Squad {index:05d}",
                "status": "ACTIVE",
                "daily_budget_micro": 1_000_000,
                "bid_micro": 100_000,
                "bid_strategy": "TARGET_COST",
            },
        }


async def _seed(db: Any) -> None:
    observed_at = datetime.now(timezone.utc)
    started_at = observed_at - timedelta(minutes=1)
    finished_at = observed_at + timedelta(minutes=1)
    await _insert_batches(db[SNAPCHAT_ENTITY_FACTS_COLLECTION], _entity_rows())
    await _insert_batches(db[SNAPCHAT_HOURLY_FACTS_COLLECTION], _hourly_rows())
    await _insert_batches(db[SNAPCHAT_ENTITY_COLLECTION], _native_setting_rows(observed_at))
    await _insert_batches(
        db[PRODUCTS],
        (
            {
                "user_id": USER_ID,
                "id": f"product-{index:05d}",
                "salla_product_id": f"product-{index:05d}",
                "mezan_product_id": f"mezan-product-{index:05d}",
                "name": f"Product {index:05d}",
                "sku": f"SKU-{index:05d}",
                "cost_price_from_salla": 20.0,
                "variants": [],
            }
            for index in range(PRODUCTS_IN_CATALOG)
        ),
    )
    await db[SNAPCHAT_ACCOUNTS_COLLECTION].insert_one(
        {
            "user_id": USER_ID,
            "provider": "snapchat_ads",
            "ad_account_id": ACCOUNT_ID,
            "external_account_id": ACCOUNT_ID,
            "display_name": "Full request benchmark account",
            "currency": "SAR",
            "timezone": "Asia/Riyadh",
            "selected": True,
            "active": True,
        }
    )
    await db["mezan_integration_accounts_v2"].insert_one(
        {
            "user_id": USER_ID,
            "provider": "snapchat_ads",
            "ad_account_id": ACCOUNT_ID,
            "external_account_id": ACCOUNT_ID,
            "currency": "SAR",
        }
    )
    await db["mezan_snapchat_sync_runs_v2"].insert_one(
        {
            "user_id": USER_ID,
            "provider": "snapchat_ads",
            "ad_account_id": ACCOUNT_ID,
            "run_id": "benchmark-run",
            "run_type": "analytics_refresh",
            "status": "complete",
            # Keep the entity route on hourly facts while proving a complete
            # native settings observation window.
            "campaign_sync_status": "partial",
            "ad_squad_sync_status": "partial",
            "ad_sync_status": "partial",
            "started_at": started_at,
            "finished_at": finished_at,
        }
    )
    await db.settings.insert_one(
        {
            "user_id": USER_ID,
            "report_included_statuses": ["DELIVERED"],
            "hide_inferred_date_orders": False,
        }
    )
    await _insert_batches(
        db.unified_orders,
        (
            {
                "user_id": USER_ID,
                "order_number": f"benchmark-order-{index:02d}",
                "created_at": datetime(2026, 9, 1, 8, index, tzinfo=timezone.utc),
                "order_date": REPORT_DATE.isoformat(),
                "order_status": "DELIVERED",
                "total_amount": 100.0,
                # Deliberately stale: the response must use current catalog
                # cost 20, not this snapshot.
                "total_product_cost": 999.0,
                "products": [
                    {
                        "product_id": f"product-{index:05d}",
                        "sku": f"SKU-{index:05d}",
                        "quantity": 1,
                        "total": 100.0,
                    }
                ],
                "raw_by_source": {
                    "salla_direct": {
                        "campaign_id": f"campaign-{index:05d}",
                        "ad_platform_source": "snapchat",
                    }
                },
            }
            for index in range(VISIBLE_ORDERS)
        ),
    )

    # These indexes exist only in the UUID-named disposable database and are
    # dropped with it.  No existing or production index is touched.
    await ensure_entity_indexes(db)
    await ensure_fact_indexes(db)
    await ensure_snapchat_v2_read_indexes(db)
    await db.unified_orders.create_index([("user_id", 1), ("order_date", 1)])
    await db[PRODUCTS].create_index([("user_id", 1), ("salla_product_id", 1)])


def _measure_started() -> float:
    tracemalloc.start()
    return time.perf_counter()


def _measure_finished(started: float) -> dict[str, Any]:
    elapsed_ms = (time.perf_counter() - started) * 1_000
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {
        "wall_ms": round(elapsed_ms, 3),
        "python_peak_traced_bytes": peak,
    }


def _execution_stat_nodes(value: Any, path: str = "$") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "totalDocsExamined" in value and "nReturned" in value:
            rows.append(
                {
                    "path": path,
                    "stage": value.get("stage"),
                    "total_docs_examined": int(value.get("totalDocsExamined") or 0),
                    "n_returned": int(value.get("nReturned") or 0),
                }
            )
        for key, child in value.items():
            rows.extend(_execution_stat_nodes(child, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            rows.extend(_execution_stat_nodes(child, f"{path}[{index}]"))
    return rows


async def _run() -> dict[str, Any]:
    mongo_url = os.environ.get("SNAPCHAT_V2_TEST_MONGO_URL", "").strip()
    if not mongo_url:
        raise SystemExit("SNAPCHAT_V2_TEST_MONGO_URL is required")
    if not mongo_url.startswith(
        ("mongodb://127.0.0.1", "mongodb://localhost", "mongodb://[::1]")
    ):
        raise SystemExit("benchmark refuses non-loopback MongoDB URLs")
    listener = ReadCommandCounter()
    client = AsyncIOMotorClient(
        mongo_url,
        serverSelectionTimeoutMS=5_000,
        event_listeners=[listener],
    )
    await client.admin.command("ping")
    database_name = f"snap_v2_full_request_{uuid.uuid4().hex}"
    listener.database_name = database_name
    db = client[database_name]
    try:
        await _seed(db)

        listener.reset()
        listener.enabled = True
        before_started = _measure_started()
        before_error = None
        try:
            await db[SNAPCHAT_ENTITY_FACTS_COLLECTION].aggregate(
                [
                    {"$match": {"user_id": USER_ID, "entity_type": "campaign"}},
                    {
                        "$facet": {
                            "catalog_count": [{"$count": "value"}],
                            "filtered": [
                                {
                                    "$facet": {
                                        "items": [{"$limit": PAGE_SIZE}],
                                        "count": [{"$count": "value"}],
                                    }
                                }
                            ],
                        }
                    },
                ],
                allowDiskUse=False,
                maxTimeMS=15_000,
            ).to_list(length=1)
        except OperationFailure as exc:
            before_error = {
                "type": type(exc).__name__,
                "code": exc.code,
                "contains_facet_rejection": "$facet" in str(exc),
            }
        before_measurement = _measure_finished(before_started)
        before_commands = listener.snapshot()
        listener.enabled = False
        if not before_error:
            raise AssertionError("legacy nested $facet unexpectedly executed")

        listener.reset()
        listener.enabled = True
        broad_started = _measure_started()
        broad_context = await _load_cost_context(db, USER_ID)
        broad_measurement = _measure_finished(broad_started)
        broad_commands = listener.snapshot()
        listener.enabled = False

        async def current_user() -> dict[str, str]:
            return {"id": USER_ID}

        router = APIRouter(prefix="/api/integrations-v2")
        attach_snapchat_v2_routes(router, db, current_user, lambda user: user)
        app = FastAPI()
        app.include_router(router)
        transport = ASGITransport(app=app)

        listener.reset()
        listener.enabled = True
        after_started = _measure_started()
        async with AsyncClient(transport=transport, base_url="http://benchmark") as http:
            response = await http.get(
                "/api/integrations-v2/snapchat-v2/campaigns",
                params={
                    "date_from": REPORT_DATE.isoformat(),
                    "date_to": REPORT_DATE.isoformat(),
                    "timezone": "account",
                    "action_report_time": "conversion",
                    "page": 1,
                    "page_size": PAGE_SIZE,
                },
            )
        response.raise_for_status()
        body = response.json()
        visible_ids = [str(row["campaign_id"]) for row in body["campaigns"]]
        settings = await list_financial_management_settings(
            db,
            USER_ID,
            entity_type="campaign",
            unified_entity_ids=visible_ids,
            limit=PAGE_SIZE,
        )
        settings_bytes = len(
            json.dumps(settings, default=str, separators=(",", ":")).encode("utf-8")
        )
        after_measurement = _measure_finished(after_started)
        after_commands = listener.snapshot()
        listener.enabled = False

        page_pipeline = build_entity_page_pipeline(
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
            spec=EntityPageSpec(page=1, page_size=PAGE_SIZE),
        )
        explain = await db.command(
            {
                "explain": {
                    "aggregate": SNAPCHAT_ENTITY_FACTS_COLLECTION,
                    "pipeline": page_pipeline,
                    "cursor": {},
                },
                "verbosity": "executionStats",
            }
        )

        detail_cost = dict(body["salla"].get("cost_read_diagnostics") or {})
        summary_cost = dict(body["salla"]["summary"].get("cost_read_diagnostics") or {})
        if len(body["campaigns"]) != PAGE_SIZE:
            raise AssertionError("campaign page was not bounded to 25 rows")
        if settings["settings_rows_materialized"] != PAGE_SIZE:
            raise AssertionError("settings read was not bounded to the visible IDs")
        if detail_cost.get("products_materialized") != VISIBLE_ORDERS:
            raise AssertionError("row cost context was not identity-bounded")
        if summary_cost.get("products_materialized") != VISIBLE_ORDERS:
            raise AssertionError("summary cost context was not identity-bounded")

        return {
            "benchmark": "isolated_real_mongo_campaign_page_request_chain",
            "environment": {
                "database": "uuid_named_disposable_database",
                "production_data_or_indexes_touched": False,
                "mongo_url_redacted": True,
            },
            "dataset": {
                "campaigns": CAMPAIGNS,
                "ad_squads": AD_SQUADS,
                "ads": ADS,
                "products": PRODUCTS_IN_CATALOG,
                "orders": VISIBLE_ORDERS,
                "page_size": PAGE_SIZE,
            },
            "before": {
                "outcome": "mongo_rejected_before_response",
                "response_bytes": None,
                "campaign_rows_returned": 0,
                "settings_rows_returned": 0,
                "error": before_error,
                **before_measurement,
                **before_commands,
                "broad_cost_context_probe": {
                    **broad_measurement,
                    **broad_commands,
                    **dict(broad_context.get("read_diagnostics") or {}),
                },
            },
            "after": {
                "outcome": "complete",
                "http_status": response.status_code,
                "response_bytes": len(response.content) + settings_bytes,
                "campaign_response_bytes": len(response.content),
                "settings_response_bytes": settings_bytes,
                "campaign_rows_returned": len(body["campaigns"]),
                "settings_rows_returned": settings["settings_rows_materialized"],
                "ad_squad_entity_page_rows_returned": 0,
                "ad_entity_page_rows_returned": 0,
                "pagination": body["pagination"],
                "row_cost_context": detail_cost,
                "summary_cost_context": summary_cost,
                "summary_profitability": body["salla"]["summary"].get("profitability"),
                **after_measurement,
                **after_commands,
            },
            "execution_stats": {
                "environment": "isolated_ci_mongodb_not_production",
                "nodes": _execution_stat_nodes(explain),
            },
        }
    finally:
        listener.enabled = False
        await client.drop_database(database_name)
        client.close()


def main() -> None:
    print(json.dumps(asyncio.run(_run()), sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
