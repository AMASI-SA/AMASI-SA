"""Apps & Integrations Control Center V2 public API."""
from typing import Any, Callable

from .catalog import (
    AD_CAPABILITY_KEYS,
    AD_MUTATION_CAPABILITIES,
    ADVERTISING_PROVIDERS,
    PROVIDERS,
    PROVIDER_BY_ID,
    SAFETY_POLICY,
)
from .dashboard_ads_executive_routes import (
    attach_dashboard_ads_executive_routes,
)
from .dashboard_authoritative_summary_routes import (
    attach_dashboard_authoritative_summary_routes,
)
from .google_analytics_realtime_routes import (
    attach_google_analytics_realtime_routes,
)
from .google_analytics_source_attribution_routes import (
    attach_google_analytics_source_routes,
)
from .google_connections import attach_google_connection_routes
from .google_error_resolution import install_google_stale_error_filter
from .google_merchant_registration import (
    attach_google_merchant_registration_route,
)
from .meta_account_selection import attach_meta_account_selection_routes
from .meta_catalog_native import install_meta_native_catalog
from .meta_connections import attach_meta_connection_routes
from .meta_dashboard_summary_routes import attach_meta_dashboard_summary_routes
from .meta_native_reporting_routes import attach_meta_native_reporting_routes
from .meta_management_readiness import attach_meta_management_readiness_routes
from .meta_campaign_management import attach_meta_campaign_management_routes
from .snapchat_account_delivery_refresh import (
    install_snapchat_account_delivery_refresh,
    install_snapchat_effective_delivery_report,
)
from .snapchat_account_hourly_chart import (
    install_snapchat_account_hourly_chart,
)
from .snapchat_account_selection import attach_snapchat_account_selection_routes
from .snapchat_account_timezone_manager import (
    attach_snapchat_account_timezone_campaign_routes,
    install_snapchat_account_timezone_scheduler,
)
from .snapchat_account_timezone_retention import (
    install_snapchat_account_timezone_retention,
)
from .snapchat_platform_source_integrity import (
    install_snapchat_platform_source_integrity,
)
from .snapchat_campaign_truth_contract import (
    install_snapchat_campaign_truth_contract,
)
from .snapchat_ad_performance import (
    attach_snapchat_ad_routes,
    install_snapchat_ad_performance_refresh,
)
from .snapchat_ads_manager_attribution import (
    install_snapchat_ads_manager_attribution,
)
from .snapchat_adsquad_parent_delivery import (
    install_snapchat_adsquad_parent_delivery_report,
)
from .snapchat_adsquad_performance import (
    attach_snapchat_adsquad_routes,
    install_snapchat_adsquad_performance_refresh,
)
from .snapchat_adsquad_status_delivery_separation import (
    install_snapchat_adsquad_status_delivery_separation,
)
from .snapchat_campaign_catalog_refresh import (
    install_snapchat_campaign_catalog_refresh,
)
from .snapchat_campaign_management import (
    attach_snapchat_campaign_management_routes,
)
from .snapchat_campaign_preview_async import (
    attach_snapchat_campaign_preview_async_routes,
)
from .snapchat_decision_routes import attach_snapchat_decision_routes
from .snapchat_campaign_result_source_routes import (
    attach_snapchat_campaign_result_source_routes,
)
from .snapchat_salla_campaign_outcomes import (
    install_snapchat_salla_campaign_outcomes,
)

# The Ads Manager route is account-scoped and follows the selected account's
# native timezone. Dashboard/accounting continue reading the Riyadh-day facts.
attach_snapchat_campaign_report_routes = (
    attach_snapchat_account_timezone_campaign_routes
)
from .snapchat_catalog_native import install_snapchat_native_catalog
from .snapchat_connections import attach_snapchat_connection_routes
from .snapchat_dashboard_summary_routes import attach_snapchat_dashboard_summary_routes
from .snapchat_native_data_routes import attach_snapchat_native_data_routes
from .snapchat_native_tracking_routes import attach_snapchat_native_tracking_routes
from .snapchat_native_guard import assert_snapchat_v2_is_legacy_independent
from .tiktok_catalog_native import install_tiktok_native_catalog
from .tiktok_connections import attach_tiktok_connection_routes
from .tiktok_native_reporting_routes import (
    attach_tiktok_native_reporting_routes,
)
from .models import (
    CampaignProductLinkRecord,
    COLLECTION_NAMES,
    ensure_integrations_control_center_indexes,
)
from .routes import (
    _require_meta_integration_access,
    _require_owner,
    make_integrations_control_center_router as _base_make_integrations_router,
)
from .service import IntegrationsControlCenterService


