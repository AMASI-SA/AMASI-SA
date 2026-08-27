from datetime import datetime, timezone

import pytest

from unified_marketing.readers import snapchat_v2_decision_evidence as evidence


def _account_report(*, sales=100.0, spend=40.0):
    return {
        "totals": {
            "quality": {"amount_complete": True},
            "delivery": {"spend_sar": {"amount": spend, "currency": "SAR"}},
            "commerce_outcomes": {
                "status": "complete",
                "revenue": {"amount": sales, "currency": "SAR"},
            },
            "commerce_profitability": {"status": "unavailable"},
        }
    }


def _campaign_report(rows):
    return {
        "totals": {
            "quality": {
                "sync_status": "complete",
                "coverage_status": "complete",
            }
        },
        "rows": rows,
    }


def _row(*, sales, product_cost=None, status="complete", orders=1):
    profitability = {"status": status}
    if status == "complete":
        profitability.update(
            {
                "orders": orders,
                "sales": {"amount": sales, "currency": "SAR"},
                "product_cost": {"amount": product_cost, "currency": "SAR"},
                "known_product_cost": {"amount": product_cost, "currency": "SAR"},
                "missing_cost_orders": 0,
                "product_count": 1 if orders else 0,
            }
        )
    return {
        "commerce_outcomes": {
            "status": "complete",
            "orders": orders,
            "revenue": {"amount": sales, "currency": "SAR"},
        },
        "commerce_profitability": profitability,
    }


def test_account_profitability_aggregates_exact_campaign_cost_and_account_spend():
    result = evidence._derive_account_profitability(
        _account_report(sales=100.0, spend=40.0),
        _campaign_report(
            [
                _row(sales=60.0, product_cost=20.0),
                _row(sales=40.0, product_cost=10.0),
            ]
        ),
    )

    assert result is not None
    assert result["status"] == "complete"
    assert result["sales"]["amount"] == 100.0
    assert result["product_cost"]["amount"] == 30.0
    assert result["ad_spend"]["amount"] == 40.0
    assert result["contribution_profit"]["amount"] == 30.0
    assert result["missing_cost_orders"] == 0


def test_zero_exact_match_revenue_does_not_invent_product_cost():
    result = evidence._derive_account_profitability(
        _account_report(sales=100.0, spend=40.0),
        _campaign_report(
            [
                _row(sales=100.0, product_cost=30.0),
                _row(sales=0.0, status="unavailable", orders=0),
            ]
        ),
    )

    assert result is not None
    assert result["product_cost"]["amount"] == 30.0
    assert result["contribution_profit"]["amount"] == 30.0


def test_nonzero_revenue_without_profitability_keeps_gate_fail_closed():
    result = evidence._derive_account_profitability(
        _account_report(sales=100.0, spend=40.0),
        _campaign_report(
            [
                _row(sales=80.0, product_cost=20.0),
                _row(sales=20.0, status="unavailable"),
            ]
        ),
    )

    assert result is None


class _Runs:
    async def find_one(self, query, projection, sort=None):
        return {
            "sync_run_id": "run-1",
            "finished_at": datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc),
            "financial_sync_status": "complete",
        }


class _DB:
    def __getitem__(self, name):
        return _Runs()


@pytest.mark.asyncio
async def test_identity_uses_latest_completed_sync_run_when_selected_account_timestamp_missing(monkeypatch):
    async def fake_identity(db, user_id):
        return {
            "provider": "snapchat_ads",
            "id": "acct-1",
            "name": "Amasi",
            "currency": "SAR",
            "timezone": "Asia/Riyadh",
            "last_sync_at": None,
        }

    monkeypatch.setattr(evidence.base_reader, "load_snapchat_v2_account_identity", fake_identity)
    result = await evidence.load_snapchat_v2_account_identity(_DB(), "user-1")

    assert result["last_sync_at"] == datetime(2026, 8, 27, 14, 0, tzinfo=timezone.utc)
    assert result["freshness_source"] == "snapchat_v2_latest_completed_sync_run"
    assert result["freshness_sync_run_id"] == "run-1"
