"""Append-only original-currency wallet movements, bound to financial transactions.

Opening confirmations describe an already posted P07 opening; this module never
creates or changes a general-ledger opening or derives foreign units from SAR.
"""
from decimal import Decimal, InvalidOperation

from accounting_atomic import SessionDatabase
from accounting_advertising_contract import FX, decimal, digest, fail, money, now
from accounting_advertising_sources import aware
from accounting_clean_start_guard import require_accounting_safe_active
from accounting_ledger_v2 import query_entries_v2, verify_active_opening_v2

OPENINGS = "mz2_ad_wallet_openings_v2"
MOVEMENTS = "mz2_ad_wallet_movements_v2"
_OPENING_META = {"_id", "id", "content_hash", "confirmed_by", "confirmed_at", "status", "owner", "request_hash"}


def opening_key(owner, binding):
    return digest([owner, binding["wallet_financial_account_id"], binding["currency"]])


def opening_hash(row):
    return digest({k: v for k, v in row.items() if k not in _OPENING_META})


def _valid_movement(row):
    return (digest({k: v for k, v in row.items() if k not in {"_id", "created_at", "content_hash", "sealed_hash"}})
            == row.get("content_hash") and
            digest({k: v for k, v in row.items() if k != "sealed_hash"}) == row.get("sealed_hash"))


def _scope(db, owner):
    if (not isinstance(db, SessionDatabase) or db._owner != owner
            or not db._session.in_transaction):
        fail("ad_wallet_financial_transaction_required")


