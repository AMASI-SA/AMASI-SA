"""Native MZ2 advertising journal bridge. No legacy reads or writes."""
from decimal import Decimal

from fastapi import HTTPException

from accounting_atomic import atomic_owner
from accounting_advertising_contract import (
    BINDINGS, EXPENSES, FACTS, FX, LOCKS, POSTINGS, BankMovement, SpendPost,
    decimal, digest, fail, money, now, spend_legs,
)
from accounting_advertising_setup import confirmed_binding, owner_actor
from accounting_advertising_sources import ACCOUNTS, SOURCES, account_view, aware, daily_source
from accounting_clean_start_guard import require_accounting_safe_active
from accounting_ledger_v2 import AccountingLedgerV2Error, post_journal_v2, query_entries_v2
from accounting_periods import assert_open_journal_periods


async def _fx(db, owner, fact, snapshot_id):
    if fact["original_currency"] == "SAR":
        if snapshot_id is not None:
            fail("ad_sar_fx_snapshot_not_applicable")
        return dict(original_currency="SAR", original_amount=fact["original_amount"],
            fx_rate_to_sar="1", fx_at=fact["effective_at"], fx_source="SAR_identity",
            evidence="same_currency_no_conversion", sar_amount=money(fact["original_amount"]))
    if not snapshot_id:
        fail("ad_fx_snapshot_required")
    row = await db[FX].find_one({"_id": snapshot_id, "user_id": owner, "status": "active"})
    if (not row or not row.get("confirmed_by") or row.get("currency") != fact["original_currency"]
            or row.get("business_date") != fact["business_date"]):
        fail("ad_fx_snapshot_invalid")
    return dict(original_currency=fact["original_currency"], original_amount=fact["original_amount"],
        fx_snapshot_id=snapshot_id, fx_rate_to_sar=row["fx_rate_to_sar"], fx_at=row["fx_at"],
        fx_source=row["fx_source"], evidence=row["evidence"],
        sar_amount=money(decimal(fact["original_amount"]) * decimal(row["fx_rate_to_sar"])))


async def _wallet_capacity(db, owner, identity, effective_at):
    """Minimum available SAR from the economic date through all existing legs.

    Checking only the current balance misses a negative historical interval
    concealed by later funding. Read through the sealed ledger API, in session.
    """
    rows, after = [], None
    while True:
        page = await query_entries_v2(db, user_id=owner, entity_type="ad_account",
            entity_id=identity, sub_account="balance", after_entry_no=after, limit=1000)
        rows.extend(page)
        if len(rows) > 10000:
            fail("ad_wallet_history_limit_requires_reconciliation")
        if len(page) < 1000:
            break
        after = int(page[-1]["entry_no"])
    economic_at = aware(effective_at, "effective_at")
    buckets = {}
    for row in rows:
        instant = aware(row["effective_at"], "effective_at")
        if row["side"] not in {"debit", "credit"}:
            fail("ad_wallet_ledger_integrity_failure")
        amount = decimal(row["amount"]) * (1 if row["side"] == "debit" else -1)
        buckets[instant] = buckets.get(instant, Decimal(0)) + amount
    balance = sum((amount for instant, amount in buckets.items() if instant <= economic_at), Decimal(0))
    capacity = balance
    for instant in sorted(i for i in buckets if i > economic_at):
        balance += buckets[instant]
        capacity = min(capacity, balance)
    return capacity


