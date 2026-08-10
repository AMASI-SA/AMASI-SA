from __future__ import annotations

import pytest

from integrations_control_center.snapchat_campaign_result_source_routes import (
    _match_order_campaign,
    _source_is_snapchat,
)
from integrations_control_center.snapchat_order_source_audit import (
    build_order_audit_rows,
)
from dashboard_v2_ads_executive import resolve_salla_ad_platform
from dashboard_v2_routes import _filtered_orders
from orders_db import _merge_into
from salla_integration.sync import _salla_order_to_doc
from salla_marketing_attribution import (
    SALLA_RAW_ATTRIBUTION_PROJECTION,
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
    ):
        assert forbidden not in paths
    for broad_container in (
        "raw_by_source.salla_direct.marketing",
        "raw_by_source.salla_direct.attribution",
        "raw_by_source.salla_direct.metadata",
    ):
        assert broad_container not in SALLA_RAW_ATTRIBUTION_PROJECTION


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
