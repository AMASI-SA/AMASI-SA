from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

import dashboard_v2_routes
import product_v2_routes
from integrations_control_center import campaign_product_associations as product_links
from integrations_control_center import snapchat_campaign_profitability as profitability
from integrations_control_center import (
    snapchat_campaign_result_source_routes as result_source,
)
from integrations_control_center import snapchat_decision_metrics as metrics


class _Cursor:
    def __init__(self, rows):
        self.rows = [deepcopy(row) for row in rows]

    async def to_list(self, length=None):
        rows = self.rows if length is None else self.rows[:length]
        return deepcopy(rows)


class _Collection:
    def __init__(self, rows=None):
        self.rows = [deepcopy(row) for row in (rows or [])]

    def find(self, query, projection=None):
        return _Cursor(self.rows)

    async def find_one(self, query, projection=None):
        if not self.rows:
            return None
        return deepcopy(self.rows[0])


class _DB:
    def __init__(self, *, products=None, sync_runs=None):
        self.collections = {
            product_v2_routes.PRODUCTS: _Collection(products),
            metrics.SNAPCHAT_PERFORMANCE_COLLECTION: _Collection(),
            metrics.SNAPCHAT_ENTITY_COLLECTION: _Collection(),
            metrics.SNAPCHAT_SYNC_RUN_COLLECTION: _Collection(sync_runs),
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, _Collection())

    def __getattr__(self, name):
        return self[name]


def _prepare_snapshot_dependencies(monkeypatch, *, confirmed_links=None):
    hierarchy_calls = []

    async def no_orders(*args, **kwargs):
        return []

    async def no_costs(*args, **kwargs):
        return {}

    async def no_identities(*args, **kwargs):
        return []

    async def linked(*args, **kwargs):
        hierarchy_calls.append(deepcopy(kwargs))
        return deepcopy(confirmed_links or [])

    monkeypatch.setattr(dashboard_v2_routes, "_filtered_orders", no_orders)
    monkeypatch.setattr(profitability, "_load_cost_context", no_costs)
    monkeypatch.setattr(result_source, "_campaign_identities", no_identities)
    monkeypatch.setattr(product_links, "list_effective_campaign_products", linked)
    return hierarchy_calls


def _complete_native_sync_run(
    *,
    account_id="account-1",
    date_from="2026-07-01",
    date_to="2026-08-12",
):
    return {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "run_type": "analytics_refresh",
        "status": "complete",
        "source_mode": metrics.SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
        "run_id": "native-sync-complete-1",
        "finished_at": "2026-08-12T10:05:00+00:00",
        "summary": {
            "date_from": date_from,
            "date_to": date_to,
            "accounts_attempted": 1,
            "accounts_complete": 1,
            "errors_count": 0,
            "entity_counts": {account_id: {"campaign": 0}},
        },
    }


def _complete_scheduler_campaign_run(
    *,
    run_id,
    date_from,
    date_to,
    account_id="account-1",
    campaign_source_mode=metrics.SCHEDULER_CAMPAIGN_FACTS_SOURCE_MODE,
    schema_version=metrics.SCHEDULER_CAMPAIGN_FACTS_SCHEMA_VERSION,
):
    return {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "run_type": "analytics_refresh",
        "status": "complete",
        # Scheduler top-level source remains the account refresh contract; the
        # independent summary marker proves campaign rows were also persisted.
        "source_mode": "snapchat_account_hourly_campaign_breakdown_riyadh_refresh_v3",
        "run_id": run_id,
        "finished_at": f"{date_to}T23:59:00+00:00",
        "summary": {
            "date_from": date_from,
            "date_to": date_to,
            "accounts_attempted": 1,
            "accounts_complete": 1,
            "errors_count": 0,
            "campaign_facts_source_mode": campaign_source_mode,
            "campaign_facts_schema_version": schema_version,
            "account_provider_calls": [
                {"ad_account_id": account_id, "provider_calls": 1}
            ],
        },
    }