async def post_spend(db, actor_id, payload: SpendPost):
    owner = await owner_actor(db, actor_id)
    async def post(scoped):
        await owner_actor(scoped, actor_id, owner)
        # Same lock as paused setup: binding/expense/FX cannot change underneath posting.
        lock = await scoped[LOCKS].update_one({"_id": owner}, {"$inc": {"revision": 1}})
        if lock.matched_count != 1:
            fail("ad_setup_missing")
        await require_accounting_safe_active(scoped, user_id=owner)
        fact = await scoped[FACTS].find_one({"_id": payload.snapshot_id, "user_id": owner, "status": "active"})
        if not fact or fact.get("native_accounting_eligible") is not True or not fact.get("confirmed_by"):
            fail("ad_approved_spend_snapshot_missing")
        current = await daily_source(scoped, owner, fact["platform"], fact["integration_account_id"], fact["business_date"])
        # One economic day, across revisions and even regenerated integration IDs.
        day_key = digest([owner, fact["platform"], fact["platform_account_id"], fact["business_date"]])
        prior = await scoped[POSTINGS].find_one({"_id": day_key, "user_id": owner})
        if current["source_revision"] != fact["source_revision"]:
            fail("ad_adjustment_reconciliation_required" if prior else "ad_source_changed_refresh_required")
        request_hash = digest(payload.model_dump(mode="json"))
        if prior:
            if prior["source_revision"] != fact["source_revision"]:
                fail("ad_adjustment_reconciliation_required")
            if prior["request_hash"] != request_hash:
                fail("ad_post_request_conflict")
            return {"status": "already_posted", "txn_group_id": prior["txn_group_id"]}
        binding = await confirmed_binding(scoped, owner, fact["platform"], fact["integration_account_id"])
        expense = await scoped[EXPENSES].find_one({"user_id": owner, "purpose": "advertising", "status": "active"})
        if not expense or not expense.get("confirmed_by"):
            fail("ad_expense_identity_missing")
        fx = await _fx(scoped, owner, fact, payload.fx_snapshot_id)
        wallet_balance = Decimal(0)
        if binding.get("wallet_financial_account_id"):
            if fact["original_currency"] != "SAR":
                fail("ad_foreign_wallet_native_balance_contract_missing")
            wallet_balance = await _wallet_capacity(scoped, owner,
                binding["wallet_financial_account_id"], fact["effective_at"])
        entries = spend_legs(binding, expense["entity_id"], fx["sar_amount"], wallet_balance, payload.wallet_sar_amount)
        await assert_open_journal_periods(scoped, owner, [{"metadata": {"accounting_at": fact["effective_at"]}}])
        provenance = {k: v for k, v in fact.items() if k != "_id"}
        result = await post_journal_v2(scoped._db, user_id=owner, actor_id=actor_id, actor_name=actor_id,
            idempotency_key="ad-spend:" + day_key, txn_type="advertising_daily_spend",
            source="mz2_advertising_v2", effective_at=fact["effective_at"], entries=entries,
            metadata={"ad_source_snapshot": provenance, "ad_binding_id": binding["id"],
                "ad_binding_version": binding["version"], "ad_expense_identity_id": expense["id"],
                "ad_fx": fx, "accounting_at": fact["effective_at"]}, mongo_session=scoped._session)
        group_id = result["group"]["txn_group_id"]
        await scoped[POSTINGS].insert_one({"_id": day_key, "user_id": owner,
            "snapshot_id": payload.snapshot_id, "source_revision": fact["source_revision"],
            "request_hash": request_hash, "txn_group_id": group_id, "posted_at": now()})
        return {"status": "posted", "txn_group_id": group_id}
    try:
        return await atomic_owner(db, owner, post)
    except AccountingLedgerV2Error as error:
        fail(error.code)


async def require_financial_ledger_identity(*args, **kwargs):
    """Track A port. Deliberately unimplemented until its approved contract lands.

    No dynamic import/fallback to external_ref, bank names, or legacy registries.
    Integration must replace this port with Track A's actual validated signature
    and add end-to-end evidence/idempotency tests before enabling bank posting.
    """
    fail("track_a_require_financial_ledger_identity_not_integrated")


async def bank_movement(db, actor_id, payload: BankMovement):
    owner = await owner_actor(db, actor_id)
    async def blocked(scoped):
        await owner_actor(scoped, actor_id, owner)
        await confirmed_binding(scoped, owner, payload.platform, payload.integration_account_id)
        await require_financial_ledger_identity(scoped, owner=owner,
                                               financial_account_id=payload.bank_financial_account_id)
        # An adapter alone must never inadvertently enable unverified money movement.
        fail("track_a_bank_evidence_and_posting_integration_required")
    return await atomic_owner(db, owner, blocked)


async def stage12_context(db, actor_id):
    owner = await owner_actor(db, actor_id)
    rows = await db[ACCOUNTS].find({"user_id": owner,
        "provider": {"$in": [p for p, _ in SOURCES.values()]}}, {"_id": 0}).to_list(1001)
    if len(rows) > 1000:
        fail("ad_stage12_account_limit_exceeded")
    items = []
    for row in rows:
        item = account_view(row)
        item.update(wallet_binding=None, payable_binding=None, funding_mode=None,
                    readiness="NOT_READY", missing_contract_reason=None,
                    daily_source_collection=SOURCES[item["platform"]][1],
                    daily_spend_readiness="NOT_READY",
                    daily_spend_gap="approved_closed_daily_snapshot_and_fx_required",
                    bank_movement_readiness="NOT_READY",
                    bank_movement_gap="track_a_require_financial_ledger_identity_not_integrated")
        try:
            binding = await confirmed_binding(db, owner, item["platform"], item["integration_account_id"])
            item.update(wallet_binding=binding.get("wallet_financial_account_id"),
                payable_binding=binding.get("payable_financial_account_id"), funding_mode=binding["funding_mode"],
                binding_version=binding["version"])
            expense = await db[EXPENSES].find_one({"user_id": owner, "purpose": "advertising", "status": "active"})
            if not expense:
                fail("ad_expense_identity_missing")
            if not item.get("timezone"):
                fail("ad_source_timezone_or_date_missing")
            if binding.get("wallet_financial_account_id") and item["currency"] != "SAR":
                fail("ad_foreign_wallet_native_balance_contract_missing")
            item.update(readiness="SETUP_READY", missing_contract_reason="approved_daily_snapshot_and_fx_required_for_posting")
        except HTTPException as error:
            item["missing_contract_reason"] = error.detail.get("code") if isinstance(error.detail, dict) else error.detail
        items.append(item)
    return {"stage": 12, "identity_source": ACCOUNTS, "items": items,
            "readiness": "GAP" if not items else "REQUIRES_DAILY_REVIEW",
            "missing_contract_reason": "ad_v2_integration_missing" if not items else None}
