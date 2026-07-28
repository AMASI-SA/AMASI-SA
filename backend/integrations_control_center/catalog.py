"""Static provider catalogue and Phase-1 capability policy.

The control centre is intentionally declarative.  A provider appearing in
this catalogue does not mean that Mezan may mutate that provider.  In
particular, every advertising write is blocked until the complete approval
lifecycle is implemented.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Iterable


AD_CAPABILITY_KEYS: Final[tuple[str, ...]] = (
    "campaigns.read",
    "campaigns.create",
    "campaigns.update",
    "campaigns.pause",
    "campaigns.resume",
    "budgets.read",
    "budgets.update",
    "ads.read",
    "ads.create",
    "ads.update",
    "creatives.read",
    "creatives.create",
    "audiences.read",
    "insights.read",
    "conversions.read",
)

AD_MUTATION_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "campaigns.create",
        "campaigns.update",
        "campaigns.pause",
        "campaigns.resume",
        "budgets.update",
        "ads.create",
        "ads.update",
        "creatives.create",
    }
)

# Phase 1 can prove these capabilities from the bounded local performance
# rows.  The remaining advertising reads stay planned/unknown rather than
# being optimistically advertised.
LOCAL_DATA_READ_CAPABILITIES: Final[frozenset[str]] = frozenset(
    {
        "campaigns.read",
        "ads.read",
        "insights.read",
        "conversions.read",
    }
)

MUTATION_LIFECYCLE: Final[tuple[str, ...]] = (
    "proposal",
    "preview",
    "approval",
    "execution",
    "verification",
    "audit",
    "rollback",
)


@dataclass(frozen=True, slots=True)
class ProviderDefinition:
    provider: str
    name: str
    name_ar: str
    category: str
    legacy_sources: tuple[str, ...]
    required_permissions: tuple[str, ...] = ()
    native_capabilities: tuple[str, ...] = ()
    ai_can_when_ready: tuple[str, ...] = ()
    ai_cannot_phase_one: tuple[str, ...] = ()
    planned: bool = False
    advertising: bool = False


PROVIDERS: Final[tuple[ProviderDefinition, ...]] = (
    ProviderDefinition(
        provider="salla",
        name="Salla",
        name_ar="سلة",
        category="commerce",
        legacy_sources=("salla_integrations", "salla_sync_logs"),
        required_permissions=(
            "offline_access",
            "settings.read",
            "orders.read_write",
            "shipping.read_write",
            "webhooks.read_write",
        ),
        native_capabilities=(
            "store.read",
            "orders.read",
            "products.read",
            "customers.read_from_orders",
        ),
        ai_can_when_ready=(
            "قراءة بيانات المتجر والطلبات المتاحة محليًا",
            "ربط نتائج التسويق بالمنتجات بعد اكتمال هوية المنتج",
        ),
        ai_cannot_phase_one=(
            "تغيير بيانات المتجر أو الطلبات من مركز التكاملات",
            "استخدام صلاحية كتابة دون دورة اعتماد",
        ),
    ),
    ProviderDefinition(
        provider="snapchat_ads",
        name="Snapchat Ads",
        name_ar="إعلانات سناب شات",
        category="advertising",
        legacy_sources=(
            "snapchat_connections",
            "snapchat_ad_accounts",
            "snapchat_account_daily",
            "ads_accounts",
        ),
        required_permissions=("snapchat-marketing-api",),
        ai_can_when_ready=(
            "قراءة أداء الحملات المتزامن محليًا",
            "تحليل التكلفة والتحويلات والربحية",
        ),
        ai_cannot_phase_one=(
            "إنشاء أو تعديل أو تشغيل أو إيقاف الحملات",
            "تعديل الميزانيات أو الإعلانات أو التصاميم",
        ),
        advertising=True,
    ),
    ProviderDefinition(
        provider="tiktok_ads",
        name="TikTok Ads",
        name_ar="إعلانات تيك توك",
        category="advertising",
        legacy_sources=("tiktok_connections", "tiktok_ads_daily"),
        required_permissions=("ads.read", "reporting.read"),
        ai_can_when_ready=(
            "قراءة الأداء المتاح من تغذية البيانات",
            "تحليل الإنفاق والتحويلات والربحية",
        ),
        ai_cannot_phase_one=(
            "اعتبار تغذية Make اتصال إدارة أصليًا",
            "إنشاء أو تعديل الحملات والميزانيات والإعلانات",
        ),
        advertising=True,
    ),
    ProviderDefinition(
        provider="meta_ads",
        name="Meta Ads",
        name_ar="إعلانات ميتا",
        category="advertising",
        legacy_sources=("meta_connections", "meta_ads_daily", "ads_accounts"),
        required_permissions=("ads_read", "read_insights"),
        ai_can_when_ready=(
            "قراءة الأداء المتزامن محليًا",
            "تحليل الحملات والتكلفة والتحويلات",
        ),
        ai_cannot_phase_one=(
            "إنشاء أو تعديل أو تشغيل أو إيقاف الحملات",
            "تعديل الميزانيات أو الإعلانات أو التصاميم",
        ),
        advertising=True,
    ),
    ProviderDefinition(
        provider="google_analytics_4",
        name="Google Analytics 4",
        name_ar="إحصاءات Google 4",
        category="analytics",
        legacy_sources=(),
        required_permissions=("analytics.readonly",),
        native_capabilities=("analytics.read", "conversions.read"),
        ai_can_when_ready=("قراءة الزيارات والتحويلات ومسارات المستخدم",),
        ai_cannot_phase_one=("قراءة GA4 قبل ربط خاصية موثقة",),
    ),
    ProviderDefinition(
        provider="google_search_console",
        name="Google Search Console",
        name_ar="Google Search Console",
        category="search",
        legacy_sources=(),
        required_permissions=("webmasters.readonly",),
        native_capabilities=("search_performance.read",),
        ai_can_when_ready=("قراءة أداء البحث العضوي والاستعلامات",),
        ai_cannot_phase_one=("قراءة بيانات البحث قبل ربط الموقع",),
    ),
    ProviderDefinition(
        provider="google_merchant_center",
        name="Google Merchant Center",
        name_ar="Google Merchant Center",
        category="commerce",
        legacy_sources=(),
        required_permissions=("content.readonly",),
        native_capabilities=("products.read", "diagnostics.read"),
        ai_can_when_ready=("قراءة حالة المنتجات وتشخيص رفضها",),
        ai_cannot_phase_one=("تعديل الخلاصة أو المنتجات قبل ربط الحساب",),
    ),
    ProviderDefinition(
        provider="google_ads",
        name="Google Ads",
        name_ar="إعلانات Google",
        category="advertising",
        legacy_sources=(),
        required_permissions=("adwords",),
        ai_can_when_ready=(
            "قراءة الحملات والأداء بعد الربط",
            "تحليل التكلفة والتحويلات والربحية",
        ),
        ai_cannot_phase_one=(
            "إنشاء أو تعديل أو تشغيل أو إيقاف الحملات",
            "تعديل الميزانيات أو الإعلانات أو التصاميم",
        ),
        advertising=True,
    ),
    ProviderDefinition(
        provider="qoyod",
        name="Qoyod",
        name_ar="قيود",
        category="accounting",
        legacy_sources=("qoyod_credentials", "qoyod_settings", "qoyod_invoices"),
        required_permissions=("api_credentials",),
        native_capabilities=(
            "configuration.read",
            "invoice_status.read",
            "accounting_health.read",
        ),
        ai_can_when_ready=(
            "قراءة حالة الربط والإرسال المحاسبي محليًا",
            "اكتشاف تعطل الإرسال أو نقص الإعداد",
        ),
        ai_cannot_phase_one=(
            "إنشاء فاتورة أو دفعة أو قيد من مركز التكاملات",
            "تغيير إعدادات الإرسال المحاسبي",
        ),
    ),
    ProviderDefinition(
        provider="shipping_companies",
        name="Shipping Companies",
        name_ar="شركات الشحن",
        category="shipping",
        legacy_sources=(),
        native_capabilities=("shipments.read", "tracking.read"),
        ai_can_when_ready=("قراءة الشحنات والتتبع بعد إضافة موصلات معتمدة",),
        ai_cannot_phase_one=("اعتبار إعدادات تكلفة الشحن الحالية موصلات API",),
        planned=True,
    ),
)

PROVIDER_BY_ID: Final[dict[str, ProviderDefinition]] = {
    item.provider: item for item in PROVIDERS
}
ADVERTISING_PROVIDERS: Final[frozenset[str]] = frozenset(
    item.provider for item in PROVIDERS if item.advertising
)


def provider_or_none(provider: str) -> ProviderDefinition | None:
    return PROVIDER_BY_ID.get(str(provider or "").strip().lower())


def _entry(
    state: str,
    reason: str,
    *,
    approval_required: bool = False,
    blocked_by_policy: bool = False,
) -> dict:
    return {
        "state": state,
        "available": state == "available",
        "approval_required": approval_required,
        "blocked_by_policy": blocked_by_policy,
        "reason": reason,
    }


def build_capability_matrix(
    definition: ProviderDefinition,
    *,
    connection_status: str,
    has_data: bool,
    current_permissions: Iterable[str] = (),
    permissions_observed: bool | None = None,
    evidence_capabilities: Iterable[str] = (),
) -> dict[str, dict]:
    """Build an evidence-based matrix without granting inferred writes."""
    status = str(connection_status or "unknown")
    connected = status in {"connected", "active", "healthy"}
    data_visible = bool(has_data) or status == "data_available"
    current = {str(item) for item in current_permissions}
    permission_evidence_present = (
        bool(current)
        if permissions_observed is None
        else bool(permissions_observed)
    )
    evidence = {str(item) for item in evidence_capabilities}

    if definition.advertising:
        matrix: dict[str, dict] = {}
        for capability in AD_CAPABILITY_KEYS:
            if capability in AD_MUTATION_CAPABILITIES:
                if not connected:
                    matrix[capability] = _entry(
                        "not_connected",
                        (
                            "A provider-management connection is required "
                            "before this mutation can enter the approval lifecycle."
                        ),
                        blocked_by_policy=True,
                    )
                else:
                    matrix[capability] = _entry(
                        "approval_required",
                        (
                            "Phase 1 blocks provider mutations until proposal, "
                            "preview, approval, verification, audit, and rollback "
                            "controls are implemented."
                        ),
                        approval_required=True,
                        blocked_by_policy=True,
                    )
            elif (
                capability in LOCAL_DATA_READ_CAPABILITIES
                and data_visible
                and capability in evidence
            ):
                matrix[capability] = _entry(
                    "available",
                    "Sanitized tenant-scoped fields prove this read capability locally.",
                )
            elif not connected and not data_visible:
                matrix[capability] = _entry(
                    "not_connected",
                    "No native connection or local provider data was found.",
                )
            elif capability in LOCAL_DATA_READ_CAPABILITIES:
                matrix[capability] = _entry(
                    "blocked_missing_data",
                    "A connection exists, but no synchronized data proves this capability yet.",
                )
            else:
                matrix[capability] = _entry(
                    "planned",
                    "This read path is not implemented in the Phase 1 control centre.",
                )
        return matrix

    matrix = {}
    missing = set(definition.required_permissions) - current
    for capability in definition.native_capabilities:
        if definition.planned:
            matrix[capability] = _entry(
                "planned",
                "The provider connector is planned for a later phase.",
            )
        elif not connected and not data_visible:
            matrix[capability] = _entry(
                "not_connected",
                "No verified connection evidence was found.",
            )
        elif definition.required_permissions and not permission_evidence_present:
            matrix[capability] = _entry(
                "unknown",
                "Permission evidence is unavailable; no missing permission is asserted.",
            )
        elif missing:
            matrix[capability] = _entry(
                "blocked_missing_permission",
                "Required permission evidence is incomplete.",
            )
        else:
            matrix[capability] = _entry(
                "available",
                "Current local evidence supports this read-only capability.",
            )
    return matrix


SAFETY_POLICY: Final[dict] = {
    "phase": 1,
    "read_only": True,
    "advertising_mutations_enabled": False,
    "mutation_lifecycle": list(MUTATION_LIFECYCLE),
    "policy": (
        "Provider mutations are blocked. This control centre may inspect local "
        "evidence and persist only sanitized mezan_*_v2 snapshots."
    ),
}