def _product(*, units, source, name="منتج أ"):
    return {
        "salla_product_id": "101",
        "mezan_product_id": "mpv2_101",
        "name": name,
        "sku": "A-1",
        "orders": int(units),
        "units": units,
        "sales_sar": units * 100,
        "sources": {
            source: {
                "source": source,
                "orders": int(units),
                "units": units,
                "sales_sar": units * 100,
                "source_verified_from_order": source != "unknown",
            }
        },
    }


@pytest.mark.asyncio
async def test_zero_performance_rows_require_durable_native_range_proof(monkeypatch):
    captured_at = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
    _prepare_snapshot_dependencies(monkeypatch)

    missing = await metrics.capture_decision_baseline(
        _DB(),
        "owner-1",
        account_id="account-1",
        campaign_id="campaign-1",
        captured_at=captured_at,
    )
    proven_zero = await metrics.capture_decision_baseline(
        _DB(sync_runs=[_complete_native_sync_run()]),
        "owner-1",
        account_id="account-1",
        campaign_id="campaign-1",
        captured_at=captured_at,
    )

    assert missing["coverage"]["campaign_performance_rows_observed"] == 0
    assert missing["coverage"]["complete"] is False
    assert missing["coverage"]["campaign_performance_sync"]["status"] == (
        "campaign_performance_sync_dates_missing"
    )
    # A fully successful durable run disambiguates a true provider zero from
    # absent facts without using row_count as the completeness gate.
    assert proven_zero["coverage"]["campaign_performance_rows_observed"] == 0
    assert proven_zero["coverage"]["complete"] is True
    assert {
        proof["run_id"]
        for proof in proven_zero["coverage"]["campaign_performance_sync"][
            "proofs"
        ]
    } == {"native-sync-complete-1"}


@pytest.mark.asyncio
async def test_account_hourly_scheduler_run_cannot_prove_campaign_coverage(
    monkeypatch,
):
    captured_at = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
    hourly = _complete_native_sync_run()
    hourly["source_mode"] = "snapchat_account_hourly_campaign_breakdown_riyadh_refresh_v3"
    _prepare_snapshot_dependencies(monkeypatch)

    baseline = await metrics.capture_decision_baseline(
        _DB(sync_runs=[hourly]),
        "owner-1",
        account_id="account-1",
        campaign_id="campaign-1",
        captured_at=captured_at,
    )

    assert baseline["coverage"]["complete"] is False
    assert baseline["coverage"]["campaign_performance_sync"]["proofs"] == []


@pytest.mark.asyncio
async def test_union_of_scheduler_v4_ranges_can_cover_window(monkeypatch):
    captured_at = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
    runs = [
        _complete_scheduler_campaign_run(
            run_id="scheduler-v4-a", date_from="2026-07-29", date_to="2026-08-05"
        ),
        _complete_scheduler_campaign_run(
            run_id="scheduler-v4-b", date_from="2026-08-06", date_to="2026-08-12"
        ),
    ]
    _prepare_snapshot_dependencies(monkeypatch)

    baseline = await metrics.capture_decision_baseline(
        _DB(sync_runs=runs),
        "owner-1",
        account_id="account-1",
        campaign_id="campaign-1",
        captured_at=captured_at,
    )

    assert baseline["coverage"]["complete"] is True
    fourteen = next(row for row in baseline["windows"] if row["days"] == 14)
    assert fourteen["coverage"]["complete"] is True
    assert {proof["run_id"] for proof in fourteen["coverage"]["proofs"]} == {
        "scheduler-v4-a",
        "scheduler-v4-b",
    }


@pytest.mark.asyncio
async def test_union_gap_and_scheduler_without_v4_schema_fail_closed(monkeypatch):
    captured_at = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
    runs = [
        _complete_scheduler_campaign_run(
            run_id="scheduler-before-gap",
            date_from="2026-07-29",
            date_to="2026-08-05",
        ),
        _complete_scheduler_campaign_run(
            run_id="scheduler-after-gap",
            date_from="2026-08-07",
            date_to="2026-08-12",
        ),
        _complete_scheduler_campaign_run(
            run_id="scheduler-schema-v3",
            date_from="2026-08-06",
            date_to="2026-08-06",
            schema_version=3,
        ),
    ]
    _prepare_snapshot_dependencies(monkeypatch)

    baseline = await metrics.capture_decision_baseline(
        _DB(sync_runs=runs),
        "owner-1",
        account_id="account-1",
        campaign_id="campaign-1",
        captured_at=captured_at,
    )

    fourteen = next(row for row in baseline["windows"] if row["days"] == 14)
    assert fourteen["coverage"]["complete"] is False
    assert fourteen["coverage"]["missing_dates"] == ["2026-08-06"]
    assert "scheduler-schema-v3" not in {
        proof["run_id"] for proof in fourteen["coverage"]["proofs"]
    }


