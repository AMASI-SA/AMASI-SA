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
from .google_connections import attach_google_connection_routes
from .google_error_resolution import install_google_stale_error_filter
from .google_merchant_registration import (
    attach_google_merchant_registration_route,
)
from .tiktok_connections import attach_tiktok_connection_routes
from .models import (
    CampaignProductLinkRecord,
    COLLECTION_NAMES,
    ensure_integrations_control_center_indexes,
)
from .routes import (
    _require_owner,
    make_integrations_control_center_router as _base_make_integrations_router,
)
from .service import IntegrationsControlCenterService


def make_integrations_control_center_router(db: Any, current_user: Callable):
    """Compose the V2 router with isolated provider-native connection routes.

    Exact Google and TikTok local-test routes are moved before the generic
    ``/{provider}/test-connection`` route so FastAPI cannot dispatch them to a
    transitional legacy probe.
    """
    install_google_stale_error_filter()
    router = _base_make_integrations_router(db, current_user)
    attach_google_connection_routes(router, db, current_user, _require_owner)
    attach_google_merchant_registration_route(
        router, db, current_user, _require_owner
    )
    attach_tiktok_connection_routes(router, db, current_user, _require_owner)

    exact_test_routes = [
        route
        for route in router.routes
        if str(getattr(route, "name", "")).startswith(
            ("test_google_", "test_tiktok_")
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
