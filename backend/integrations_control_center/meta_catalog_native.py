"""Install the native Meta definition only for the V2 control plane.

Old Meta pages keep their frozen migration contract. The V2 control centre uses
platform-owned OAuth credentials and never reads legacy ``meta_connections`` or
``meta_ads_daily`` collections.
"""
from __future__ import annotations

from dataclasses import replace


def install_meta_native_catalog() -> None:
    from . import catalog as catalog_module
    from . import service as service_module

    current = catalog_module.PROVIDER_BY_ID["meta_ads"]
    expected_permissions = (
        "ads_read",
        "ads_management",
        "business_management",
        "catalog_management",
        "pages_show_list",
        "pages_read_engagement",
        "pages_manage_metadata",
        "pages_messaging",
        "leads_retrieval",
        "instagram_basic",
        "instagram_manage_insights",
        "instagram_manage_comments",
        "instagram_manage_messages",
    )
    if not current.legacy_sources and current.required_permissions == expected_permissions:
        return

    native = replace(
        current,
        legacy_sources=(),
        required_permissions=expected_permissions,
        ai_can_when_ready=(
            "قراءة Business Manager والحسابات الإعلانية المصرح بها مباشرة من Meta",
            "قراءة الحملات والمجموعات والإعلانات والتصاميم والجماهير والتقارير بعد المزامنة",
            "فحص Pixels وCatalogs وحسابات Instagram المرتبطة واكتشاف نقص التتبع",
            "استقبال تعليقات ورسائل Instagram وربطها بأدلة ذكاء العملاء بعد منح الصلاحيات",
            "قراءة الرصيد والإنفاق وحدود الصرف وتوفر مصدر التمويل دون كشف بيانات الدفع",
            "تحليل الأداء وربطه بالطلبات والمنتجات وصافي الربح",
        ),
        ai_cannot_phase_one=(
            "تنفيذ إنشاء أو تعديل أو إيقاف دون اقتراح ومعاينة واعتماد وتحقق ورجوع",
            "استخدام مجموعات Meta القديمة كمصدر لبطاقة ميزان 2",
            "اعتبار Standard Access كافيًا لإدارة حسابات متاجر أخرى قبل Advanced Access",
        ),
    )
    providers = tuple(
        native if item.provider == "meta_ads" else item
        for item in catalog_module.PROVIDERS
    )
    catalog_module.PROVIDER_BY_ID["meta_ads"] = native
    catalog_module.PROVIDERS = providers
    service_module.PROVIDERS = providers

    import sys

    package = sys.modules.get("integrations_control_center")
    if package is not None:
        setattr(package, "PROVIDERS", providers)
        setattr(package, "PROVIDER_BY_ID", catalog_module.PROVIDER_BY_ID)
