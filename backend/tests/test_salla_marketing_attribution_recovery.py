from __future__ import annotations

import pytest

from integrations_control_center.snapchat_campaign_result_source_routes import (
    _match_order_campaign,
    _source_is_snapchat,
)
from integrations_control_center.snapchat_order_source_audit import (
    build_order_audit_rows,
)
from integrations_control_center.snapchat_campaign_created_order_semantics import (
    _all_orders_in_padded_window,
)
from dashboard_v2_ads_executive import resolve_salla_ad_platform
from dashboard_v2_routes import _filtered_orders
from order_engine.service import _map_row
from orders_db import _merge_into, orders_to_parsed
from salla_integration.sync import _salla_order_to_doc
from snapchat_v2.salla_outcomes import _order_timestamp
from salla_marketing_attribution import (
    SALLA_RAW_ATTRIBUTION_PROJECTION,
    attach_projected_salla_attribution,
    canonical_marketing_source,
    canonical_order_source,
    meaningful_source_label,
    preserve_salla_raw_attribution,
    promoted_salla_attribution,
)


def _raw_snap_order(**marketing):
    return {
        "order_number": "3001",
        "created_at": "2026-08-10T08:00:00+00:00",
        "order_date": "2026-08-10",
        "order_status": "completed",
        "total_amount": 250,
        "source": "salla_direct",
        "raw_by_source": {
            "salla_direct": {
                "marketing": marketing,
                # These must never be needed by attribution extraction.
                "customer": {"name": "private", "mobile": "0500000000"},
                "shipping_address": {"street": "private"},
            }
        },
    }


def test_existing_raw_salla_order_resolves_platform_and_exact_campaign():
    order = _raw_snap_order(
        utm_source="snapchat",
        campaign_id="campaign-1",
        campaign_name="حملة الرياض",
    )

    assert _source_is_snapchat(order) is True
    assert resolve_salla_ad_platform(order) == "snapchat"
    assert meaningful_source_label(order) == "snapchat"
    key, method = _match_order_campaign(
        order,
        id_lookup={"campaign-1": ("account-1", "campaign-1")},
        name_lookup={"حملة الرياض": ("account-1", "campaign-1")},
    )
    assert key == ("account-1", "campaign-1")
    assert method == "campaign_id"


def test_raw_meta_source_is_not_misclassified_as_snapchat():
    order = {
        "source": "salla_direct",
        "raw_by_source": {
            "salla_direct": {
                "attribution": {"utm_source": "instagram"},
            }
        },
    }
    assert resolve_salla_ad_platform(order) == "meta"
    assert _source_is_snapchat(order) is False


def test_salla_utm_object_shape_resolves_source_and_campaign_name():
    order = {
        "source": "salla_direct",
        "raw_by_source": {
            "salla_direct": {
                "utm": {
                    "source": "snapchat",
                    "medium": "paid_social",
                    "campaign": "حملة الرياض",
                },
            }
        },
    }
    key, method = _match_order_campaign(
        order,
        id_lookup={},
        name_lookup={"حملة الرياض": ("account-1", "campaign-1")},
    )
    assert _source_is_snapchat(order) is True
    assert key == ("account-1", "campaign-1")
    assert method == "campaign_name"


def test_salla_live_source_details_shape_resolves_exact_snapchat_campaign():
    order = {
        "source": "store",
        "raw_by_source": {
            "salla_direct": {
                "source": "store",
                "source_details": {
                    "utm_source": "snapchat",
                    "utm_medium": "paid",
                    "utm_campaign": "5ae08447-23b6-4921-a54d-7ec641a099e2",
                    "utm_content": "652827c1-4810-4419-ad9f-4a2b671f8b24",
                    "ip": "must-not-be-projected",
                    "user-agent": "must-not-be-projected",
                },
                "campaign": {
                    "medium": "paid",
                    "source": "snapchat",
                    "campaign": "5ae08447-23b6-4921-a54d-7ec641a099e2",
                },
            },
        },
    }

    key, method = _match_order_campaign(
        order,
        id_lookup={
            "5ae08447-23b6-4921-a54d-7ec641a099e2": (
                "account-1",
                "5ae08447-23b6-4921-a54d-7ec641a099e2",
            ),
        },
        name_lookup={},
    )

    assert _source_is_snapchat(order) is True
    assert resolve_salla_ad_platform(order) == "snapchat"
    assert meaningful_source_label(order) == "snapchat"
    assert key == (
        "account-1",
        "5ae08447-23b6-4921-a54d-7ec641a099e2",
    )
    assert method == "campaign_id"
    assert promoted_salla_attribution(order) == {
        "utm_source": "snapchat",
        "utm_medium": "paid",
        "utm_campaign": "5ae08447-23b6-4921-a54d-7ec641a099e2",
        "campaign_name": "5ae08447-23b6-4921-a54d-7ec641a099e2",
        "marketing_source": "snapchat",
        "source_native": "snapchat",
        "ad_platform_source": "snapchat",
    }


