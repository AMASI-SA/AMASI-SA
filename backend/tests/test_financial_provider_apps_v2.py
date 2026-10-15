import pytest
from pydantic import ValidationError

from financial_provider_apps import (
    OPERATION_ID,
    ProviderTaxInvoiceIn,
    build_provider_catalog,
    provider_operation_route,
)


def _settings():
    return {
        "payment_methods": [
            {"name": "مدى", "commission_percent": 1.0, "fixed_fee": 1.0, "vat_percent": 15.0},
            {"name": "تمارا", "commission_percent": 6.99, "fixed_fee": 1.5, "vat_percent": 15.0},
            {"name": "تابي", "commission_percent": 6.99, "fixed_fee": 1.0, "vat_percent": 15.0},
            {"name": "إمكان", "commission_percent": 6.99, "fixed_fee": 1.5, "vat_percent": 15.0},
        ],
        "shipping_companies": [
            {
                "name": "SMSA",
                "cost_per_order": 20.0,
                "vat_percent": 15.0,
                "is_deferred": True,
                "cod_fee_percent": 0.05,
                "cod_fee_fixed_per_order": 1.0,
                "cod_fee_tiers": [
                    {
                        "min_amount": 50,
                        "max_amount": 1000,
                        "min_inclusive": True,
                        "max_inclusive": True,
                        "commission_percent": 0.01,
                        "fixed_fee": 2,
                        "vat_percent": 15,
                    },
                    {
                        "min_amount": 1000,
                        "max_amount": None,
                        "min_inclusive": False,
                        "max_inclusive": True,
                        "commission_percent": 0.02,
                        "fixed_fee": 5,
                        "vat_percent": 15,
                    },
                ],
            },
        ],
    }


def test_catalog_models_financial_providers_as_apps_without_legacy_balances():
    apps = build_provider_catalog(_settings(), invoice_summary={
        "payment:tamara": {
            "count": 2,
            "total": 115.0,
            "latest": {"invoice_number": "T-2"},
        },
    })
    by_id = {app["provider_id"]: app for app in apps}

    assert set(by_id) == {
        "payment:salla",
        "payment:tamara",
        "payment:tabby",
        "payment:emkan",
        "shipping:smsa",
    }
    assert by_id["payment:salla"]["fee_rules"][0]["code"] == "mada"
    assert by_id["payment:tamara"]["tax_invoice_count"] == 2
    assert by_id["payment:tamara"]["fee_rules"][0]["fixed_fee"] == 1.5
    assert by_id["payment:tamara"]["fee_policy"]["refund_treatment"] == "no_commission_rebate"
    assert by_id["payment:tamara"]["fee_policy"]["statement_issue_weekday"] == "saturday"
    assert by_id["payment:tamara"]["fee_policy"]["cutoff_time_verified"] is False
    assert by_id["payment:tabby"]["fee_rules"][0]["commission_percent"] == 6.99
    assert by_id["payment:tabby"]["fee_policy"]["refundable_commission_percent"] == 4.99
    assert by_id["payment:tabby"]["fee_policy"]["transfer_weekday"] == "monday"
    assert by_id["payment:tabby"]["fee_policy"]["settlement_fee_per_statement_default"] == 0.0
    assert by_id["payment:emkan"]["fee_rules"][0]["fixed_fee"] == 1.5
    assert by_id["payment:emkan"]["fee_policy"]["commission_percent"] == 6.99
    assert by_id["payment:emkan"]["fee_policy"]["cycle_verified"] is False
    assert by_id["payment:emkan"]["fee_policy"]["cutoff_time_verified"] is False
    assert by_id["shipping:smsa"]["cod_fee_rule_mode"] == "tiered"
    assert by_id["shipping:smsa"]["fee_rules"][0]["commission_percent"] == 1.0
    assert by_id["shipping:smsa"]["fee_rules"][1]["min_inclusive"] is False
    assert by_id["shipping:smsa"]["settlement_netting_supported"] is True
    assert by_id["shipping:smsa"]["bank_transfer_optional"] is True
    assert all(app["legacy_financial_data_included"] is False for app in apps)
    assert all("opening_balance" not in app for app in apps)
    assert all("sales_total" not in app for app in apps)


def test_provider_routes_keep_settlements_out_of_generic_internal_transfer():
    assert provider_operation_route("payment:salla") == {
        "kind": "provider_settlement",
        "href": "/salla-settlements",
        "label": "رفع فاتورة/تسوية سلة",
    }
    assert provider_operation_route("payment:tamara")["href"] == "/bnpl-settlements/register"
    assert provider_operation_route("shipping:smsa")["href"].endswith("operation=courier_cod_settle")


def test_tax_invoice_is_evidence_only_and_rejects_migration_shaped_payloads():
    invoice = ProviderTaxInvoiceIn(
        invoice_number="INV-1",
        issue_date="2026-08-23",
        net_amount=100,
        vat_amount=15,
        total_amount=115,
        verification_status="verified",
        evidence_ref="archive://INV-1",
    )
    assert invoice.total_amount == 115

    with pytest.raises(ValidationError):
        ProviderTaxInvoiceIn(
            invoice_number="INV-2",
            issue_date="2026-08-23",
            net_amount=100,
            vat_amount=15,
            total_amount=116,
        )

    with pytest.raises(ValidationError):
        ProviderTaxInvoiceIn(
            invoice_number="INV-3",
            issue_date="2026-08-23",
            net_amount=100,
            vat_amount=15,
            total_amount=115,
            opening_balance=999,
        )

    assert OPERATION_ID == "MZ2-FIN-CUTOVER-001"
