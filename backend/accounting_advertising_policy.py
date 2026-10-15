"""Setup-once deterministic calendar policy; no deployment scheduler side effects."""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from accounting_advertising_contract import EXPENSES, POLICIES, decimal, digest, fail
from accounting_advertising_sources import aware, require_account


async def latest_policy(db, owner, platform, integration_id):
    rows = await db[POLICIES].find({"user_id": owner, "platform": platform,
        "integration_account_id": integration_id}).sort("version", -1).limit(1).to_list(1)
    if not rows or rows[0].get("status") != "active":
        fail("ad_automation_policy_missing")
    return rows[0]


async def validate_policy(db, owner, body):
    from accounting_advertising_setup import confirmed_binding
    binding = await confirmed_binding(db, owner, body["platform"], body["integration_account_id"])
    if binding["version"] != body["binding_version"]:
        fail("ad_policy_binding_version_mismatch")
    account = await require_account(db, owner, body["platform"], body["integration_account_id"])
    if body["business_timezone"] != account.get("timezone"):
        fail("ad_policy_account_timezone_mismatch")
    try:
        for key in ("business_timezone", "schedule_timezone"):
            ZoneInfo(body[key])
    except (ValueError, ZoneInfoNotFoundError):
        fail("ad_policy_timezone_invalid")
    if body["platform"] == "meta" and (body["schedule_timezone"], body["run_at"]) != ("Asia/Riyadh", "01:00"):
        fail("ad_meta_schedule_requires_riyadh_0100")
    if body["platform"] == "snapchat" and body["business_timezone"] != "America/New_York":
        fail("ad_snapchat_new_york_business_day_required")
    expense = await db[EXPENSES].find_one({"user_id": owner, "purpose": "advertising", "status": "active"})
    if not expense:
        fail("ad_expense_identity_missing")
    fraction = body.get("wallet_fraction")
    if binding["funding_mode"] == "hybrid":
        if fraction is None or decimal(fraction) > 1:
            fail("ad_automatic_hybrid_fraction_required")
    elif fraction is not None:
        fail("ad_split_requires_hybrid")
    currency = binding["currency"]
    fx_mode = body["fx_policy"]
    if (currency == "SAR") != (fx_mode == "sar_identity"):
        fail("ad_policy_currency_fx_mismatch")
    if fx_mode == "fixed_rate":
        if not body.get("fx_rate_to_sar") or not decimal(body["fx_rate_to_sar"]):
            fail("ad_policy_fx_rate_required")
        for key in ("fx_at", "fx_source", "fx_evidence", "fx_valid_from", "fx_valid_through"):
            if not str(body.get(key) or "").strip():
                fail("ad_policy_fx_authority_required", field=key)
        aware(body["fx_at"], "fx_at")
        if body["fx_valid_from"] > body["fx_valid_through"]:
            fail("ad_policy_fx_validity_invalid")
    prior = await db[POLICIES].find({"user_id": owner, "platform": body["platform"],
        "integration_account_id": body["integration_account_id"]}).sort("version", -1).limit(1).to_list(1)
    if body["version"] != (prior[0]["version"] if prior else 0):
        fail("ad_policy_version_conflict")
    return {**body, "version": body["version"] + 1, "currency": currency,
            "platform_account_id": binding["platform_account_id"], "expense_identity_id": expense["id"],
            "expense_entity_id": expense["entity_id"], "binding_snapshot": {k: v for k, v in binding.items() if k != "_id"}}


def _scheduled_instant(day, clock, zone):
    local = datetime.combine(day, time.fromisoformat(clock))
    # Pick the later fold on autumn DST; advance nonexistent spring wall time
    # to the first valid minute. Economic-day identity still executes once.
    for _ in range(181):
        candidates = []
        for fold in (0, 1):
            instant = local.replace(tzinfo=zone, fold=fold).astimezone(timezone.utc)
            if instant.astimezone(zone).replace(tzinfo=None) == local:
                candidates.append(instant)
        if candidates:
            return max(candidates)
        local += timedelta(minutes=1)
    fail("ad_schedule_local_time_invalid")


def due_at(policy, business_date):
    day = date.fromisoformat(str(business_date))
    close = datetime.combine(day + timedelta(days=1), time.min,
        ZoneInfo(policy["business_timezone"])).astimezone(timezone.utc)
    close += timedelta(minutes=policy["close_delay_minutes"])
    zone = ZoneInfo(policy["schedule_timezone"])
    scheduled = _scheduled_instant(close.astimezone(zone).date(), policy["run_at"], zone)
    if scheduled < close:
        scheduled = _scheduled_instant(close.astimezone(zone).date() + timedelta(days=1), policy["run_at"], zone)
    return scheduled


def eligible_dates(policy, as_of):
    current = aware(as_of, "as_of")
    day = date.fromisoformat(policy["start_date"])
    last = current.astimezone(ZoneInfo(policy["business_timezone"])).date() - timedelta(days=1)
    if (last - day).days > 3660:
        fail("ad_policy_due_history_limit")
    dates = []
    while day <= last:
        if due_at(policy, day) <= current:
            dates.append(day.isoformat())
        day += timedelta(days=1)
    return dates


def economic_day_key(owner, policy, business_date):
    return digest([owner, policy["platform"], policy["platform_account_id"], str(business_date)])
