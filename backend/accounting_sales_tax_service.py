"""Versioned manual sales-tax policy for Mezan 2 only.

The audit history is embedded in the same owner document as the versions, so
a successful settings write cannot lose its audit record in a second write.
"""
from datetime import datetime, timezone
from uuid import uuid4

from accounting_sales_tax import TaxError, instant, rate_value, select_version, split_gross


async def read_policy(db, owner):
    return await db.mz2_sales_tax_policies.find_one({"_id": owner}) or {
        "_id": owner, "user_id": owner, "revision": 0, "versions": [], "audit": [],
    }


async def save_policy(db, *, owner, actor_id, rate, effective_at, revision, reason):
    percent = rate_value(rate)
    starts = instant(effective_at).isoformat()
    if not actor_id or not owner or not str(reason or "").strip():
        raise TaxError("actor_owner_and_reason_required")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise TaxError("invalid_policy_revision")
    current = await read_policy(db, owner)
    if current["revision"] != revision:
        raise TaxError("policy_changed_refresh_required")
    if len(current["versions"]) >= 1000:
        raise TaxError("policy_history_limit")
    now = datetime.now(timezone.utc).isoformat()
    version = {"id": str(uuid4()), "rate": str(percent), "effective_at": starts,
               "revision": revision + 1, "created_at": now, "created_by": actor_id}
    audit = {"id": str(uuid4()), "action": "manual_sales_tax_version",
             "actor_id": actor_id, "at": now, "reason": str(reason).strip(),
             "previous_revision": revision, "version": version}
    record = {**current, "revision": revision + 1,
              "versions": [*current["versions"], version],
              "audit": [*current["audit"], audit]}
    if revision == 0:
        try:
            await db.mz2_sales_tax_policies.insert_one(record)
        except Exception as exc:
            if getattr(exc, "code", None) == 11000:
                raise TaxError("policy_changed_refresh_required") from None
            raise
    else:
        result = await db.mz2_sales_tax_policies.replace_one(
            {"_id": owner, "revision": revision}, record)
        if result.matched_count != 1:
            raise TaxError("policy_changed_refresh_required")
    return record


def sale_snapshot(policy, event, source_order):
    version = select_version(policy["versions"], event["recognized_at"])
    calculation = split_gross(event["amount"], version["rate"])
    return {
        **calculation, "policy_id": policy["_id"], "policy_version_id": version["id"],
        "effective_at": version["effective_at"], "revision": version["revision"],
        "recognized_at": event["recognized_at"],
        # Source amounts remain evidence only: never fed into the calculation.
        "source_tax_for_review": {
            key: source_order.get(key) for key in
            ("tax_percent", "tax_rate", "tax_amount", "vat_amount")
            if key in source_order
        },
    }