@pytest.mark.asyncio
async def test_public_campaign_fact_gate_proves_each_requested_window():
    runs = [
        _complete_scheduler_campaign_run(
            run_id="previous-window",
            date_from="2026-07-29",
            date_to="2026-08-04",
        ),
        _complete_scheduler_campaign_run(
            run_id="selected-window",
            date_from="2026-08-05",
            date_to="2026-08-11",
        ),
    ]

    coverage = await metrics.campaign_performance_sync_coverage(
        _DB(sync_runs=runs),
        "owner-1",
        account_id="account-1",
        windows={
            "previous": {"date_from": "2026-07-29", "date_to": "2026-08-04"},
            "selected": {"date_from": "2026-08-05", "date_to": "2026-08-11"},
        },
    )

    assert coverage["complete"] is True
    assert coverage["windows"]["previous"]["complete"] is True
    assert coverage["windows"]["selected"]["complete"] is True
    assert {
        proof["run_id"]
        for proof in coverage["windows"]["selected"]["proofs"]
    } == {"selected-window"}


def test_product_comparison_separates_campaign_other_platform_and_manual():
    campaign = {"101": _product(units=3, source="campaign_exact_attribution")}
    store = {}
    for row in (
        _product(units=3, source="snapchat"),
        _product(units=2, source="meta"),
        _product(units=4, source="manual"),
        _product(units=1, source="unknown"),
    ):
        metrics._merge_products(store, {"101": row})

    result = metrics._product_sales_comparison(campaign, store)[0]

    assert result["campaign_attributed_units"] == 3
    assert result["whole_store_product_units"] == 10
    assert result["verified_other_ad_platform_units"] == 2
    assert result["salla_manual_entry_units"] == 4
    assert result["explicit_whatsapp_units"] == 0
    assert result["units_unresolved_for_snapchat_decision"] == 5
    assert result["manual_entry_note"] == (
        "manual_entry_is_observed; whatsapp_origin_is_unverified"
    )
    assert result["causality_warning"].startswith("units_not_attributed")


def test_product_comparison_keeps_snapchat_without_campaign_unresolved():
    campaign = {"101": _product(units=2, source="campaign_exact_attribution")}
    store = {"101": _product(units=6, source="snapchat")}

    result = metrics._product_sales_comparison(campaign, store)[0]

    assert result["explicit_snapchat_source_units"] == 6
    assert result["snapchat_units_without_exact_campaign"] == 4
    assert result["units_unresolved_for_snapchat_decision"] == 4


def test_known_product_is_visible_before_the_campaign_records_its_first_sale():
    intended = metrics._empty_product_bucket(
        {
            "salla_product_id": "710474094",
            "mezan_product_id": "mpv2_710474094",
            "name": "مشط شنب ولحية معدني مخصص بالاسم",
        }
    )
    store = {"710474094": _product(units=4, source="manual")}

    result = metrics._product_sales_comparison({"710474094": intended}, store)[0]

    assert result["campaign_attributed_units"] == 0
    assert result["whole_store_product_units"] == 4
    assert result["salla_manual_entry_units"] == 4
    assert result["units_unresolved_for_snapchat_decision"] == 4


def test_recent_improvement_requires_multiple_measured_signals():
    windows = [
        {
            "days": 7,
            "campaign": {
                "orders": 7,
                "sales_sar": 700,
                "contribution_profit_sar": 70,
                "roas": 2,
            },
        },
        {
            "days": 3,
            "campaign": {
                "orders": 9,
                "sales_sar": 1200,
                "contribution_profit_sar": 300,
                "roas": 4,
            },
        },
    ]

    trend = metrics.detect_recent_improvement(windows)

    assert trend["recent_improving"] is True
    assert trend["comparison"] == "3d_daily_average_vs_7d_daily_average"
    assert all(row["improved"] for row in trend["signals"])


