"""Safe presentation of a source-read failure; no reads, writes, or retries.

Only fixed source names and failure codes may reach HTTP details or logs.
Unknown values, including objects and string subclasses, are never formatted.
This explains an observed failure; it does not repair its source.
"""
from types import MappingProxyType


_SOURCE_NAMES = MappingProxyType({
    "mezan_ad_account_cost_settings_v2": "إعدادات تكلفة الحسابات الإعلانية",
    "mezan_cost_resources_v2": "عناصر التكلفة",
    "mezan_employee_salary_contracts_v2": "عقود رواتب الموظفين",
    "mezan_employees_v2": "بيانات الموظفين",
    "mezan_google_ads_performance_daily_v2": "بيانات إعلانات Google",
    "mezan_integration_accounts_v2": "حسابات الربط",
    "mezan_meta_performance_daily_v2": "بيانات إعلانات Meta",
    "mezan_preparation_pieces_v1": "قطع التجهيز",
    "mezan_product_cost_profiles_v2": "تكاليف المنتجات",
    "mezan_product_option_cost_bindings_v2": "تكاليف خيارات المنتجات",
    "mezan_product_resource_bindings_v2": "موارد المنتجات",
    "mezan_snapchat_daily_projections_v2": "بيانات إعلانات Snapchat",
    "mezan_supplier_invoices_v2": "فواتير الموردين",
    "mezan_suppliers_v2": "بيانات الموردين",
    "mezan_tiktok_performance_daily_v2": "بيانات إعلانات TikTok",
    "mz2_ad_account_bindings_v2": "ربط الحسابات الإعلانية",
    "mz2_ad_automation_policies_v2": "سياسات الإعلانات",
    "mz2_ad_fx_snapshots_v2": "أسعار تحويل عملات الإعلانات",
    "mz2_bank_transfer_bindings": "ربط التحويلات البنكية",
    "mz2_external_persons_v2": "الجهات الخارجية",
    "mz2_financial_accounts": "الحسابات المالية",
    "mz2_operational_owner_beneficiaries_v2": "المستفيدين التشغيليين",
    "mz2_provider_fee_policies_v2": "عمولات منصات الدفع",
    "mz2_shipping_setup_v2": "إعدادات الشحن",
    "operating_recurring_invoices_v2": "فواتير المصروفات الدورية",
    "operating_recurring_obligations_v2": "التزامات المصروفات الدورية",
    "store_delivery_assignments": "إسناد الطلبات للتوصيل",
    "store_delivery_collections": "تحصيلات التوصيل",
    "store_delivery_driver_earnings": "مستحقات مناديب المتجر",
    "store_delivery_driver_settlements": "تسويات مناديب المتجر",
    "store_drivers": "بيانات مناديب المتجر",
    "unified_orders": "الطلبات",
})

_CAUSE_MESSAGES = MappingProxyType({
    "operational_source_scope_too_large": (
        "تجاوز حجم سجلاته الحد المتاح للقراءة",
        "أرسل هذه الرسالة للدعم لمعالجة حجم المصدر؛ إعادة المحاولة وحدها لا تكفي.",
    ),
    "operational_source_read_timeout": (
        "انتهت مهلة قراءة هذا المصدر",
        "يمكنك إعادة التحديث يدويًا؛ إذا تكرر التعذر، أرسل هذه الرسالة للدعم.",
    ),
    "operational_source_owner_mismatch": (
        "تعذّر التحقق من ارتباط جميع سجلاته بالحساب",
        "أرسل هذه الرسالة للدعم لمراجعة ارتباط المصدر بالحساب قبل إعادة التحديث.",
    ),
    "operational_source_rejected": (
        "تعذّر التحقق من السماح بقراءة المصدر",
        "أرسل هذه الرسالة للدعم لمراجعة إعدادات المصدر قبل إعادة التحديث.",
    ),
    "operational_source_read_invalid": (
        "تعذرت قراءة بياناته بالصيغة المطلوبة",
        "أرسل هذه الرسالة للدعم لمراجعة بيانات المصدر قبل إعادة التحديث.",
    ),
})


def source_failure_detail(cause, collection):
    """Retain known machine fields and provide an actionable Arabic message."""
    safe_cause = cause if type(cause) is str and cause in _CAUSE_MESSAGES else "unknown"
    safe_collection = collection if type(collection) is str and collection in _SOURCE_NAMES else "unknown"
    source = _SOURCE_NAMES.get(safe_collection, "أحد مصادر الأرصدة")
    reason, action = _CAUSE_MESSAGES.get(safe_cause, (
        "لم تكتمل قراءة هذا المصدر", "أرسل هذه الرسالة للدعم لمراجعة المصدر.",
    ))
    return {
        "code": "operational_source_setup_incomplete",
        "cause": safe_cause,
        "source_collection": safe_collection,
        "message": f"تعذر تحديث {source}: {reason}. احتُفظ بآخر لقطة ناجحة بتاريخها. {action}",
    }