def test_orders_v2_uses_same_source_details_normalizer_as_ad_reports():
    raw = {
        "id": "1986136862",
        "reference_id": "277225915",
        "date": {"date": "2026-08-10T01:23:00+03:00"},
        "status": {"slug": "under_review", "name": "بإنتظار المراجعة"},
        "customer": {"full_name": "عميل"},
        "amounts": {"total": {"amount": 127.57, "currency": "SAR"}},
        "items": [],
        "source": "store",
        "source_details": {
            "utm_source": "snapchat",
            "utm_medium": "paid",
            "utm_campaign": "campaign-1",
        },
    }

    dto = _map_row(raw)

    assert canonical_marketing_source(raw) == "snapchat"
    assert canonical_order_source(raw)["source"] == "snapchat"
    assert dto.source.source == "snapchat"
    assert dto.source.channel == "snapchat"
    assert dto.source.platform == "snapchat"
    assert dto.source.source_native == "snapchat"
    assert dto.source.utm_campaign == "campaign-1"
    assert dto.source.campaign_name == "campaign-1"


def test_new_salla_source_details_order_promotes_live_marketing_fields():
    payload = {
        "id": "1986136862",
        "reference_id": "277225915",
        "date": {"date": "2026-08-10T01:23:00+03:00"},
        "amounts": {"total": {"amount": 127.57, "currency": "SAR"}},
        "source": "store",
        "source_details": {
            "utm_source": "snapchat",
            "utm_medium": "paid",
            "utm_campaign": "5ae08447-23b6-4921-a54d-7ec641a099e2",
            "utm_content": "652827c1-4810-4419-ad9f-4a2b671f8b24",
        },
        "campaign": {
            "medium": "paid",
            "source": "snapchat",
            "campaign": "5ae08447-23b6-4921-a54d-7ec641a099e2",
        },
    }

    doc = _salla_order_to_doc(payload)

    assert doc["order_number"] == "277225915"
    assert doc["source"] == "store"
    assert doc["utm_source"] == "snapchat"
    assert doc["utm_medium"] == "paid"
    assert doc["utm_campaign"] == "5ae08447-23b6-4921-a54d-7ec641a099e2"
    assert doc["marketing_source"] == "snapchat"
    assert doc["ad_platform_source"] == "snapchat"


def test_salla_campaign_object_shape_resolves_without_source_details():
    order = {
        "source": "store",
        "raw_by_source": {
            "salla_direct": {
                "campaign": {
                    "medium": "paid",
                    "source": "snapchat",
                    "campaign": "campaign-1",
                },
            },
        },
    }

    key, method = _match_order_campaign(
        order,
        id_lookup={"campaign-1": ("account-1", "campaign-1")},
        name_lookup={},
    )

    assert _source_is_snapchat(order) is True
    assert key == ("account-1", "campaign-1")
    assert method == "campaign_id"


def test_audit_counts_raw_exact_match_without_distributing_other_orders():
    result = build_order_audit_rows(
        [
            _raw_snap_order(
                utm_source="snapchat",
                campaign_id="campaign-1",
                campaign_name="حملة الرياض",
            ),
            {
                **_raw_snap_order(utm_source="direct"),
                "order_number": "3002",
            },
        ],
        identities=[{
            "account_id": "account-1",
            "campaign_id": "campaign-1",
            "campaign_name": "حملة الرياض",
        }],
        timezone_name="Asia/Riyadh",
        date_from="2026-08-10",
        date_to="2026-08-10",
        included_statuses=["completed"],
        platform_attributed_purchases=1,
    )

    assert result["summary"]["total_salla_created_orders"] == 2
    assert result["summary"]["campaign_matched_orders"] == 1
    assert result["summary"]["non_campaign_orders"] == 1
    assert result["summary"]["non_campaign_distribution_allowed"] is False