@pytest.mark.asyncio
async def test_squad_and_ad_product_links_reach_baseline_scope(monkeypatch):
    captured_at = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
    links = [
        {
            "campaign_id": "campaign-1",
            "ad_squad_id": "squad-1",
            "product_id": "squad-product",
            "product_variant_id": None,
        },
        {
            "campaign_id": "campaign-1",
            "ad_squad_id": "squad-1",
            "ad_id": "ad-1",
            "product_id": "ad-product",
            "product_variant_id": None,
        },
    ]
    products = [
        {
            "salla_product_id": product_id,
            "name": product_id,
            "status": "sale",
            "quantity": 10,
            "unlimited_quantity": False,
            "variants": [],
            "last_synced_at": captured_at.isoformat(),
        }
        for product_id in ("squad-product", "ad-product")
    ]
    db = _DB(products=products)
    hierarchy_calls = _prepare_snapshot_dependencies(
        monkeypatch,
        confirmed_links=links,
    )

    baseline = await metrics.capture_decision_baseline(
        db,
        "owner-1",
        account_id="account-1",
        campaign_id="campaign-1",
        ad_squad_id="squad-1",
        ad_id="ad-1",
        captured_at=captured_at,
    )

    assert hierarchy_calls == [
        {
            "provider": "snapchat_ads",
            "account_id": "account-1",
            "campaign_id": "campaign-1",
            "ad_squad_id": "squad-1",
            "ad_id": "ad-1",
            "management_proposal_id": None,
            "as_of": captured_at,
            "include_unverified": False,
        }
    ]
    assert baseline["linked_product_ids"] == ["ad-product", "squad-product"]
    assert {
        row["salla_product_id"]
        for row in baseline["inventory"]
        if row["in_decision_product_scope"]
    } == {"ad-product", "squad-product"}
    assert baseline["inventory_verification_status"] == "verified"
    assert "verified_inventory" in baseline["primary_basis"]


@pytest.mark.asyncio
async def test_every_selected_variant_of_same_product_is_inventory_checked(monkeypatch):
    captured_at = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
    db = _DB(
        products=[
            {
                "salla_product_id": "comb-1",
                "name": "مشط معدني",
                "status": "sale",
                "quantity": 25,
                "unlimited_quantity": False,
                "variants": [
                    {
                        "id": "comb-stocked",
                        "quantity": 25,
                        "unlimited_quantity": False,
                    },
                    {
                        "id": "comb-empty",
                        "quantity": 0,
                        "unlimited_quantity": False,
                    },
                ],
                "last_synced_at": captured_at.isoformat(),
                "details_synced_at": captured_at.isoformat(),
            }
        ]
    )
    _prepare_snapshot_dependencies(monkeypatch)

    baseline = await metrics.capture_decision_baseline(
        db,
        "owner-1",
        account_id="account-1",
        campaign_id="campaign-1",
        product_ids=["comb-1"],
        product_refs=[
            {"product_id": "comb-1", "product_variant_id": "comb-stocked"},
            {"product_id": "comb-1", "product_variant_id": "comb-empty"},
        ],
        captured_at=captured_at,
    )

    selected = {
        row["product_variant_id"]: row
        for row in baseline["inventory"]
        if row["salla_product_id"] == "comb-1"
    }
    assert set(selected) == {"comb-stocked", "comb-empty"}
    assert selected["comb-stocked"]["variant_found"] is True
    assert selected["comb-stocked"]["delivery_blocked"] is False
    # One depleted selected variant is enough to fail closed even though the
    # other selected variant has stock.
    assert selected["comb-empty"]["delivery_blocked"] is True
    assert baseline["inventory_delivery_blocked"] is True
    assert baseline["inventory_verification_status"] == "incomplete"
    assert "verified_inventory" not in baseline["primary_basis"]


