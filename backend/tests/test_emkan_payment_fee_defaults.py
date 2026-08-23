"""Regression coverage for statement-derived Emkan fees (2026-08)."""
from __future__ import annotations

import asyncio

import openpyxl
import pytest

from bnpl import settlements_service as settlements
from bnpl.ledger_bridge import _norm_provider as normalize_ledger_provider
from bnpl.ledger_bridge import post_bnpl_sale_to_ledger
from payment_methods import (
    DEFAULT_PAYMENT_METHODS,
    PAYMENT_FEE_DEFAULTS_VERSION,
    migrate_payment_method_defaults,
)
from settlements_import.parsers.emkan import parse
from settlements_import.registry import detect_provider
from settlements_import.service import _apply_entries


def _default(name: str) -> dict:
    return next(row for row in DEFAULT_PAYMENT_METHODS if row["name"] == name)


def test_emkan_unified_and_bnpl_defaults_match_four_reports():
    assert _default("إمكان") == {
        "name": "إمكان",
        "commission_percent": 6.99,
        "fixed_fee": 1.5,
        "vat_percent": 15.0,
    }
    assert PAYMENT_FEE_DEFAULTS_VERSION == (
        "salla-tamara-tabby-emkan-statements-2026-08-v4"
    )
    assert settlements.DEFAULT_FEE_RATES["emkan"] == {
        "commission_pct": 6.99,
        "vat_pct": 15.0,
        "fixed_fee_per_order": 1.5,
        "refundable_commission_pct": 0.0,
        "settlement_fee_per_invoice": 0.0,
        "settlement_fee_vat_applicable": True,
        "settlement_period_days": 7,
    }
    assert normalize_ledger_provider({"provider": "emkan"}) == "emkan"
    assert normalize_ledger_provider({"provider": "imkan"}) == "emkan"


def test_emkan_ledger_bridge_is_blocked_until_cutoff_is_configured(monkeypatch):
    monkeypatch.delenv("BNPL_BRIDGE_CUTOFF_ISO", raising=False)
    result = asyncio.run(post_bnpl_sale_to_ledger(
        None,
        user_id="u1",
        txn={
            "provider": "emkan",
            "provider_id": "provider-uuid-1",
            "status": "completed",
            "amount": 219.32,
            "created_at_provider": "2026-08-05T12:00:00Z",
        },
    ))
    assert result == {
        "ok": True,
        "skipped": True,
        "reason": "missing_bridge_cutoff",
    }


@pytest.mark.parametrize(
    "legacy",
    [
        {"name": "إمكان", "commission_percent": 5.0,
         "fixed_fee": 0.0, "vat_percent": 15.0},
        {"name": "إمكان", "commission_percent": 6.99,
         "fixed_fee": 0.0, "vat_percent": 15.0},
    ],
)
def test_migration_upgrades_only_known_untouched_emkan_defaults(legacy):
    custom = {"name": "تمارا", "commission_percent": 4.25,
              "fixed_fee": 0.75, "vat_percent": 15.0}
    migrated, changed = migrate_payment_method_defaults(
        [legacy, custom], current_version=None,
    )
    by_name = {row["name"]: row for row in migrated}
    assert changed is True
    assert by_name["إمكان"]["commission_percent"] == 6.99
    assert by_name["إمكان"]["fixed_fee"] == 1.5
    assert by_name["تمارا"]["commission_percent"] == 4.25
    assert by_name["تمارا"]["fixed_fee"] == 0.75


def test_migration_preserves_merchant_edited_emkan_rule():
    current = [{"name": "إمكان", "commission_percent": 5.75,
                "fixed_fee": 1.25, "vat_percent": 15.0}]
    migrated, _changed = migrate_payment_method_defaults(
        current, current_version=None,
    )
    emkan = next(row for row in migrated if row["name"] == "إمكان")
    assert emkan["commission_percent"] == 5.75
    assert emkan["fixed_fee"] == 1.25


