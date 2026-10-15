"""Automatic due-day service and append-only exception adjustments.

No job is installed/deployed here. A trusted scheduler invokes run_batch;
normal days require only the already-confirmed setup policy, never daily clicks.
"""
from datetime import datetime, timezone
from decimal import Decimal, localcontext

from fastapi import HTTPException

from accounting_atomic import atomic_owner
from accounting_advertising_contract import (
    ADJUSTMENTS, ADJUSTMENT_POSTS, AUDIT, EVENTS, EXPENSES, FACTS, FX, LOCKS,
    POLICIES, POSTINGS, decimal, digest, fail, leg, money, now,
)
from accounting_advertising_policy import due_at, economic_day_key, eligible_dates, latest_policy
from accounting_advertising_setup import confirmed_binding, owner_actor
from accounting_advertising_sources import aware, daily_source
from accounting_advertising_bridge import _fx, _wallet_capacity
from accounting_clean_start_guard import require_accounting_safe_active
from accounting_ledger_v2 import AccountingLedgerV2Error, post_journal_v2
from accounting_periods import assert_open_journal_periods

SYSTEM_ACTOR = "mz2-advertising-runner"


def _verify_seal(row, code):
    if (not row or not row.get("content_hash")
            or digest({k: v for k, v in row.items() if k not in {"_id", "content_hash"}}) != row["content_hash"]):
        fail(code)


def _verify_posting(row):
    # Earlier manual postings predate this sealed automatic contract and remain
    # explicitly unsupported as adjustment authority; never silently promote.
    if row.get("version") == 2 or row.get("policy_id") or row.get("totals"):
        _verify_seal(row, "ad_posting_integrity_failure")


async def _transaction(db, owner, callback):
    async def work(scoped):
        await owner_actor(scoped, owner, owner)
        result = await scoped[LOCKS].update_one({"_id": owner}, {"$inc": {"revision": 1}})
        if result.matched_count != 1:
            fail("ad_setup_missing")
        await require_accounting_safe_active(scoped, user_id=owner)
        return await callback(scoped)
    try:
        return await atomic_owner(db, owner, work)
    except AccountingLedgerV2Error as error:
        fail(error.code)


async def _context(db, owner, policy):
    if policy.get("status") != "active" or policy.get("confirmed_by") != owner:
        fail("ad_automation_authority_invalid")
    binding = await confirmed_binding(db, owner, policy["platform"], policy["integration_account_id"])
    if binding["version"] != policy["binding_version"]:
        fail("ad_policy_binding_version_mismatch")
    expense = await db[EXPENSES].find_one({"user_id": owner, "id": policy["expense_identity_id"], "status": "active"})
    if not expense or expense.get("entity_id") != policy["expense_entity_id"]:
        fail("ad_expense_identity_missing")
    return binding


async def _snapshot(db, owner, policy, fact):
    identity = digest([owner, "automatic", policy["id"], fact["source_revision"]])
    prior = await db[FACTS].find_one({"_id": identity, "user_id": owner})
    if prior:
        _verify_seal(prior, "ad_snapshot_integrity_failure")
        return {k: v for k, v in prior.items() if k != "_id"}
    row = {**fact, "id": identity, "user_id": owner, "owner": owner, "policy_id": policy["id"],
        "policy_version": policy["version"], "status": "active", "version": 2,
        "native_accounting_eligible": True, "native_approval_required": False,
        "missing_contract_reason": None, "created_by": SYSTEM_ACTOR, "created_at": now(),
        "setup_confirmed_by": policy["confirmed_by"], "setup_confirmed_at": policy["confirmed_at"]}
    row["content_hash"] = digest(row)
    await db[FACTS].insert_one({"_id": identity, **row})
    await db[AUDIT].insert_one({"user_id": owner, "actor_id": SYSTEM_ACTOR, "at": now(),
        "action": "automatic_source_snapshot", "contract_id": identity, "policy_id": policy["id"],
        "content_hash": row["content_hash"]})
    return row