def test_new_salla_order_promotes_only_stable_marketing_fields():
    payload = {
        "id": "order-id-1",
        "reference_id": "3003",
        "date": {"date": "2026-08-10T10:00:00+03:00"},
        "amounts": {"total": {"amount": 250, "currency": "SAR"}},
        "marketing": {
            "utm_source": "snapchat",
            "utm_medium": "paid_social",
            "utm_campaign": "campaign-1",
            "campaign_id": "campaign-1",
            "campaign_name": "حملة الرياض",
        },
        "customer": {"full_name": "private", "mobile": "0500000000"},
    }

    promoted = promoted_salla_attribution(payload)
    assert promoted == {
        "utm_source": "snapchat",
        "utm_medium": "paid_social",
        "utm_campaign": "campaign-1",
        "campaign_id": "campaign-1",
        "campaign_name": "حملة الرياض",
        "marketing_source": "snapchat",
        "source_native": "snapchat",
        "ad_platform_source": "snapchat",
    }

    doc = _salla_order_to_doc(payload)
    assert doc["order_number"] == "3003"
    assert doc["source"] == "salla_direct"
    assert doc["utm_source"] == "snapchat"
    assert doc["campaign_id"] == "campaign-1"
    assert doc["ad_platform_source"] == "snapchat"

    stored = _merge_into({}, doc, "salla_direct")
    assert stored["utm_source"] == "snapchat"
    assert stored["campaign_id"] == "campaign-1"
    assert stored["campaign_name"] == "حملة الرياض"
    assert stored["source_native"] == "snapchat"
    assert stored["marketing_source"] == "snapchat"
    assert stored["ad_platform_source"] == "snapchat"


def test_salla_marketing_fields_override_stale_make_attribution_only():
    existing = {
        "source": "make",
        "utm_source": "direct",
        "source_native": "store",
        "last_make_update_at": "2026-08-10T00:00:00+00:00",
    }
    incoming = {
        "source": "store",
        "utm_source": "snapchat",
        "source_native": "snapchat",
        "marketing_source": "snapchat",
        "ad_platform_source": "snapchat",
    }

    stored = _merge_into(existing, incoming, "salla_direct")

    assert stored["source"] == "make"
    assert stored["utm_source"] == "snapchat"
    assert stored["source_native"] == "snapchat"
    assert stored["ad_platform_source"] == "snapchat"


def test_sparse_salla_list_row_preserves_existing_campaign_evidence():
    existing = {
        "id": "order-1",
        "source": "store",
        "source_details": {
            "utm_source": "snapchat",
            "utm_medium": "paid",
            "utm_campaign": "campaign-1",
        },
    }
    sparse = {
        "id": "order-1",
        "source": "store",
        "status": {"slug": "in_progress"},
    }

    merged = preserve_salla_raw_attribution(existing, sparse)

    assert merged["status"]["slug"] == "in_progress"
    assert merged["utm_source"] == "snapchat"
    assert merged["utm_medium"] == "paid"
    assert merged["utm_campaign"] == "campaign-1"
    assert canonical_marketing_source(merged) == "snapchat"


def test_explicit_provider_correction_does_not_keep_stale_snapchat_evidence():
    existing = {
        "source_details": {
            "utm_source": "snapchat",
            "utm_campaign": "snap-campaign",
        },
    }
    corrected = {
        "source_details": {
            "utm_source": "instagram",
            "utm_campaign": "meta-campaign",
        },
    }

    merged = preserve_salla_raw_attribution(existing, corrected)

    assert canonical_marketing_source(merged) == "meta"
    assert merged["source_details"]["utm_campaign"] == "meta-campaign"
    assert merged.get("utm_campaign") is None


def test_snapchat_v2_uses_salla_creation_clock_not_internal_sync_timestamp():
    order = {
        "updated_at": "2026-08-25T00:11:00+00:00",
        "raw_by_source": {
            "salla_direct": {
                "date": {
                    "date": "2026-08-24 20:15:00.000000",
                    "timezone": "Asia/Riyadh",
                },
            },
        },
    }

    timestamp = _order_timestamp(order)

    assert timestamp is not None
    assert timestamp.isoformat() == "2026-08-24T17:15:00+00:00"