@pytest.mark.parametrize(
    ("amount", "expected_fee", "expected_vat"),
    [
        (219.32, 15.330468 + 1.5, 2.5245702),
        (286.47, 20.024253 + 1.5, 3.22863795),
        (239.84, 16.764816 + 1.5, 2.7397224),
    ],
)
def test_emkan_applies_vat_to_raw_percentage_plus_fixed_fee(
    amount, expected_fee, expected_vat,
):
    fee, vat = settlements._capture_fee_components(
        "emkan", amount, 0.0699, 1.5, 0.15,
    )
    assert fee == pytest.approx(expected_fee)
    assert vat == pytest.approx(expected_vat)


def _statement_workbook() -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Settlement Report"
    ws.append([
        "Merchant Name:", "Ammasi Alkhlyj ", "Total Settlement Today:",
        None, 199.9649618,
    ])
    ws.append([
        "Date:", "2026-08-13", "Closing Balance:", None, 199.9649618,
    ])
    ws.append([
        "Opening Balance:", 0.0, "Settlement Fees:", None, 0.0,
    ])
    ws.append([
        "Merchant name", "Merchant Code", None, "Order ID", None,
        "Order creation date", "Original bill Amount", "REFUND STATUS",
        "Refunded amount", "Net bill amount (Order amount after Refund if ",
        "Commission rate", "Commission Amount",
        "Refundable commission Rate", "Refundable commission Amount",
        "Non-Refundable commission rate",
        "Non-Refundable commission Amount", "Fixed Fee",
        "Total Fee (Commission + non refundable commission + Fixed Fee)",
        "VAT rate", "VAT amount",
        "Total deduction for EMKAN: (Total Fee + VAT amount)",
        "Settelment: The amount due to the merchant from EMKAN",
        "PO: Merchant code - Application ID - Order ID.",
    ])
    ws.append([
        "Ammasi Alkhlyj Altjary E", "-", None,
        "provider-uuid-1", None, "2026-08-05", 219.32, "NO REFUND",
        0.0, 219.32, 6.99, 15.330468, 0.0, 0.0, 69.0, 0.0,
        1.5, 16.830468, 15.0, 2.5245702, 19.3550382,
        199.9649618, "--WF-1-BNPL-provider-uuid-1",
    ])
    return wb


def test_emkan_parser_detects_report_and_balances_halalah_residual():
    wb = _statement_workbook()
    try:
        assert detect_provider(wb) == "emkan"
        result = parse(wb)
    finally:
        wb.close()

    assert result["header"]["statement_date"] == "2026-08-13"
    assert result["header"]["refund_policy_verified"] is False
    assert result["header"]["settlement_cycle_verified"] is False
    assert result["totals"] == {
        "rows": 1,
        "transactions_count": 1,
        "refunds_count": 0,
        "full_refunds_count": 0,
        "partial_refunds_count": 0,
        "gross": 219.32,
        "fees": 16.84,
        "reported_fees": 16.83,
        "fees_vat": 2.52,
        "total_deduction": 19.36,
        "net": 199.96,
        "rows_net": 199.96,
        "statement_net_difference": 0.0,
        "refund_full": 0.0,
        "refund_partial": 0.0,
        "settlement_fee": 0.0,
        "settlement_fee_vat": 0.0,
        "rounding_adjustment": 0.01,
        "opening_balance": 0.0,
        "closing_balance": 199.96,
    }
    entry = result["entries"][0]
    assert entry["provider_order_id"] == "provider-uuid-1"
    assert entry["order_number"] == "provider-uuid-1"
    assert entry["actual_payment_fee"] == 16.84
    assert entry["actual_payment_vat"] == 2.52
    assert entry["actual_net_amount"] == 199.96
    assert entry["statement_rounding_adjustment"] == 0.01
    assert round(
        entry["actual_payment_fee"]
        + entry["actual_payment_vat"]
        + entry["actual_net_amount"],
        2,
    ) == entry["actual_gross_amount"]


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, *_args):
        return self

    def __aiter__(self):
        async def _rows():
            for doc in self.docs:
                yield doc
        return _rows()


