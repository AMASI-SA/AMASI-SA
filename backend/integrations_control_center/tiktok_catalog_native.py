"""Install the native TikTok definition only for the V2 control plane.

The shared catalogue remains unchanged so unrelated Ads Manager and migration
workflows keep their frozen Phase-1 contract. The V2 router calls this installer
before constructing its service, making TikTok native and legacy-independent in
Mezan 2 without changing old pages.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any


def install_tiktok_native_catalog() -> None:
    from . import catalog as catalog_module
    from . import service as service_module

    current = catalog_module.PROVIDER_BY_ID["tiktok_ads"]
    if not current.legacy_sources and current.required_permissions == (
        "tiktok_marketing_api",
    ):
        return

    native = replace(
        current,
        legacy_sources=(),
        required_permissions=("tiktok_marketing_api",),
        ai_can_when_ready=(
            "قراءة حسابات TikTok المصرح بها مباشرة عبر Marketing API",
            "قراءة الحملات والإعلانات والتصاميم والجماهير والتقارير بعد المزامنة",
            "تحليل الإنفاق والتحويلات والربحية",
        ),
        ai_cannot_phase_one=(
            "تنفيذ إنشاء أو تعديل أو إيقاف دون اقتراح ومعاينة واعتماد وتحقق ورجوع",
            "استخدام بيانات Make أو مجموعات TikTok القديمة كمصدر لبطاقة ميزان 2",
        ),
    )
    providers = tuple(
        native if item.provider == "tiktok_ads" else item
        for item in catalog_module.PROVIDERS
    )

    # The provider lookup is imported by reference in routes.py, so mutate it
    # in place. The service imports the provider tuple by value, so replace its
    # module binding before the V2 service is constructed.
    catalog_module.PROVIDER_BY_ID["tiktok_ads"] = native
    catalog_module.PROVIDERS = providers
    service_module.PROVIDERS = providers

    # Keep package-level public exports truthful for runtime introspection.
    import sys

    package = sys.modules.get("integrations_control_center")
    if package is not None:
        setattr(package, "PROVIDERS", providers)
        setattr(package, "PROVIDER_BY_ID", catalog_module.PROVIDER_BY_ID)
