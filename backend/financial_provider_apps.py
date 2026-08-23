"""Financial-provider apps plus the unified Mezan 2 accounting module.

The legacy provider catalogue remains byte-for-byte preserved in
``financial_provider_apps_legacy``. This wrapper adds only the new read-only
accounting home, independent permission control, and fail-closed cutover
readiness. It does not choose a cutover instant or post opening balances.
"""
from fastapi import Depends

from financial_provider_apps_legacy import *  # noqa: F401,F403
from financial_provider_apps_legacy import (
    make_financial_provider_apps_router as _legacy_router,
)

from accounting_module_contract import (  # noqa: F401
    ACCOUNTING_ACTIONS,
    ACCOUNTING_PAGES,
    ACCOUNTING_PERMISSION_KEYS,
    ACCOUNTING_PAGE_PERMISSION_KEYS,
    OPERATION_ID,
    accounting_owner_id,
    accounting_permissions_for_user,
    require_accounting_permission,
)
from accounting_module_ledger import summarize_accounting_home_ledger  # noqa: F401
from accounting_module_permission_routes import install_accounting_permission_routes
from accounting_module_readiness import build_accounting_module_status  # noqa: F401
from accounting_module_status_routes import (
    fresh_accounting_user,
    install_accounting_status_routes,
)


def make_financial_provider_apps_router(db, current_user):
    async def provider_user(user: dict = Depends(current_user)):
        fresh = await fresh_accounting_user(db, user)
        require_accounting_permission(fresh, "accounting.settlements.view")
        return {**fresh, "id": accounting_owner_id(fresh)}

    router = _legacy_router(db, provider_user)
    install_accounting_status_routes(router, db, current_user)
    install_accounting_permission_routes(router, db, current_user)
    return router