class _Collection:
    def __init__(self, docs):
        self.docs = docs

    def find(self, *_args, **_kwargs):
        return _Cursor(self.docs)


class _UnifiedOrders(_Collection):
    def __init__(self, docs):
        super().__init__(docs)
        self.updates = []

    async def update_one(self, query, update):
        self.updates.append((query, update))


class _Db:
    def __init__(self, docs):
        self.settlement_files = _Collection(docs)


def test_emkan_official_reports_are_deduplicated_and_not_weekly_bucketed():
    statement = {
        "id": "file-1",
        "user_id": "u1",
        "provider": "emkan",
        "header": {
            "statement_id": "emkan:2026-08-13:provider-uuid-1",
            "statement_date": "2026-08-13",
        },
        "totals": {
            "transactions_count": 1, "refunds_count": 0,
            "gross": 219.32, "refund_full": 0.0,
            "refund_partial": 0.0, "fees": 16.84,
            "reported_fees": 16.83, "fees_vat": 2.52,
            "settlement_fee": 0.0, "settlement_fee_vat": 0.0,
            "rounding_adjustment": 0.01, "net": 199.96,
        },
    }
    db = _Db([statement, {**statement, "id": "duplicate-upload"}])
    totals = asyncio.run(settlements._aggregate_emkan_official_totals(
        db, "u1", "2026-08-01", "2026-08-31",
    ))
    rows = asyncio.run(settlements.compute_weekly_settlements(
        db, "u1", "emkan", "2026-08-01", "2026-08-31",
    ))

    assert totals == {
        "transactions_count": 1,
        "refunds_count": 0,
        "canceled_count": 0,
        "canceled_amount": 0.0,
        "gross_sales": 219.32,
        "total_refunds": 0.0,
        "commission": 16.84,
        "reported_commission": 16.83,
        "commission_vat": 2.52,
        "settlement_fee": 0.0,
        "settlement_fee_vat": 0.0,
        "rounding_adjustment": 0.01,
        "settlement_invoices_count": 1,
        "net_payable": 199.96,
    }
    assert len(rows) == 1
    assert rows[0]["from"] == rows[0]["to"] == "2026-08-13"
    assert rows[0]["settlement_reference"].startswith("emkan:2026-08-13")
    assert rows[0]["data_source"] == "provider_official_file"


def test_emkan_uuid_is_resolved_before_matching_unified_order():
    class Db:
        payment_transactions = _Collection([{
            "provider_id": "provider-uuid-1",
            "order_reference_id": "661234567",
        }])
        unified_orders = _UnifiedOrders([{"order_number": "661234567"}])

    entry = {
        "provider_order_id": "provider-uuid-1",
        "order_number": "provider-uuid-1",
        "actual_payment_method": "emkan",
        "actual_gross_amount": 219.32,
        "actual_payment_fee": 16.84,
        "actual_payment_vat": 2.52,
        "actual_net_amount": 199.96,
        "actual_refund_amount": 0.0,
        "actual_partial_refund_amount": 0.0,
        "actual_fee_rate": 6.99,
        "event_type": "sale",
        "settlement_date": "2026-08-13",
        "settlement_reference": "emkan:2026-08-13:provider-uuid-1",
    }
    result = asyncio.run(_apply_entries(
        Db(), "u1", "emkan", [entry], file_id="file-1",
    ))

    assert result["matched"] == 1
    assert result["unmatched"] == 0
    assert entry["source_order_number"] == "provider-uuid-1"
    assert entry["order_number"] == "661234567"
    query, update = Db.unified_orders.updates[0]
    assert query == {"user_id": "u1", "order_number": "661234567"}
    assert update["$set"]["settlement_source"] == "emkan"
    assert update["$set"]["actual_payment_fee"] == 16.84