def make_integrations_control_center_router(db: Any, current_user: Callable):
    """Compose the V2 router with isolated provider-native connection routes.

    Meta, Snapchat, and TikTok native definitions are installed only in this
    control plane. Old pages keep their frozen migration contracts while Mezan
    2 does not read legacy advertising credentials or data-feed collections.
    """
    # The Salla profitability stack imports Dashboard cost/auth modules. Keep it
    # behind full router composition so CAPI, scheduler and focused provider
    # modules remain importable in lightweight environments.
    from .snapchat_campaign_created_order_semantics import (
        install_fixed_created_order_semantics,
    )
    from .snapchat_campaign_current_catalog_cost import (
        install_current_catalog_salla_cost_resolution,
    )
    from .snapchat_campaign_profitability import (
        install_snapchat_campaign_profitability,
    )
    from .snapchat_campaign_profitability_exact_reuse import (
        install_exact_salla_profitability_reuse,
    )

    install_meta_native_catalog()
    install_snapchat_native_catalog()
    assert_snapchat_v2_is_legacy_independent()
    install_snapchat_ads_manager_attribution()
    install_snapchat_salla_campaign_outcomes()
    install_snapchat_campaign_profitability()
    install_current_catalog_salla_cost_resolution()
    install_exact_salla_profitability_reuse()
    # Install before importing the scheduler below. The scheduler then receives
    # the account-local wrapper while all Dashboard/accounting readers retain
    # the original Riyadh-day collection and semantics. Hourly rows are captured
    # from the same provider HOUR response without adding another provider call.
    # Campaign identity and status are refreshed before performance. Ad Squad
    # and Ad performance are refreshed at a bounded 15-minute cadence, and
    # account-level delivery is read last so billing/budget blocks become the
    # effective delivery status. Campaign order counts remain fixed by Salla
    # creation time across all statuses, while sales and profitability use only
    # the current financially included order set. Product costs resolve against
    # the same current catalog used by Products V2, including a unique exact-name
    # fallback when historical Salla line identities no longer match. All
    # projections remain read-only.
    install_snapchat_account_timezone_retention()
    install_snapchat_account_timezone_scheduler()
    install_snapchat_platform_source_integrity()
    # Wrap the final provider TOTAL projection so Salla profitability is
    # attached to the same visible campaign generation regardless of the
    # legacy result_source toggle, without rewriting provider metrics.
    install_fixed_created_order_semantics()
    install_snapchat_campaign_truth_contract()
    install_snapchat_account_hourly_chart()
    install_snapchat_campaign_catalog_refresh()
    install_snapchat_adsquad_performance_refresh()
    install_snapchat_ad_performance_refresh()
    install_snapchat_account_delivery_refresh()
    install_snapchat_effective_delivery_report()
    install_snapchat_adsquad_parent_delivery_report()
    install_snapchat_adsquad_status_delivery_separation()
    install_tiktok_native_catalog()
    install_google_stale_error_filter()
    router = _base_make_integrations_router(db, current_user)
    attach_google_connection_routes(router, db, current_user, _require_owner)
    attach_google_merchant_registration_route(
        router, db, current_user, _require_owner
    )
    attach_google_analytics_realtime_routes(
        router, db, current_user, _require_owner
    )
    attach_google_analytics_source_routes(
        router, db, current_user, _require_owner
    )
    attach_meta_connection_routes(router, db, current_user, _require_meta_integration_access)
    attach_meta_account_selection_routes(router, db, current_user, _require_meta_integration_access)
    attach_meta_native_reporting_routes(router, db, current_user, _require_meta_integration_access)
    attach_meta_management_readiness_routes(
        router, db, current_user, _require_meta_integration_access
    )
    attach_meta_campaign_management_routes(
        router, db, current_user, _require_owner
    )
    attach_meta_dashboard_summary_routes(router, db, current_user, _require_meta_integration_access)
    attach_snapchat_connection_routes(router, db, current_user, _require_owner)
    attach_snapchat_native_data_routes(router, db, current_user, _require_owner)
    attach_snapchat_native_tracking_routes(router, db, current_user, _require_owner)
    attach_snapchat_account_selection_routes(router, db, current_user, _require_owner)
    attach_snapchat_campaign_report_routes(
        router, db, current_user, _require_owner
    )
    attach_snapchat_adsquad_routes(
        router, db, current_user, _require_owner
    )
    attach_snapchat_ad_routes(
        router, db, current_user, _require_owner
    )
    attach_snapchat_campaign_management_routes(
        router, db, current_user, _require_owner
    )
    attach_snapchat_campaign_preview_async_routes(
        router, db, current_user, _require_owner
    )
    attach_snapchat_decision_routes(
        router, db, current_user, _require_owner
    )
    attach_snapchat_dashboard_summary_routes(router, db, current_user, _require_owner)

    # Keep package imports lightweight for focused Dashboard tests. PyMongo
    # and the CAPI worker are loaded only when the full V2 router is composed.
    from .snapchat_capi_purchases import attach_snapchat_capi_purchase_routes

    attach_snapchat_capi_purchase_routes(router, db, current_user, _require_owner)
    attach_dashboard_authoritative_summary_routes(
        router, db, current_user, _require_owner
    )
    attach_dashboard_ads_executive_routes(
        router, db, current_user, _require_owner
    )
    attach_tiktok_connection_routes(router, db, current_user, _require_owner)
    attach_tiktok_native_reporting_routes(
        router, db, current_user, _require_owner
    )

    # Lazy imports keep focused V2 modules importable in lightweight test
    # environments that intentionally omit Motor/PyMongo. In Production this
    # registers a server-side task that refreshes Meta and selected Snapchat
    # account totals every five minutes even when no browser is open. The
    # recovery installer prevents a missing provider-level projection from
    # silently producing targets=0 while selected Meta accounts still exist.
    from .ads_auto_sync_scheduler import attach_ads_auto_sync_scheduler
    from .ads_auto_sync_target_recovery import (
        install_ads_auto_sync_target_recovery,
    )

    install_ads_auto_sync_target_recovery()
    attach_ads_auto_sync_scheduler(router, db, current_user, _require_owner)

    exact_test_routes = [
        route
        for route in router.routes
        if str(getattr(route, "name", "")).startswith(
            ("test_google_", "test_meta_", "test_snapchat_", "test_tiktok_")
        )
    ]
    if exact_test_routes:
        for route in exact_test_routes:
            router.routes.remove(route)
        generic_index = next(
            (
                index
                for index, route in enumerate(router.routes)
                if str(getattr(route, "path", ""))
                == "/integrations-v2/{provider}/test-connection"
            ),
            len(router.routes),
        )
        router.routes[generic_index:generic_index] = exact_test_routes

    # Expose exactly one production Snapchat sync endpoint: the encrypted,
    # provider-native Mezan 2 implementation. There is no environment-based
    # fallback to the legacy collections.
    native_sync_route = next(
        (
            route for route in router.routes
            if str(getattr(route, "name", "")) == "sync_snapchat_native_data"
        ),
        None,
    )
    if native_sync_route is not None:
        insertion_index = next(
            (
                index for index, route in enumerate(router.routes)
                if str(getattr(route, "path", ""))
                == "/integrations-v2/snapchat_ads/sync"
            ),
            len(router.routes),
        )
        router.routes[:] = [
            route for route in router.routes
            if not (
                str(getattr(route, "path", ""))
                == "/integrations-v2/snapchat_ads/sync"
                and route is not native_sync_route
            )
        ]
        if native_sync_route in router.routes:
            router.routes.remove(native_sync_route)
        router.routes.insert(min(insertion_index, len(router.routes)), native_sync_route)

    return router


make_integrations_v2_router = make_integrations_control_center_router


def attach_integrations_control_center_routes(parent_router: Any, db: Any) -> None:
    from fastapi import Request
    from auth import get_current_user_from_db

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    parent_router.include_router(
        make_integrations_control_center_router(db, current_user)
    )


attach_integrations_v2_routes = attach_integrations_control_center_routes


__all__ = [
    "AD_CAPABILITY_KEYS",
    "AD_MUTATION_CAPABILITIES",
    "ADVERTISING_PROVIDERS",
    "CampaignProductLinkRecord",
    "COLLECTION_NAMES",
    "IntegrationsControlCenterService",
    "PROVIDERS",
    "PROVIDER_BY_ID",
    "SAFETY_POLICY",
    "attach_integrations_control_center_routes",
    "attach_integrations_v2_routes",
    "ensure_integrations_control_center_indexes",
    "make_integrations_control_center_router",
    "make_integrations_v2_router",
]