@pytest.mark.asyncio
async def test_recent_light_sync_cannot_make_stale_variant_inventory_fresh(
    monkeypatch,
):
    captured_at = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
    db = _DB(
        products=[
            {
                "salla_product_id": "comb-1",
                "name": "مشط معدني",
                "status": "sale",
                "quantity": 25,
                "unlimited_quantity": False,
                "variants": [
                    {
                        "id": "comb-stocked",
                        "quantity": 25,
                        "unlimited_quantity": False,
                    }
                ],
                # The light catalog was refreshed now, but the variant detail
                # snapshot is older than the 24-hour safety window.
                "last_synced_at": captured_at.isoformat(),
                "details_synced_at": (captured_at - timedelta(days=3)).isoformat(),
            }
        ]
    )
    _prepare_snapshot_dependencies(monkeypatch)

    baseline = await metrics.capture_decision_baseline(
        db,
        "owner-1",
        account_id="account-1",
        campaign_id="campaign-1",
        product_ids=["comb-1"],
        product_refs=[{"product_id": "comb-1", "product_variant_id": "comb-stocked"}],
        captured_at=captured_at,
    )

    selected = next(
        row
        for row in baseline["inventory"]
        if row["product_variant_id"] == "comb-stocked"
    )
    assert selected["last_synced_at"] == captured_at.isoformat()
    assert selected["inventory_freshness_source"] == "details_synced_at"
    assert selected["freshness_status"] == "stale_or_unknown"
    assert selected["delivery_blocked"] is True
    assert baseline["inventory_delivery_blocked"] is True
    assert baseline["inventory_verification_status"] == "incomplete"
    assert "verified_inventory" not in baseline["primary_basis"]


@pytest.mark.asyncio
async def test_historical_baseline_never_uses_future_inventory_as_fresh(monkeypatch):
    captured_at = datetime(2026, 8, 1, 10, tzinfo=timezone.utc)
    future_sync = datetime(2026, 8, 10, 10, tzinfo=timezone.utc)
    db = _DB(
        products=[
            {
                "salla_product_id": "comb-1",
                "name": "مشط معدني",
                "status": "sale",
                "quantity": 25,
                "unlimited_quantity": False,
                "variants": [{"id": "comb-stocked", "quantity": 25}],
                "last_synced_at": future_sync.isoformat(),
                "details_synced_at": future_sync.isoformat(),
            }
        ]
    )
    _prepare_snapshot_dependencies(monkeypatch)

    baseline = await metrics.capture_decision_baseline(
        db,
        "owner-1",
        account_id="account-1",
        campaign_id="campaign-1",
        product_ids=["comb-1"],
        product_refs=[{"product_id": "comb-1", "product_variant_id": "comb-stocked"}],
        captured_at=captured_at,
    )

    selected = next(
        row
        for row in baseline["inventory"]
        if row["product_variant_id"] == "comb-stocked"
    )
    assert selected["freshness_status"] == "observed_after_capture"
    assert selected["observed_after_capture"] is True
    assert selected["delivery_blocked"] is True
    assert baseline["inventory_verification_status"] == "incomplete"
    assert "verified_inventory" not in baseline["primary_basis"]


@pytest.mark.asyncio
async def test_archived_linked_product_fails_inventory_verification_closed(monkeypatch):
    captured_at = datetime(2026, 8, 12, 10, tzinfo=timezone.utc)
    db = _DB(
        products=[
            {
                "salla_product_id": "comb-1",
                "name": "مشط مؤرشف",
                "status": "active",
                "archived": True,
                "quantity": 25,
                "unlimited_quantity": False,
                "variants": [],
                "last_synced_at": captured_at.isoformat(),
            }
        ]
    )
    _prepare_snapshot_dependencies(monkeypatch)

    baseline = await metrics.capture_decision_baseline(
        db,
        "owner-1",
        account_id="account-1",
        campaign_id="campaign-1",
        product_ids=["comb-1"],
        captured_at=captured_at,
    )

    selected = next(row for row in baseline["inventory"] if row["archived"])
    assert selected["quantity"] == 25
    assert selected["freshness_status"] == "fresh"
    assert selected["delivery_blocked"] is True
    assert baseline["inventory_delivery_blocked"] is True
    assert baseline["inventory_verification_status"] == "incomplete"
    assert "verified_inventory" not in baseline["primary_basis"]