def _signed(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        fail("ad_wallet_original_amount_invalid")
    if not amount.is_finite() or abs(amount) > Decimal("92233720368547758.07"):
        fail("ad_wallet_original_amount_invalid")
    return amount


async def validate_opening_evidence(db, owner, binding, payload):
    """Validate setup evidence only; no wallet or journal write occurs here."""
    row = dict(payload)
    if (not binding.get("wallet_financial_account_id") or binding["currency"] == "SAR"
            or row.get("currency") != binding["currency"]
            or row.get("platform") != binding["platform"]
            or row.get("integration_account_id") != binding["integration_account_id"]):
        fail("ad_wallet_opening_identity_mismatch")
    amount = decimal(row["original_currency_amount"])
    instant = aware(row["effective_at"], "effective_at")
    if amount == 0:
        if (row.get("zero_original_confirmed") is not True or decimal(row["opening_sar_amount"]) != 0
                or row.get("fx_snapshot_id") is not None or not str(row.get("evidence") or "").strip()):
            fail("ad_wallet_explicit_zero_evidence_required")
        await _verify_zero_opening(db, owner, binding, row)
    else:
        if row.pop("zero_original_confirmed", False):
            fail("ad_wallet_zero_confirmation_amount_conflict")
        snapshot = await db[FX].find_one({"_id": row.get("fx_snapshot_id"), "user_id": owner, "status": "active"})
        if (not snapshot or not snapshot.get("confirmed_by")
                or snapshot.get("currency") != binding["currency"]
                or snapshot.get("business_date") != instant.date().isoformat()
                or money(amount * decimal(snapshot["fx_rate_to_sar"])) != money(row["opening_sar_amount"])):
            fail("ad_wallet_opening_fx_evidence_invalid")
    row.update(user_id=owner, wallet_financial_account_id=binding["wallet_financial_account_id"],
               version=1, original_currency_amount=format(amount, "f"),
               opening_sar_amount=money(row["opening_sar_amount"]),
               effective_at=instant.isoformat(), business_date=instant.date().isoformat())
    return row


async def _verify_zero_opening(db, owner, binding, row):
    settings = await db.settings.find_one({"user_id": owner}) or {}
    cutover = settings.get("mezan2_financial_cutover") or {}
    if (row["opening_txn_group_id"] != cutover.get("opening_active_txn_group_id")
            or not await verify_active_opening_v2(db, user_id=owner, cutover=cutover)
            or aware(row["effective_at"], "effective_at") != aware(cutover.get("cutover_at"), "cutover_at")):
        fail("ad_wallet_zero_native_opening_invalid")
    manifest = cutover.get("opening_balance_zero_accounts")
    if not isinstance(manifest, list):
        fail("ad_wallet_approved_zero_manifest_required")
    zeros = [z for z in manifest if isinstance(z, dict)
        and (z.get("entity_type"), z.get("entity_id"), z.get("sub_account")) ==
            ("ad_account", binding["wallet_financial_account_id"], "balance")]
    if (len(zeros) != 1 or zeros[0].get("opening_balance_txn_group_id") != row["opening_txn_group_id"]
            or not str(zeros[0].get("evidence_ref") or "").strip()
            or aware(zeros[0].get("accounting_at"), "accounting_at") != aware(row["effective_at"], "effective_at")):
        fail("ad_wallet_approved_zero_manifest_required")
    legs = await query_entries_v2(db, user_id=owner, txn_group_id=row["opening_txn_group_id"],
        entity_type="ad_account", entity_id=binding["wallet_financial_account_id"], sub_account="balance", limit=1)
    if legs:
        fail("ad_wallet_zero_native_amount_conflict")


async def append_wallet_movement(db, owner, binding, original_delta, movement_type,
                                 source_id, business_date, effective_at, fx_snapshot_id,
                                 txn_group_id, actor_id, evidence):
    """Insert an immutable, content-checked economic event within atomic_owner."""
    _scope(db, owner)
    await require_accounting_safe_active(db, user_id=owner)
    amount = _signed(original_delta)
    zero_opening = movement_type == "opening_zero" and amount == 0 and fx_snapshot_id is None
    if (movement_type not in {"opening", "opening_zero", "spend", "spend_adjustment", "wallet_funding"} or not source_id
            or (not txn_group_id and not (movement_type == "spend_adjustment" and isinstance(evidence, dict) and evidence.get("no_sar_delta") is True))
            or not actor_id or not evidence or (not fx_snapshot_id and not zero_opening)
            or (movement_type == "opening_zero" and not zero_opening)
            or (movement_type in {"opening", "wallet_funding"} and amount <= 0)
            or (movement_type == "spend" and amount >= 0)):
        fail("ad_wallet_movement_contract_invalid")
    instant = aware(effective_at, "effective_at")
    key = digest([owner, binding["wallet_financial_account_id"], binding["currency"], movement_type, source_id])
    body = dict(user_id=owner, platform=binding["platform"],
                integration_account_id=binding["integration_account_id"],
                wallet_financial_account_id=binding["wallet_financial_account_id"],
                currency=binding["currency"], original_currency_amount=format(amount, "f"),
                movement_type=movement_type, source_id=source_id, business_date=str(business_date),
                effective_at=instant.isoformat(), fx_snapshot_id=fx_snapshot_id,
                txn_group_id=txn_group_id, version=1, actor_id=actor_id, evidence=evidence)
    content_hash = digest(body)
    prior = await db[MOVEMENTS].find_one({"_id": key, "user_id": owner})
    if prior:
        if prior.get("content_hash") != content_hash or not _valid_movement(prior):
            fail("ad_wallet_movement_idempotency_conflict")
        return prior
    row = dict(body, _id=key, content_hash=content_hash, created_at=now())
    row["sealed_hash"] = digest(row)
    await db[MOVEMENTS].insert_one(row)
    return row


async def materialize_opening(db, owner, binding):
    """Attach foreign units to an existing verified native opening, never post it."""
    _scope(db, owner)
    await require_accounting_safe_active(db, user_id=owner)
    row = await db[OPENINGS].find_one({"_id": opening_key(owner, binding), "user_id": owner, "status": "active"})
    if not row or not row.get("confirmed_by") or row.get("content_hash") != opening_hash(row):
        fail("ad_wallet_original_opening_evidence_required")
    await validate_opening_evidence(db, owner, binding, {k: v for k, v in row.items() if k not in _OPENING_META})
    if decimal(row["original_currency_amount"]) == 0:
        return await append_wallet_movement(db, owner, binding, "0", "opening_zero", row["_id"],
            row["business_date"], row["effective_at"], None, row["opening_txn_group_id"],
            row["confirmed_by"], {"zero_original_confirmed": True, "source_evidence": row["evidence"]})
    settings = await db.settings.find_one({"user_id": owner})
    cutover = (settings or {}).get("mezan2_financial_cutover") or {}
    if (row["opening_txn_group_id"] != cutover.get("opening_active_txn_group_id")
            or not await verify_active_opening_v2(db, user_id=owner, cutover=cutover)
            or aware(row["effective_at"], "effective_at") != aware(cutover["cutover_at"], "cutover_at")):
        fail("ad_wallet_opening_native_proof_invalid")
    legs = await query_entries_v2(db, user_id=owner, txn_group_id=row["opening_txn_group_id"],
                                 entity_type="ad_account", entity_id=binding["wallet_financial_account_id"],
                                 sub_account="balance", limit=1000)
    if (not legs or len(legs) == 1000 or any(r["side"] != "debit" for r in legs)
            or sum((decimal(r["amount"]) for r in legs), Decimal(0)) != decimal(row["opening_sar_amount"])):
        fail("ad_wallet_opening_native_amount_mismatch")
    return await append_wallet_movement(db, owner, binding, row["original_currency_amount"], "opening",
        row["_id"], row["business_date"], row["effective_at"], row["fx_snapshot_id"],
        row["opening_txn_group_id"], row["confirmed_by"], row["evidence"])


async def wallet_capacity(db, owner, binding, effective_at):
    """Minimum original units available from this economic date onward."""
    _scope(db, owner)
    rows = await db[MOVEMENTS].find({"user_id": owner,
        "wallet_financial_account_id": binding["wallet_financial_account_id"],
        "currency": binding["currency"]}).to_list(10001)
    if len(rows) > 10000:
        fail("ad_wallet_history_limit_requires_reconciliation")
    buckets = {}
    for row in rows:
        if not _valid_movement(row):
            fail("ad_wallet_movement_integrity_failure")
        instant = aware(row["effective_at"], "effective_at")
        buckets[instant] = buckets.get(instant, Decimal(0)) + _signed(row["original_currency_amount"])
    instant = aware(effective_at, "effective_at")
    balance = sum((v for k, v in buckets.items() if k <= instant), Decimal(0))
    capacity = balance
    for key in sorted(k for k in buckets if k > instant):
        balance += buckets[key]
        capacity = min(capacity, balance)
    return capacity


async def wallet_position(db, owner, binding):
    """Read-only Stage 12 view; confirmed setup evidence is not a posted balance."""
    opening = await db[OPENINGS].find_one({"_id": opening_key(owner, binding), "user_id": owner})
    if opening and opening.get("content_hash") != opening_hash(opening):
        fail("ad_wallet_original_opening_evidence_required")
    rows = await db[MOVEMENTS].find({"user_id": owner,
        "wallet_financial_account_id": binding["wallet_financial_account_id"],
        "currency": binding["currency"]}).to_list(10001)
    if len(rows) > 10000 or any(not _valid_movement(row) for row in rows):
        fail("ad_wallet_movement_integrity_failure")
    return {"currency": binding["currency"], "opening_confirmed": bool(opening and opening.get("status") == "active"),
        "confirmed_opening_amount": (opening or {}).get("original_currency_amount"),
        "opening_materialized": any(row["movement_type"] in {"opening", "opening_zero"} for row in rows),
        "posted_original_balance": format(sum((_signed(row["original_currency_amount"]) for row in rows), Decimal(0)), "f") if rows else None}