async def _translation(db, owner, policy, fact, prior=None):
    # Adjustment preserves the original day's FX authority. It never reprices
    # an old expense at today's rate or silently switches a daily snapshot.
    if prior and prior.get("fx"):
        rate = decimal(prior["fx"]["fx_rate_to_sar"])
        return {**prior["fx"], "original_amount": fact["original_amount"],
                "sar_amount": money(decimal(fact["original_amount"]) * rate)}
    if fact["original_currency"] == "SAR":
        return await _fx(db, owner, fact, None)
    if policy["fx_policy"] == "fixed_rate":
        if not policy["fx_valid_from"] <= fact["business_date"] <= policy["fx_valid_through"]:
            fail("ad_fx_policy_outside_validity")
        identity = digest([owner, "policy_fx", policy["id"], fact["business_date"]])
        row = await db[FX].find_one({"_id": identity, "user_id": owner})
        if not row:
            row = dict(id=identity, user_id=owner, currency=fact["original_currency"],
                business_date=fact["business_date"], fx_rate_to_sar=policy["fx_rate_to_sar"],
                fx_at=policy["fx_at"], fx_source=policy["fx_source"], evidence=policy["fx_evidence"],
                policy_id=policy["id"], confirmed_by=policy["confirmed_by"], confirmed_at=policy["confirmed_at"],
                generated_at=now(), status="active", version=1)
            await db[FX].insert_one({"_id": identity, **row})
    else:
        rows = await db[FX].find({"user_id": owner, "currency": fact["original_currency"],
            "business_date": fact["business_date"], "status": "active"}).to_list(2)
        if len(rows) != 1:
            fail("ad_fx_snapshot_missing_or_ambiguous")
        identity = rows[0]["id"]
    return await _fx(db, owner, fact, identity)


def _totals(binding, policy, fact, fx):
    amount = decimal(fact["original_amount"])
    fraction = Decimal(1) if binding["funding_mode"] == "prepaid" else Decimal(0)
    if binding["funding_mode"] == "hybrid":
        fraction = decimal(policy["wallet_fraction"])
    total_sar = decimal(fx["sar_amount"]) if fx else Decimal(0)
    # Original currency is an independent quantity, not a SAR minor-unit
    # allocation. Preserve every supplied digit, including submicro amounts.
    with localcontext() as context:
        context.prec = max(28, len(amount.as_tuple().digits) + len(fraction.as_tuple().digits))
        original_wallet = amount * fraction
    wallet_sar = decimal(money(total_sar * fraction))
    return dict(original_amount=format(amount, "f"), original_wallet=format(original_wallet, "f"),
        total_sar=money(total_sar), wallet_sar=money(wallet_sar), payable_sar=money(total_sar - wallet_sar))


ZERO_TOTALS = dict(original_amount="0", original_wallet="0", total_sar="0.00", wallet_sar="0.00", payable_sar="0.00")


def _delta_entries(binding, policy, old, target):
    entries = []
    for key, entity_type, identity, sub, positive_side in (
        ("total_sar", "expense", policy["expense_entity_id"], None, "debit"),
        ("wallet_sar", "ad_account", binding.get("wallet_financial_account_id"), "balance", "credit"),
        ("payable_sar", "ad_account", binding.get("payable_financial_account_id"), "debt", "credit")):
        change = decimal(target[key]) - decimal(old[key])
        if change:
            side = positive_side if change > 0 else ("credit" if positive_side == "debit" else "debit")
            entries.append(leg(entity_type, identity, sub, side, abs(change)))
    return entries