def test_legacy_report_source_breakdown_uses_central_source_details():
    parsed = orders_to_parsed([{
        "order_number": "3007",
        "order_date": "2026-08-10",
        "source": "store",
        "total_amount": 175,
        "raw_by_source": {
            "salla_direct": {
                "source_details": {"utm_source": "snapchat"},
            },
        },
    }])

    assert parsed["order_sources"] == [{
        "name": "snapchat",
        "orders_count": 1,
        "total_sales": 175.0,
    }]


def test_raw_projection_is_attribution_only():
    paths = " ".join(SALLA_RAW_ATTRIBUTION_PROJECTION).casefold()
    for forbidden in (
        "customer",
        "mobile",
        "phone",
        "email",
        "address",
        "payment",
        "products",
        "items",
        "user-agent",
        ".ip",
    ):
        assert forbidden not in paths
    for broad_container in (
        "raw_by_source.salla_direct.source_details",
        "raw_by_source.salla_direct.marketing",
        "raw_by_source.salla_direct.attribution",
        "raw_by_source.salla_direct.metadata",
    ):
        assert broad_container not in SALLA_RAW_ATTRIBUTION_PROJECTION

    assert (
        "raw_by_source.salla_direct.source_details.utm_source"
        in SALLA_RAW_ATTRIBUTION_PROJECTION
    )
    assert (
        "raw_by_source.salla_direct.source_details.utm_campaign"
        in SALLA_RAW_ATTRIBUTION_PROJECTION
    )
    assert (
        "raw_by_source.salla_direct.campaign.campaign"
        in SALLA_RAW_ATTRIBUTION_PROJECTION
    )


def test_raw_projection_has_no_mongodb_path_collisions():
    paths = set(SALLA_RAW_ATTRIBUTION_PROJECTION)
    for path in paths:
        parts = path.split(".")
        for index in range(1, len(parts)):
            assert ".".join(parts[:index]) not in paths

    assert (
        "raw_by_source.salla_direct.marketing.source.name"
        in SALLA_RAW_ATTRIBUTION_PROJECTION
    )
    assert (
        "raw_by_source.salla_direct.marketing.campaign.id"
        in SALLA_RAW_ATTRIBUTION_PROJECTION
    )


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length):
        return self.rows[:length]


class _OrdersCollection:
    def __init__(self):
        self.projections = []

    def find(self, query, projection):
        self.projections.append(projection)
        if projection.get("raw_by_source") == 0:
            return _Cursor([{
                "order_number": "3004",
                "order_date": "2026-08-10",
                "order_status": "completed",
                "payment_method": "card",
                "shipping_company": "smsa",
                "source": "salla_direct",
            }])
        return _Cursor([{
            "order_number": "3004",
            "raw_by_source": {
                "salla_direct": {
                    "marketing": {
                        "utm_source": "snapchat",
                        "campaign_id": "campaign-4",
                    }
                }
            },
        }])


class _DB:
    def __init__(self):
        self.unified_orders = _OrdersCollection()


@pytest.mark.asyncio
async def test_filtered_orders_safely_enriches_existing_raw_attribution(monkeypatch):
    import dashboard_v2_routes

    async def fake_settings(db, user_id):
        return {"report_included_statuses": ["completed"]}

    monkeypatch.setattr(dashboard_v2_routes, "ensure_user_settings", fake_settings)
    db = _DB()
    orders = await _filtered_orders(
        db,
        "owner-1",
        from_date="2026-08-10",
        to_date="2026-08-10",
        payment_methods=None,
        shipping_companies=None,
        include_marketing_attribution=True,
    )

    assert len(db.unified_orders.projections) == 2
    assert orders[0]["raw_by_source"]["salla_direct"]["marketing"][
        "campaign_id"
    ] == "campaign-4"
    assert _source_is_snapchat(orders[0]) is True


def test_projected_attribution_attachment_is_shared_and_order_number_scoped():
    orders = [{"order_number": "3005", "source": "store"}]
    attribution_rows = [{
        "order_number": "3005",
        "raw_by_source": {
            "salla_direct": {
                "source_details": {"utm_source": "snapchat"},
            },
        },
    }]

    result = attach_projected_salla_attribution(orders, attribution_rows)

    assert result is orders
    assert canonical_marketing_source(result[0]) == "snapchat"


