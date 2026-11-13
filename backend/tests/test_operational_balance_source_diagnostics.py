"""Source failures remain useful to the visible page without exposing records."""
import asyncio
from copy import deepcopy
import json
import logging

from fastapi import HTTPException
import pytest

from operational_read_runtime import SourceReadError
from operational_balance_store import STATES
from test_operational_balance_demand_refresh import DB


SOURCES = (
    ("mezan_ad_account_cost_settings_v2", "إعدادات تكلفة الحسابات الإعلانية"),
    ("mezan_cost_resources_v2", "عناصر التكلفة"),
    ("mezan_employee_salary_contracts_v2", "عقود رواتب الموظفين"),
    ("mezan_employees_v2", "بيانات الموظفين"),
    ("mezan_google_ads_performance_daily_v2", "بيانات إعلانات Google"),
    ("mezan_integration_accounts_v2", "حسابات الربط"),
    ("mezan_meta_performance_daily_v2", "بيانات إعلانات Meta"),
    ("mezan_preparation_pieces_v1", "قطع التجهيز"),
    ("mezan_product_cost_profiles_v2", "تكاليف المنتجات"),
    ("mezan_product_option_cost_bindings_v2", "تكاليف خيارات المنتجات"),
    ("mezan_product_resource_bindings_v2", "موارد المنتجات"),
    ("mezan_snapchat_daily_projections_v2", "بيانات إعلانات Snapchat"),
    ("mezan_supplier_invoices_v2", "فواتير الموردين"),
    ("mezan_suppliers_v2", "بيانات الموردين"),
    ("mezan_tiktok_performance_daily_v2", "بيانات إعلانات TikTok"),
    ("mz2_ad_account_bindings_v2", "ربط الحسابات الإعلانية"),
    ("mz2_ad_automation_policies_v2", "سياسات الإعلانات"),
    ("mz2_ad_fx_snapshots_v2", "أسعار تحويل عملات الإعلانات"),
    ("mz2_bank_transfer_bindings", "ربط التحويلات البنكية"),
    ("mz2_external_persons_v2", "الجهات الخارجية"),
    ("mz2_financial_accounts", "الحسابات المالية"),
    ("mz2_operational_owner_beneficiaries_v2", "المستفيدين التشغيليين"),
    ("mz2_provider_fee_policies_v2", "عمولات منصات الدفع"),
    ("mz2_shipping_setup_v2", "إعدادات الشحن"),
    ("operating_recurring_invoices_v2", "فواتير المصروفات الدورية"),
    ("operating_recurring_obligations_v2", "التزامات المصروفات الدورية"),
    ("store_delivery_assignments", "إسناد الطلبات للتوصيل"),
    ("store_delivery_collections", "تحصيلات التوصيل"),
    ("store_delivery_driver_earnings", "مستحقات مناديب المتجر"),
    ("store_delivery_driver_settlements", "تسويات مناديب المتجر"),
    ("store_drivers", "بيانات مناديب المتجر"),
    ("unified_orders", "الطلبات"),
)
CAUSES = (
    ("operational_source_scope_too_large", "تجاوز حجم سجلاته الحد المتاح للقراءة"),
    ("operational_source_read_timeout", "انتهت مهلة قراءة هذا المصدر"),
    ("operational_source_owner_mismatch", "تعذّر التحقق من ارتباط جميع سجلاته بالحساب"),
    ("operational_source_rejected", "تعذّر التحقق من السماح بقراءة المصدر"),
    ("operational_source_read_invalid", "تعذرت قراءة بياناته بالصيغة المطلوبة"),
)


@pytest.mark.parametrize("collection,label", SOURCES, ids=[row[0] for row in SOURCES])
@pytest.mark.parametrize("cause,reason", CAUSES, ids=[row[0] for row in CAUSES])
def test_known_failure_preserves_codes_and_names_the_source_and_reason(collection, label,
                                                                        cause, reason):
    from operational_source_diagnostics import source_failure_detail

    detail = source_failure_detail(cause, collection)
    assert set(detail) == {"code", "cause", "source_collection", "message"}
    assert detail["code"] == "operational_source_setup_incomplete"
    assert detail["cause"] == cause and detail["source_collection"] == collection
    assert label in detail["message"] and reason in detail["message"]
    assert "احتُفظ بآخر لقطة ناجحة بتاريخها" in detail["message"]
    assert cause not in detail["message"] and collection not in detail["message"]
    if cause == "operational_source_read_timeout":
        assert "إعادة التحديث يدويًا" in detail["message"]
    else:
        assert "للدعم" in detail["message"]


class Hostile:
    def __str__(self):
        raise AssertionError("untrusted string conversion")

    def __repr__(self):
        raise AssertionError("untrusted representation")

    def __hash__(self):
        raise AssertionError("untrusted hashing")

    def __eq__(self, other):
        raise AssertionError("untrusted comparison")


class HostileString(str):
    def __hash__(self):
        raise AssertionError("untrusted string subclass hashing")


UNTRUSTED = (
    pytest.param(None, id="none"),
    pytest.param(False, id="boolean"),
    pytest.param(20001, id="integer"),
    pytest.param(b"unified_orders", id="bytes"),
    pytest.param([], id="list"),
    pytest.param({"owner_id": "private-fixture"}, id="mapping"),
    pytest.param(Hostile(), id="hostile-object"),
    pytest.param(HostileString("unified_orders"), id="string-subclass"),
    pytest.param("unified_orders\nowner=private-fixture", id="log-injection"),
    pytest.param("operational_private_account_token", id="unknown-operational-code"),
    pytest.param("", id="empty-string"),
)