async def _book(db, owner, policy, binding, fact, target, fx, *, old=None, source_id, actor_id, original_posting=None):
    from accounting_advertising_wallet import materialize_opening, wallet_capacity, append_wallet_movement
    old = old or ZERO_TOTALS
    delta_original_wallet = decimal(target["original_wallet"]) - decimal(old["original_wallet"])
    delta_wallet_sar = decimal(target["wallet_sar"]) - decimal(old["wallet_sar"])
    if binding.get("wallet_financial_account_id"):
        if delta_wallet_sar > 0:
            capacity = await _wallet_capacity(db, owner, binding["wallet_financial_account_id"], fact["effective_at"])
            if delta_wallet_sar > capacity:
                fail("ad_wallet_insufficient_balance")
        if fact["original_currency"] != "SAR" and delta_original_wallet:
            await materialize_opening(db, owner, binding)
            if delta_original_wallet > 0 and delta_original_wallet > await wallet_capacity(db, owner, binding, fact["effective_at"]):
                fail("ad_original_wallet_insufficient_balance")
    entries = _delta_entries(binding, policy, old, target)
    await assert_open_journal_periods(db, owner, [{"metadata": {"accounting_at": fact["effective_at"]}}])
    group_id = None
    if entries:
        result = await post_journal_v2(db._db, user_id=owner, actor_id=actor_id, actor_name=actor_id,
            idempotency_key="ad-native:" + source_id,
            txn_type="advertising_spend_adjustment" if original_posting else "advertising_daily_spend",
            source="mz2_advertising_v2", effective_at=fact["effective_at"], entries=entries,
            metadata={"accounting_at": fact["effective_at"], "ad_policy_id": policy["id"],
                "ad_source_snapshot_id": fact["id"], "ad_source_revision": fact["source_revision"],
                "ad_fx": fx, "ad_previous_totals": old, "ad_target_totals": target,
                "ad_original_posting_id": original_posting}, mongo_session=db._session)
        group_id = result["group"]["txn_group_id"]
    if fact["original_currency"] != "SAR" and delta_original_wallet:
        await append_wallet_movement(db, owner, binding, original_delta=format(-delta_original_wallet, "f"),
            movement_type="spend_adjustment" if original_posting else "spend", source_id=source_id,
            business_date=fact["business_date"], effective_at=fact["effective_at"],
            fx_snapshot_id=fx["fx_snapshot_id"], txn_group_id=group_id, actor_id=actor_id,
            evidence={"snapshot_id": fact["id"], "source_revision": fact["source_revision"], "policy_id": policy["id"], "no_sar_delta": not entries})
    return group_id


async def _latest_day(db, owner, day_key):
    initial = await db[POSTINGS].find_one({"_id": day_key, "user_id": owner})
    if not initial:
        return None, None
    _verify_posting(initial)
    changes = await db[ADJUSTMENT_POSTS].find({"user_id": owner, "day_key": day_key}).sort("sequence", -1).limit(1).to_list(1)
    if changes:
        _verify_seal(changes[0], "ad_adjustment_post_integrity_failure")
    return initial, changes[0] if changes else initial


async def _propose(db, owner, policy, fact, day_key, original, previous):
    if not previous.get("totals") or not previous.get("policy_id"):
        fail("ad_previous_posting_contract_requires_review")
    if previous["source_revision"] == fact["source_revision"]:
        return {"status": previous["status"], "txn_group_id": previous.get("txn_group_id"), "day_key": day_key}
    identity = digest([owner, day_key, previous.get("sequence", 0), previous["source_revision"], fact["source_revision"]])
    prior = await db[ADJUSTMENTS].find_one({"_id": identity, "user_id": owner})
    if prior:
        _verify_seal(prior, "ad_adjustment_proposal_integrity_failure")
        return {"status": "REVIEW_REQUIRED", "proposal_id": identity}
    snapshot = await _snapshot(db, owner, policy, fact)
    binding = await _context(db, owner, policy)
    fx = await _translation(db, owner, policy, snapshot, previous)
    target = _totals(binding, policy, snapshot, fx)
    row = dict(id=identity, user_id=owner, day_key=day_key, status="PROPOSED", policy_id=policy["id"],
        original_posting_id=day_key, original_txn_group_id=original.get("txn_group_id"),
        old_source_revision=previous["source_revision"], new_source_revision=fact["source_revision"],
        old_snapshot_id=previous["snapshot_id"], new_snapshot_id=snapshot["id"],
        previous_sequence=previous.get("sequence", 0), old_totals=previous["totals"], target_totals=target,
        original_delta=format(decimal(target["original_amount"]) - decimal(previous["totals"]["original_amount"]), "f"),
        sar_delta=format(decimal(target["total_sar"]) - decimal(previous["totals"]["total_sar"]), "f"),
        old_fx=previous.get("fx"), new_fx=fx, created_at=now(), created_by=SYSTEM_ACTOR, version=1)
    row["content_hash"] = digest(row)
    await db[ADJUSTMENTS].insert_one({"_id": identity, **row})
    await db[AUDIT].insert_one({"user_id": owner, "action": "adjustment_proposed", "at": now(),
        "actor_id": SYSTEM_ACTOR, "proposal_id": identity, "content_hash": row["content_hash"]})
    return {"status": "REVIEW_REQUIRED", "proposal_id": identity, "original_delta": row["original_delta"], "sar_delta": row["sar_delta"]}


