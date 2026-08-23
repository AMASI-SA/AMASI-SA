"""Regression coverage for statement-derived Tabby fees (2026-08)."""
from __future__ import annotations

import openpyxl
import pytest

from bnpl import settlements_service as settlements
from bnpl.config_store import (
    DEFAULTS,
    TABBY_STATEMENT_DEFAULTS_VERSION,
    get_settings,
)
import payment_gateway_metrics as gateway_metrics
from payment_methods import (
    DEFAULT_PAYMENT_METHODS,
    PAYMENT_FEE_DEFAULTS_VERSION,
    migrate_payment_method_defaults,
)
from settlements_import.parsers.tabby import parse


def _default(name: str) -> dict:
    return next(row for row in DEFAULT_PAYMENT_METHODS if row["name"] == name)


def test_tabby_unified_and_bnpl_defaults_match_four_reports():
    assert _default("تابي") == {
        "name": "تابي",
        "commission_percent": 6.99,
        "fixed_fee": 1.0,
        "vat_percent": 15.0,
    }
    assert PAYMENT_FEE_DEFAULTS_VERSION == (
        "salla-tamara-tabby-emkan-statements-2026-08-v4"
    )
    tabby = DEFAULTS["tabby"]
    assert tabby["refundable_commission_percent"] == pytest.approx(0.0499)
    assert tabby["settlement_fee_per_invoice"] == 0.0
    assert tabby["invoice_weekdays"] == ["monday"]
    assert tabby["transfer_weekdays"] == ["monday"]


def test_migration_upgrades_only_untouched_tabby_default():
    current = [
        {"name": "تابي", "commission_percent": 5.0,
         "fixed_fee": 0.0, "vat_percent": 15.0},
        {"name": "تمارا", "commission_percent": 4.25,
         "fixed_fee": 0.75, "vat_percent": 15.0},
    ]
    migrated, changed = migrate_payment_method_defaults(
        current,
        current_version="salla-tamara-statements-2026-08-v2",
    )
    by_name = {row["name"]: row for row in migrated}
    assert changed is True
    assert by_name["تابي"]["commission_percent"] == 6.99
    assert by_name["تابي"]["fixed_fee"] == 1.0
    assert by_name["تمارا"]["commission_percent"] == 4.25
    assert by_name["تمارا"]["fixed_fee"] == 0.75


@pytest.mark.parametrize(
    ("amount", "fee", "vat"),
    [
        (189.13, 14.22, 2.14),
        (179.87, 13.58, 2.04),
        (414.88, 30.00, 4.49),
    ],
)
def test_tabby_rounds_split_fee_and_vat_per_leg(amount, fee, vat):
    actual_fee, actual_vat = settlements._capture_fee_components(
        "tabby", amount, 0.0699, 1.0, 0.15, 0.0499,
    )
    assert actual_fee == fee
    assert actual_vat == vat


def _statement_workbook(*, payout_fee: bool = False) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "SR"
    for _ in range(5):
        ws.append([])
    ws.append([None, "Date", "17/08/2026"])
    ws.append([None, "Statement #", "Tabby20260817SAR"])
    for _ in range(3):
        ws.append([])
    ws.append([
        "Order Number", "Sale/Refund Date", "Merchant Name",
        "Merchant Code", "Product Type", "Type", "Currency",
        "Order Amount", "Commission Rate", "Refundable Commission",
        "Non Refundable Commission", "Fixed Fee", "Total Fee",
        "VAT Amount", "VAT Rate", "Total Deduction",
        "Transferred amount", "Transfer Date",
    ])
    ws.append([
        "278344901", "16/08/2026", "متجر أماسي", "default",
        "Installments: 3 Months", "sale", "SAR", 414.88, 6.99,
        20.70, 8.30, 1.0, 30.00, 4.49, 0.15, 34.49, 380.39,
        "17/08/2026",
    ])
    ws.append([
        "276055915", "13/08/2026", "متجر أماسي", "default",
        "Installments: 3 Months", "refund", "SAR", -301.99, 6.99,
        -15.07, 0, 0, -15.07, -2.26, 0.15, -17.33, -284.66,
        "17/08/2026",
    ])
    ws.append([
        "272657127", "13/08/2026", "متجر أماسي", "default",
        "Installments: 2 Months", "partial refund", "SAR", -247.00,
        6.99, -12.33, 0, 0, -12.33, -1.85, 0.15, -14.18,
        -232.82, "17/08/2026",
    ])
    if payout_fee:
        ws.append([
            "Payout fee", None, None, None, None, None, "SAR", 0,
            0, 0, 0, 0, 6.0, 0.90, 0.15, 6.90, -6.90,
            "17/08/2026",
        ])
    ws.append([
        "Note: This is not an official tax invoice; payout fee is conditional."
    ])
    return wb