@pytest.mark.parametrize("untrusted", UNTRUSTED)
def test_unknown_values_are_never_coerced_or_echoed(untrusted):
    from operational_source_diagnostics import source_failure_detail

    detail = source_failure_detail(untrusted, untrusted)
    assert detail == {
        "code": "operational_source_setup_incomplete", "cause": "unknown",
        "source_collection": "unknown",
        "message": "تعذر تحديث أحد مصادر الأرصدة: لم تكتمل قراءة هذا المصدر. "
                   "احتُفظ بآخر لقطة ناجحة بتاريخها. أرسل هذه الرسالة للدعم لمراجعة المصدر.",
    }
    assert "private-fixture" not in json.dumps(detail)


def test_unknown_cause_or_source_retains_only_the_independently_known_field():
    from operational_source_diagnostics import source_failure_detail

    cause_unknown = source_failure_detail(Hostile(), "unified_orders")
    assert cause_unknown["cause"] == "unknown"
    assert cause_unknown["source_collection"] == "unified_orders"
    assert "الطلبات" in cause_unknown["message"]
    source_unknown = source_failure_detail("operational_source_read_timeout", Hostile())
    assert source_unknown["cause"] == "operational_source_read_timeout"
    assert source_unknown["source_collection"] == "unknown"
    assert "انتهت مهلة قراءة هذا المصدر" in source_unknown["message"]


def setup_failing_refresh(monkeypatch, failure):
    import operational_balance_inventory as inventory
    import operational_balance_sources as sources

    database = DB()
    saved = database.data[STATES]["owner"]
    saved["engine"] = {"as_of": "2026-11-11T22:07:00+00:00", "amount": "9182.37"}
    saved["audit"] = [{"note": "private-audit-fixture"}]
    saved["movements"] = [{"amount": "123.45", "note": "private-movement-fixture"}]

    async def no_collision(*args):
        pass

    async def fail_read(*args, **kwargs):
        raise failure

    monkeypatch.setattr(inventory, "assert_no_source_collision", no_collision)
    monkeypatch.setattr(sources, "collect_sources", fail_read)
    return database


@pytest.mark.asyncio
@pytest.mark.parametrize("cause,reason", CAUSES, ids=[row[0] for row in CAUSES])
async def test_refresh_preserves_409_last_snapshot_and_audit_without_writes(monkeypatch,
                                                                          caplog, cause, reason):
    from operational_balance_service import refresh

    caplog.set_level(logging.WARNING, logger="operational_balance_service")
    database = setup_failing_refresh(monkeypatch, SourceReadError(cause, "unified_orders"))
    before = deepcopy(database.data)
    with pytest.raises(HTTPException) as error:
        await refresh(database, "owner", clock="2026-11-14T00:00:00+00:00")
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "operational_source_setup_incomplete"
    assert error.value.detail["cause"] == cause
    assert error.value.detail["source_collection"] == "unified_orders"
    assert database.data == before and database.writes == []
    records = [record for record in caplog.records
               if record.name == "operational_balance_service"]
    assert ("الطلبات" in error.value.detail["message"],
            reason in error.value.detail["message"], len(records)) == (True, True, 1)
    assert records[0].getMessage() == (
        "operational_refresh_source_failure stage=source_collection outcome=failed "
        f"cause={cause} source_collection=unified_orders"
    )
    assert records[0].exc_info is None and records[0].stack_info is None
    for private in ("9182.37", "123.45", "private-audit-fixture", "private-movement-fixture"):
        assert private not in caplog.text


@pytest.mark.asyncio
async def test_refresh_diagnostics_never_format_untrusted_exception_fields(monkeypatch, caplog):
    from operational_balance_service import refresh

    caplog.set_level(logging.WARNING, logger="operational_balance_service")
    failure = SourceReadError(Hostile(), Hostile())
    database = setup_failing_refresh(monkeypatch, failure)
    before = deepcopy(database.data)
    with pytest.raises(HTTPException) as error:
        await refresh(database, "owner", clock="2026-11-14T00:00:00+00:00")
    assert error.value.status_code == 409
    assert error.value.detail["cause"] == error.value.detail["source_collection"] == "unknown"
    records = [record for record in caplog.records
               if record.name == "operational_balance_service"]
    assert len(records) == 1
    assert records[0].getMessage().endswith("cause=unknown source_collection=unknown")
    assert records[0].exc_info is None and records[0].stack_info is None
    assert database.data == before and database.writes == []


@pytest.mark.asyncio
async def test_cancellation_is_not_converted_to_a_diagnostic_or_a_write(monkeypatch, caplog):
    from operational_balance_service import refresh

    caplog.set_level(logging.WARNING, logger="operational_balance_service")
    database = setup_failing_refresh(monkeypatch, asyncio.CancelledError())
    before = deepcopy(database.data)
    with pytest.raises(asyncio.CancelledError):
        await refresh(database, "owner", clock="2026-11-14T00:00:00+00:00")
    assert database.data == before and database.writes == []
    assert not [record for record in caplog.records
                if record.name == "operational_balance_service"]