async def run_day(db, owner, platform, integration_id, business_date, *, as_of=None, require_existing=False):
    current = aware(as_of, "as_of") if as_of is not None else datetime.now(timezone.utc)
    policy = await latest_policy(db, owner, platform, integration_id)
    if str(business_date) < policy["start_date"] or current < due_at(policy, business_date):
        return {"status": "NOT_DUE", "business_date": str(business_date)}
    async def work(scoped):
        active_policy = await latest_policy(scoped, owner, platform, integration_id)
        if active_policy["id"] != policy["id"]:
            fail("ad_policy_changed_retry")
        day_key = economic_day_key(owner, policy, business_date)
        initial, previous = await _latest_day(scoped, owner, day_key)
        if require_existing and not initial:
            fail("ad_original_posting_required")
        chosen = policy
        if previous and previous.get("policy_id"):
            chosen = await scoped[POLICIES].find_one({"user_id": owner, "id": previous["policy_id"]})
            if not chosen:
                fail("ad_previous_policy_missing")
        binding = await _context(scoped, owner, chosen)
        fact = await daily_source(scoped, owner, platform, integration_id, business_date, policy=chosen, as_of=current)
        if previous:
            return await _propose(scoped, owner, chosen, fact, day_key, initial, previous)
        snapshot = await _snapshot(scoped, owner, chosen, fact)
        zero = not decimal(snapshot["original_amount"])
        fx = None if zero else await _translation(scoped, owner, chosen, snapshot)
        target = _totals(binding, chosen, snapshot, fx)
        if not zero and not decimal(target["total_sar"]):
            fail("ad_amount_below_sar_minor_unit")
        group = None if zero else await _book(scoped, owner, chosen, binding, snapshot, target, fx,
                                               source_id=day_key, actor_id=SYSTEM_ACTOR)
        row = dict(id=day_key, user_id=owner, platform=platform, integration_account_id=integration_id,
            platform_account_id=chosen["platform_account_id"], business_date=str(business_date), policy_id=chosen["id"],
            source_revision=fact["source_revision"], snapshot_id=snapshot["id"], totals=target, fx=fx,
            status="CLOSED_ZERO" if zero else "POSTED", txn_group_id=group, posted_at=now(), sequence=0, version=2)
        row["content_hash"] = digest(row)
        await scoped[POSTINGS].insert_one({"_id": day_key, **row})
        await scoped[AUDIT].insert_one({"user_id": owner, "actor_id": SYSTEM_ACTOR, "at": now(),
            "action": row["status"], "day_key": day_key, "content_hash": row["content_hash"]})
        return {"status": row["status"], "txn_group_id": group, "day_key": day_key}
    return await _transaction(db, owner, work)


async def due_items(db, owner, *, as_of=None, limit=100):
    await owner_actor(db, owner, owner)
    if type(limit) is not int or not 1 <= limit <= 100:
        fail("ad_batch_limit_invalid", 422)
    current = as_of if as_of is not None else datetime.now(timezone.utc)
    policies = await db[POLICIES].find({"user_id": owner}).sort("version", -1).to_list(1001)
    if len(policies) > 1000:
        fail("ad_policy_limit_exceeded")
    items, seen = [], set()
    for policy in policies:
        key = (policy["platform"], policy["integration_account_id"])
        if key in seen:
            continue
        seen.add(key)
        if policy.get("status") != "active":
            continue
        for day in eligible_dates(policy, current):
            marker = await db[POSTINGS].find_one({"_id": economic_day_key(owner, policy, day), "user_id": owner})
            # Sealed positive/zero days leave the normal due queue permanently.
            # Source revision events use run_day again and enter exception review.
            if marker:
                _verify_posting(marker)
                continue
            items.append(dict(platform=policy["platform"], integration_account_id=policy["integration_account_id"],
                              business_date=day, policy_id=policy["id"], due_at=due_at(policy, day).isoformat()))
            if len(items) == limit:
                return items
    return items