class _CreatedOrderCollection:
    def __init__(self):
        self.projections = []

    def find(self, query, projection):
        self.projections.append(projection)
        if projection.get("raw_by_source") == 0:
            return _Cursor([{
                "order_number": "3006",
                "order_date": "2026-08-10",
                "order_status": "completed",
                "source": "store",
                "total_amount": 200,
            }])
        return _Cursor([{
            "order_number": "3006",
            "raw_by_source": {
                "salla_direct": {
                    "source_details": {
                        "utm_source": "snapchat",
                        "utm_campaign": "campaign-6",
                    },
                },
            },
        }])


@pytest.mark.asyncio
async def test_created_order_campaign_path_keeps_safe_raw_attribution():
    db = type("DB", (), {"unified_orders": _CreatedOrderCollection()})()

    orders = await _all_orders_in_padded_window(
        db,
        "owner-1",
        date_from="2026-08-10",
        date_to="2026-08-10",
        hide_inferred=False,
    )

    assert len(db.unified_orders.projections) == 2
    assert canonical_marketing_source(orders[0]) == "snapchat"
    key, method = _match_order_campaign(
        orders[0],
        id_lookup={"campaign-6": ("account-1", "campaign-6")},
        name_lookup={},
    )
    assert key == ("account-1", "campaign-6")
    assert method == "campaign_id"


@pytest.mark.asyncio
async def test_bounded_sync_recovery_loads_full_order_attribution(monkeypatch):
    import salla_integration.sync as sync_module

    calls = []
    saved = []

    async def fake_call_salla(db, user_id, method, path, params=None):
        calls.append((method, path, params))
        if path == "/orders":
            return {
                "data": [{
                    "id": "internal-1",
                    "reference_id": "3008",
                    "date": {
                        "date": "2026-08-24 20:15:00.000000",
                        "timezone": "Asia/Riyadh",
                    },
                    "source": "store",
                    "status": {"slug": "under_review"},
                    "amounts": {"total": {"amount": 175, "currency": "SAR"}},
                }],
                "pagination": {"currentPage": 1, "totalPages": 1},
            }
        assert path == "/orders/internal-1"
        return {
            "data": {
                "id": "internal-1",
                "reference_id": "3008",
                "date": {
                    "date": "2026-08-24 20:15:00.000000",
                    "timezone": "Asia/Riyadh",
                },
                "source": "store",
                "source_details": {
                    "utm_source": "snapchat",
                    "utm_medium": "paid",
                    "utm_campaign": "campaign-8",
                },
                "status": {"slug": "under_review"},
                "amounts": {"total": {"amount": 175, "currency": "SAR"}},
            },
        }

    async def fake_upsert_order(db, user_id, order_number, doc, source, raw):
        saved.append((order_number, doc, raw))
        return {"created": False, "doc": doc}

    async def fake_snapshot(*args, **kwargs):
        return {}

    async def fake_finish(*args, **kwargs):
        return None

    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(sync_module, "call_salla", fake_call_salla)
    monkeypatch.setattr(sync_module, "upsert_order", fake_upsert_order)
    monkeypatch.setattr(
        sync_module,
        "_refresh_plan_b_status_snapshot",
        fake_snapshot,
    )
    monkeypatch.setattr(sync_module, "finish_sync_log", fake_finish)
    monkeypatch.setattr(sync_module.asyncio, "sleep", no_sleep)

    result = await sync_module.run_orders_sync(
        object(),
        "owner-1",
        from_date="2026-08-24",
        to_date="2026-08-25",
        log_id="log-1",
        recover_marketing_attribution=True,
    )

    assert [path for _, path, _ in calls] == [
        "/orders",
        "/orders/internal-1",
    ]
    assert saved[0][0] == "3008"
    assert saved[0][1]["utm_source"] == "snapchat"
    assert saved[0][1]["utm_campaign"] == "campaign-8"
    assert saved[0][2]["source_details"]["utm_campaign"] == "campaign-8"
    assert result["attribution_details_fetched"] == 1
    assert result["attribution_orders_recovered"] == 1
    assert result["attribution_recovery_errors"] == 0
