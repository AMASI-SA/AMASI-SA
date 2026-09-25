"""Financial-provider apps plus the unified Mezan 2 accounting module.

The legacy provider catalogue remains preserved in
``financial_provider_apps_legacy``. This wrapper owns the accounting home,
independent permissions, fail-closed cutover readiness, and the P01 unified
settlement draft/review/post workflow. Opening balances are available only
through the guarded MZ2-native preview/approve/activate workflow.
"""
from fastapi import Depends
from accounting_source_files import install_accounting_source_file_routes
from accounting_receivable_routes import install_accounting_receivable_routes
from accounting_daily_movements import install_daily_movement_routes
from accounting_employee_finance import install_employee_finance_routes
from accounting_salla_order_evidence import install_salla_order_evidence_routes
from accounting_order_recognition import install_order_recognition_routes
from accounting_shipping_p02 import install_shipping_p02_routes

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
from accounting_module_opening_balances import install_opening_balance_routes
from accounting_module_permission_routes import install_accounting_permission_routes
from accounting_module_readiness import build_accounting_module_status  # noqa: F401
from accounting_module_status_routes import (
    fresh_accounting_user,
    install_accounting_status_routes,
)
from accounting_settlement_bank_match_routes import (
    install_accounting_settlement_bank_match_routes,
)
from accounting_settlement_currency_guard import (
    install_accounting_settlement_currency_guard,
)
from accounting_settlement_evidence_guard import delete_unlinked_settlement_file
from accounting_settlement_identity_routes import (
    install_accounting_settlement_identity_routes,
)
from accounting_settlement_import_guard import import_accounting_settlement_file
import accounting_settlement_lifecycle_routes as accounting_settlement_lifecycle_routes_module
from accounting_settlement_lifecycle_routes import (
    install_accounting_settlement_lifecycle_routes,
)
import accounting_settlement_register_routes as accounting_settlement_register_routes_module
from accounting_settlement_register_routes import (
    install_accounting_settlement_register_routes,
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
    from accounting_write_control import (
        AccountingDatabase, install_write_control_routes, protect_accounting_routes,
    )
    db = AccountingDatabase(db)
    from accounting_receipt_service import install_accounting_receipt_routes
    async def provider_user(user: dict = Depends(current_user)):
        fresh = await fresh_accounting_user(db, user)
        require_accounting_permission(fresh, "accounting.settlements.view")
        return {**fresh, "id": accounting_owner_id(fresh), "_accounting_actor_id": fresh["id"]}

    # The historical importer accepts the UI hint as authoritative. P01 swaps
    # only its local reference for a fail-closed detector that compares the
    # selected provider with the workbook before any draft is created.
    accounting_settlement_routes_module.import_file = import_accounting_settlement_file

    # The legacy delete endpoint resolves this module-level symbol at request
    # time. Protect every workbook referenced by a P01 accounting record while
    # preserving normal deletion for files that never entered accounting.
    settlement_import_routes_module.delete_file = delete_unlinked_settlement_file

    # Currency is explicit on every new draft. Unsupported currencies stop
    # before insertion, and old drafts without currency cannot cross lifecycle
    # gates. The register also surfaces missing currency instead of defaulting
    # silently to SAR.
    install_accounting_settlement_currency_guard(
        accounting_settlement_routes_module,
        accounting_settlement_lifecycle_routes_module,
        accounting_settlement_register_routes_module,
    )

    router = _legacy_router(db, provider_user)
    install_accounting_status_routes(router, db, current_user)
    from accounting_mz2_reports import install_mz2_report_routes
    install_mz2_report_routes(router, db, current_user)
    install_accounting_permission_routes(router, db, current_user)
    install_opening_balance_routes(router, db, current_user)

    # Lifecycle handlers are registered before compatibility handlers. Starlette
    # dispatches the first matching route, so ``matched`` and bank-evidence
    # checks are authoritative while the older handlers remain import-safe.
    install_accounting_settlement_lifecycle_routes(router, db, current_user)
    install_accounting_settlement_routes(router, db, current_user)
    install_accounting_settlement_bank_match_routes(router, db, current_user)
    install_accounting_settlement_identity_routes(router, db, current_user)
    install_accounting_settlement_register_routes(router, db, current_user)
    install_accounting_courier_bank_routes(router, db, current_user)
    install_accounting_source_file_routes(router, db, current_user)
    install_accounting_receivable_routes(router, db, current_user)
    install_accounting_receipt_routes(router, db, current_user)
    install_daily_movement_routes(router, db, current_user)
    install_employee_finance_routes(router, db, current_user)
    install_salla_order_evidence_routes(router, db, current_user)
    install_order_recognition_routes(router, db, current_user)
    install_shipping_p02_routes(router, db, current_user)
    install_write_control_routes(router, db, current_user)
    from accounting_periods import install_period_routes
    install_period_routes(router, db, current_user)
    from accounting_customer_advances import install_customer_advance_routes
    install_customer_advance_routes(router, db, current_user)
    protect_accounting_routes(router, db)
    return router