async def run_batch(db, owner, *, as_of=None, limit=100):
    items = await due_items(db, owner, as_of=as_of, limit=limit)
    state = await db.mz2_atomic_owners.find_one({"_id": owner}) or {}
    if state.get("writes_paused", True) is not False:
        fail("mz2_writes_paused", 423, due_items=items)
    results = []
    for item in items:
        try:
            result = await run_day(db, owner, item["platform"], item["integration_account_id"], item["business_date"], as_of=as_of)
        except HTTPException as error:
            if error.status_code == 423:
                raise
            detail = error.detail if isinstance(error.detail, dict) else {"code": str(error.detail)}
            result = {"status": "BLOCKED", "reason": detail}
            async def record(scoped):
                identity = digest([owner, item, detail])
                if not await scoped[EVENTS].find_one({"_id": identity}):
                    await scoped[EVENTS].insert_one({"_id": identity, "user_id": owner, **item,
                        "status": "BLOCKED", "reason": detail, "at": now()})
            # Race with pause must abort the event too: paused runner is read-only.
            await atomic_owner(db, owner, record)
        results.append({**item, **result})
    return {"items": results}


async def approve_adjustment(db, actor_id, proposal_id, evidence, *, as_of=None):
    owner = await owner_actor(db, actor_id)
    if not str(evidence).strip():
        fail("ad_adjustment_review_evidence_required")
    async def work(scoped):
        proposal = await scoped[ADJUSTMENTS].find_one({"_id": proposal_id, "user_id": owner})
        if not proposal:
            fail("ad_adjustment_proposal_missing")
        _verify_seal(proposal, "ad_adjustment_proposal_integrity_failure")
        posted = await scoped[ADJUSTMENT_POSTS].find_one({"_id": proposal_id, "user_id": owner})
        if posted:
            _verify_seal(posted, "ad_adjustment_post_integrity_failure")
            return {"status": "ALREADY_POSTED", "txn_group_id": posted.get("txn_group_id"), "proposal_id": proposal_id}
        original, previous = await _latest_day(scoped, owner, proposal["day_key"])
        if previous["source_revision"] != proposal["old_source_revision"] or previous.get("sequence", 0) != proposal["previous_sequence"]:
            fail("ad_adjustment_stale_review")
        policy = await scoped[POLICIES].find_one({"id": proposal["policy_id"], "user_id": owner})
        binding = await _context(scoped, owner, policy)
        snapshot = await scoped[FACTS].find_one({"id": proposal["new_snapshot_id"], "user_id": owner})
        _verify_seal(snapshot, "ad_snapshot_integrity_failure")
        fact = await daily_source(scoped, owner, policy["platform"], policy["integration_account_id"],
            snapshot["business_date"], policy=policy, as_of=as_of)
        if fact["source_revision"] != proposal["new_source_revision"]:
            fail("ad_adjustment_source_changed")
        group = await _book(scoped, owner, policy, binding, snapshot, proposal["target_totals"], proposal["new_fx"],
            old=proposal["old_totals"], source_id=proposal_id, actor_id=actor_id, original_posting=proposal["day_key"])
        row = dict(id=proposal_id, user_id=owner, day_key=proposal["day_key"], status="ADJUSTED",
            sequence=proposal["previous_sequence"] + 1, policy_id=policy["id"], source_revision=proposal["new_source_revision"],
            old_source_revision=proposal["old_source_revision"], snapshot_id=snapshot["id"],
            totals=proposal["target_totals"], fx=proposal["new_fx"], txn_group_id=group,
            original_txn_group_id=original.get("txn_group_id"), original_posting_id=proposal["day_key"],
            original_delta=proposal["original_delta"], sar_delta=proposal["sar_delta"],
            approved_by=actor_id, approved_at=now(), evidence=evidence, version=1)
        row["content_hash"] = digest(row)
        await scoped[ADJUSTMENT_POSTS].insert_one({"_id": proposal_id, **row})
        await scoped[AUDIT].insert_one({"user_id": owner, "action": "adjustment_approved_posted", "actor_id": actor_id,
            "proposal_id": proposal_id, "content_hash": row["content_hash"], "at": now()})
        return {"status": "ADJUSTED", "txn_group_id": group, "proposal_id": proposal_id,
                "original_delta": row["original_delta"], "sar_delta": row["sar_delta"]}
    return await _transaction(db, owner, work)