def test_tabby_parser_uses_type_column_and_refund_gross():
    wb = _statement_workbook()
    try:
        result = parse(wb)
    finally:
        wb.close()

    entries = {row["order_number"]: row for row in result["entries"]}
    assert entries["278344901"]["event_type"] == "sale"
    assert entries["276055915"]["actual_refund_amount"] == 301.99
    assert entries["276055915"]["actual_payment_fee"] == -15.07
    assert entries["272657127"]["source_event_type"] == "partial refund"
    assert entries["272657127"]["actual_partial_refund_amount"] == 247.0
    assert result["header"]["period_start"] == "2026-08-10"
    assert result["header"]["period_end"] == "2026-08-16"
    assert result["totals"] == {
        "rows": 3,
        "transactions_count": 1,
        "refunds_count": 2,
        "full_refunds_count": 1,
        "partial_refunds_count": 1,
        "gross": 414.88,
        "fees": 2.60,
        "fees_vat": 0.38,
        "net": -137.09,
        "refund_full": 301.99,
        "refund_partial": 247.0,
        "refunded_fees": 27.40,
        "refunded_fees_vat": 4.11,
        "settlement_fee": 0.0,
        "settlement_fee_vat": 0.0,
    }


def test_tabby_parser_keeps_conditional_payout_fee_separate():
    wb = _statement_workbook(payout_fee=True)
    try:
        result = parse(wb)
    finally:
        wb.close()
    payout = next(
        row for row in result["entries"]
        if row["event_type"] == "settlement_fee"
    )
    assert payout["actual_gross_amount"] == 0.0
    assert payout["actual_net_amount"] == -6.90
    assert result["totals"]["fees"] == 2.60
    assert result["totals"]["settlement_fee"] == 6.0
    assert result["totals"]["settlement_fee_vat"] == 0.90
    assert result["totals"]["net"] == -143.99


class _Collection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    async def find_one(self, query, projection=None):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return dict(doc)
        return None

    async def update_one(self, query, update, upsert=False):
        target = None
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                target = doc
                break
        if target is None:
            target = dict(query)
            self.docs.append(target)
        target.update(update.get("$setOnInsert", {}))
        target.update(update.get("$set", {}))


class _SettingsDb:
    def __init__(self, doc):
        self.bnpl_settings = _Collection([doc])
        self.users = _Collection()


@pytest.mark.asyncio
async def test_tabby_legacy_cycle_and_fee_defaults_migrate_safely():
    db = _SettingsDb({
        "user_id": "u1", "provider": "tabby",
        "transfer_weekdays": ["tuesday", "wednesday"],
        "settlement_fee_per_invoice": 6.0,
    })
    result = await get_settings(db, "u1", "tabby")
    assert result["transfer_weekdays"] == ["monday"]
    assert result["settlement_fee_per_invoice"] == 0.0
    saved = db.bnpl_settings.docs[0]
    assert saved["statement_cycle_defaults_version"] == (
        TABBY_STATEMENT_DEFAULTS_VERSION
    )

    custom_db = _SettingsDb({
        "user_id": "u2", "provider": "tabby",
        "transfer_weekdays": ["friday"],
        "settlement_fee_per_invoice": 2.0,
    })
    custom = await get_settings(custom_db, "u2", "tabby")
    assert custom["transfer_weekdays"] == ["friday"]
    assert custom["settlement_fee_per_invoice"] == 2.0


@pytest.mark.asyncio
async def test_gateway_metrics_keep_tabby_nonrefundable_fee_on_refund(monkeypatch):
    class AsyncRows:
        def __init__(self, rows):
            self.rows = iter(rows)

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.rows)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

    class UnifiedOrders:
        def aggregate(self, _pipeline):
            return AsyncRows([
                {
                    "order_status": "completed", "total_amount": 414.88,
                    "payment_method": "تابي", "actual_payment_method": "",
                    "payment_fee_status": "estimated",
                },
                {
                    "order_status": "refunded", "total_amount": 414.88,
                    "payment_method": "تابي", "actual_payment_method": "",
                    "payment_fee_status": "estimated",
                },
            ])

    class FakeDb:
        unified_orders = UnifiedOrders()

    async def fake_settings(_db, _user_id):
        return {
            "payment_methods": DEFAULT_PAYMENT_METHODS,
            "report_included_statuses": [],
        }

    async def fake_policy(_db, _user_id):
        return {"completed": "confirmed", "refunded": "refunded"}

    monkeypatch.setattr(gateway_metrics, "ensure_user_settings", fake_settings)
    import order_status_policy
    monkeypatch.setattr(order_status_policy, "get_policy_map", fake_policy)

    result = await gateway_metrics.compute_metrics(FakeDb(), "merchant")
    tabby = next(row for row in result["rows"] if row["key"] == "tabby")
    assert tabby["fees"] == 39.30
    assert tabby["fees_vat"] == 5.88
    assert tabby["refund_full"] == 414.88
    assert tabby["net"] == 369.70
