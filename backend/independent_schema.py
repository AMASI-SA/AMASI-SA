"""Independent schema initialization: indexes only, never historical repair.

The explicit helper plan below reuses existing schema definitions. Its database
boundary forwards real create_index calls and records failures even when a
legacy helper catches them. No data mutation or provider access is exposed by
that boundary. Existing accounts are never promoted or rewritten.
"""
from __future__ import annotations


class IndexInitializationFailed(RuntimeError):
    pass


class _IndexCollection:
    def __init__(self, owner, name):
        self._owner = owner
        self._name = name

    async def create_index(self, *args, **kwargs):
        try:
            return await self._owner._database[self._name].create_index(*args, **kwargs)
        except Exception as exc:
            self._owner.failures.append(exc)
            raise

    def __getattr__(self, name):
        error = IndexInitializationFailed("Schema helper attempted a non-index operation")
        self._owner.failures.append(error)
        raise error


class _IndexDatabase:
    def __init__(self, database):
        self._database = database
        self.failures = []

    def __getitem__(self, name):
        return _IndexCollection(self, name)

    def __getattr__(self, name):
        return self[name]

    def assert_success(self):
        if self.failures:
            raise IndexInitializationFailed("Required index initialization failed; migration is incomplete") from self.failures[0]


async def ensure_independent_schema(server) -> None:
    """Called only by the explicitly selected, fenced migration role.

    A populated users collection must already contain an Owner. Missing Owner
    is an operator decision, never an implicit privilege promotion. An empty
    database uses the existing password-required initial-owner bootstrap.
    """
    from auth import seed_admin
    from login_security import MongoLoginSecurityStore
    from progressive_login_security import ProgressiveLoginStore
    from passkey_security import PasskeyStore
    from mfa_security import MfaChallengeStore
    from email_otp_security import EmailOtpChallengeStore, validate_email_otp_runtime
    from ledger_core import ensure_indexes as ensure_ledger_indexes
    from campaign_ai_monitor import ensure_campaign_ai_indexes
    from integrations.qoyod.models import ensure_qoyod_indexes
    from salla_integration.routes import ensure_abandoned_cart_indexes, ensure_sync_indexes

    validate_email_otp_runtime()
    db = _IndexDatabase(server.db)
    # Exact direct index declarations from the approved server baseline.
    await db.users.create_index('email', unique=True)
    await db.settings.create_index('user_id', unique=True)
    await db.daily_costs.create_index([('user_id', 1), ('date', 1)], unique=True)
    await db.analyses.create_index([('user_id', 1), ('created_at', -1)])
    await db.webhook_parse_failures.create_index('occurred_at', expireAfterSeconds=30 * 24 * 60 * 60)
    await db.product_costs.create_index([('user_id', 1), ('sku_normalized', 1)], unique=True)
    await db.product_costs.create_index([('user_id', 1), ('is_active', 1)])
    await db.product_costs.create_index([('user_id', 1), ('product_id', 1)])
    await db.shipping_payments.create_index([('user_id', 1), ('company_name', 1), ('payment_date', -1)])
    await db.webhook_tokens.create_index('user_id', unique=True)
    await db.webhook_tokens.create_index('token', unique=True)
    await db.unified_orders.create_index([('user_id', 1), ('order_number', 1)], unique=True)
    await db.unified_orders.create_index([('user_id', 1), ('order_date', -1)])
    await db.unified_orders.create_index([('user_id', 1), ('data_source', 1)])
    await db.operating_salaries.create_index([('user_id', 1), ('status', 1)])
    await db.operating_rentals.create_index([('user_id', 1), ('status', 1)])
    await db.operating_daily_expenses.create_index([('user_id', 1), ('date', -1)])
    await db.operating_prepaid_expenses.create_index([('user_id', 1), ('status', 1)])
    await db.operating_prepaid_expenses.create_index([('user_id', 1), ('expense_type', 1)])
    await db.payment_adjustments.create_index([('user_id', 1), ('adjusted_at', -1)])
    await db.payment_adjustments.create_index([('user_id', 1), ('order_number', 1)])
    await db.payment_adjustments.create_index([('user_id', 1), ('provider', 1)])
    await db.accounts.create_index([('user_id', 1), ('account_type', 1)])
    await db.accounts.create_index([('user_id', 1), ('status', 1)])
    await db.account_transactions.create_index([('user_id', 1), ('account_id', 1), ('transaction_date', -1)])

    for prepare in (
        server.ensure_import_jobs_indexes,
        server.ensure_transfers_indexes,
        server.ensure_accounts_indexes,
        server.ensure_liabilities_indexes,
        server.ensure_recurring_obligation_indexes,
        server.ensure_counterparties_indexes,
        server.ensure_purchase_invoices_indexes,
        server.ensure_custom_app_indexes,
        server.ensure_ad_account_indexes,
        server.ensure_financial_provider_app_indexes,
        server.ensure_bnpl_indexes,
        server.ensure_alerts_indexes,
        server.ensure_employee_v2_indexes,
        server._ads_v2_ensure_indexes,
        server.ensure_integrations_control_center_indexes,
        server.ensure_first_party_attribution_indexes,
        server.ensure_customer_intelligence_foundation_indexes,
        server.ensure_settlements_indexes,
        server.ensure_preparation_indexes,
        server.ensure_payment_settlements_indexes,
        ensure_ledger_indexes,
        ensure_campaign_ai_indexes,
        ensure_qoyod_indexes,
    ):
        await prepare(db)
        db.assert_success()

    # The original Salla helper also loads credentials into a process cache.
    # Migration needs only its indexes, not that unrelated cache side effect.
    await db.salla_integrations.create_index("user_id", unique=True)
    await db.salla_oauth_states.create_index("state", unique=True)
    await db.salla_oauth_states.create_index("expires_at", expireAfterSeconds=0)
    await ensure_abandoned_cart_indexes(db)
    db.assert_success()
    await ensure_sync_indexes(db)
    db.assert_success()
    await db.bank_transfer_reviews.create_index(
        [("user_id", 1), ("source_type", 1), ("source_id", 1), ("target_bank_id", 1)],
        unique=True,
        partialFilterExpression={"source_type": {"$in": [
            "salla", "tamara", "tabby", "imkan", "shipping_cod", "customer_transfer",
        ]}},
        name="uniq_review_source_target",
    )
    for store_type in (MongoLoginSecurityStore, ProgressiveLoginStore,
                       PasskeyStore, MfaChallengeStore, EmailOtpChallengeStore):
        await store_type(db).ensure_indexes()
        db.assert_success()

    existing_user = await server.db.users.find_one({}, {"_id": 1})
    if existing_user is None:
        await seed_admin(server.db)
    elif await server.db.users.find_one({"role": "owner"}, {"_id": 1}) is None:
        raise IndexInitializationFailed("Existing accounts have no Owner; explicit account recovery is required")
