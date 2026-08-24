"""Financial-provider apps plus the unified Mezan 2 accounting module.

The legacy provider catalogue remains preserved in
``financial_provider_apps_legacy``. This wrapper owns the accounting home,
independent permissions, fail-closed cutover readiness, and the P01 unified
settlement draft/review/post workflow. It never chooses a cutover instant or
posts opening balances.
"""
from fastapi import Depends

from financial_provider_apps_legacy import *  # noqa: F401,F403
from financial_provider_apps_legacy import (
    make_financial_provider_apps_router as _legacy_router,
)

from accounting_courier_bank_routes import install_accounting_courier_bank_routes
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
from accounting_settlement_bank_match_routes import (
    install_accounting_settlement_bank_match_routes,
)
from accounting_settlement_evidence_guard import delete_unlinked_settlement_file
from accounting_settlement_identity_routes import (
    install_accounting_settlement_identity_routes,
)
from accounting_settlement_import_guard import import_accounting_settlement_file
from accounting_settlement_lifecycle_routes import (
    install_accounting_settlement_lifecycle_routes,
)
import accounting_settlement_routes as accounting_settlement_routes_module
from accounting_settlement_routes import (  # noqa: F401
    ensure_accounting_settlement_indexes,
    install_accounting_settlement_routes,
)
from accounting_settlement_service import (  # noqa: F401
    BLOCKING_REASON_CODES,
    PROVIDERS,
    PROVIDER_LABELS,
    build_journal_preview,
    build_review_reasons,
    calculate_settlement_totals,
    canonical_provider,
    settlement_idempotency_key,
)
import settlements_import.routes as settlement_import_routes_module


def make_financial_provider_apps_router(db, current_user):
    async def provider_user(user: dict = Depends(current_user)):
        fresh = await fresh_accounting_user(db, user)
        require_accounting_permission(fresh, "accounting.settlements.view")
        return {**fresh, "id": accounting_owner_id(fresh)}

    # The historical importer accepts the UI hint as authoritative. P01 swaps
    # only its local reference for a fail-closed detector that compares the
    # selected provider with the workbook before any draft is created.
    accounting_settlement_routes_module.import_file = import_accounting_settlement_file

    # The legacy delete endpoint resolves this module-level symbol at request
    # time. Protect every workbook referenced by a P01 accounting record while
    # preserving normal deletion for files that never entered accounting.
    settlement_import_routes_module.delete_file = delete_unlinked_settlement_file

    router = _legacy_router(db, provider_user)
    install_accounting_status_routes(router, db, current_user)
    install_accounting_permission_routes(router, db, current_user)

    # Lifecycle handlers are registered before compatibility handlers. Starlette
    # dispatches the first matching route, so ``matched`` and bank-evidence
    # checks are authoritative while the older handlers remain import-safe.
    install_accounting_settlement_lifecycle_routes(router, db, current_user)
    install_accounting_settlement_routes(router, db, current_user)
    install_accounting_settlement_bank_match_routes(router, db, current_user)
    install_accounting_settlement_identity_routes(router, db, current_user)
    install_accounting_courier_bank_routes(router, db, current_user)
    return router
