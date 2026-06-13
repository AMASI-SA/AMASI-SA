"""
Iter-159h — Smart Settlement Alerts
====================================
Scans the merchant's data and produces in-app notifications for events
that need attention: overdue BNPL settlements, amount discrepancies,
missing Salla invoices, courier-balance buildups, unmatched orders,
and ad-account debts approaching the credit limit.

Each generator function is idempotent: if a "new" or "snoozed" alert
with the same fingerprint already exists, it is updated in place
instead of being duplicated.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel


# ── Defaults ────────────────────────────────────────────────────────────
DEFAULT_BNPL_OVERDUE_DAYS = 7
DEFAULT_AMOUNT_DIFF_PCT = 0.05          # 5 %
DEFAULT_MISSING_SALLA_DAYS = 14
DEFAULT_COURIER_BALANCE_THRESHOLD = 5000.0
DEFAULT_AD_DEBT_PCT = 0.50              # debt > 50 % of (debt + balance)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fp(alert_type: str, entity_type: str, entity_id: str) -> str:
    return f"{alert_type}:{entity_type}:{entity_id or '_global'}"


async def _upsert_alert(db, user_id: str, payload: dict) -> str:
    """Idempotent upsert keyed by fingerprint.

    If an open ("new" or "snoozed") alert with the same fingerprint
    exists, its `message`, `severity`, `metadata` and `updated_at` are
    refreshed.  Resolved/dismissed alerts are NEVER auto-resurrected.
    """
    fp = payload["fingerprint"]
    existing = await db.settlement_alerts.find_one(
        {"user_id": user_id, "fingerprint": fp,
         "status": {"$in": ["new", "snoozed"]}},
        {"_id": 0, "id": 1, "status": 1},
    )
    if existing:
        await db.settlement_alerts.update_one(
            {"id": existing["id"]},
            {"$set": {
                "title": payload["title"],
                "message": payload["message"],
                "severity": payload["severity"],
                "metadata": payload.get("metadata") or {},
                "updated_at": _now_iso(),
            }},
        )
        return existing["id"]
    aid = str(uuid.uuid4())
    payload.update({
        "id": aid, "user_id": user_id, "status": "new",
        "snoozed_until": None,
        "created_at": _now_iso(), "updated_at": _now_iso(),
    })
    await db.settlement_alerts.insert_one(payload)
    return aid


# ── Generators ─────────────────────────────────────────────────────────
async def _gen_overdue_bnpl(db, user_id: str, settings: dict) -> int:
    """Orders with payment_method ∈ {tabby, tamara} whose settlement
    has not arrived after N days from order received_at."""
    days = int(settings.get("bnpl_overdue_days", DEFAULT_BNPL_OVERDUE_DAYS))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cursor = db.orders.find({
        "user_id": user_id,
        "payment_method": {"$in": ["tabby", "tamara"]},
        "received_at": {"$lte": cutoff},
        "$or": [{"actual_payment_method": {"$exists": False}},
                {"actual_payment_method": None}],
    }, {"_id": 0, "id": 1, "order_number": 1, "received_at": 1,
        "amount": 1, "payment_method": 1}).limit(200)
    n = 0
    async for o in cursor:
        await _upsert_alert(db, user_id, {
            "alert_type": "overdue_bnpl",
            "severity": "warning",
            "title": "تأخّر تسوية BNPL",
            "message": f"الطلب #{o.get('order_number', o['id'][:8])} "
                       f"({o.get('payment_method', '?')}) لم تصله تسوية "
                       f"منذ أكثر من {days} يوم.",
            "related_entity_type": "order",
            "related_entity_id": o["id"],
            "related_entity_url": "/settlements-overview",
            "fingerprint": _fp("overdue_bnpl", "order", o["id"]),
            "metadata": {"days_threshold": days,
                         "received_at": o.get("received_at"),
                         "amount": o.get("amount")},
        })
        n += 1
    return n


async def _gen_amount_diff(db, user_id: str, settings: dict) -> int:
    """Settled orders whose actual_amount differs from expected by >X%."""
    pct = float(settings.get("amount_diff_pct", DEFAULT_AMOUNT_DIFF_PCT))
    cursor = db.orders.find({
        "user_id": user_id,
        "actual_amount": {"$exists": True, "$ne": None, "$gt": 0},
        "amount": {"$exists": True, "$ne": None, "$gt": 0},
    }, {"_id": 0, "id": 1, "order_number": 1, "amount": 1,
        "actual_amount": 1, "payment_method": 1}).limit(2000)
    n = 0
    async for o in cursor:
        exp = float(o["amount"])
        act = float(o["actual_amount"])
        if exp <= 0:
            continue
        diff_pct = abs(act - exp) / exp
        if diff_pct < pct:
            continue
        sign = "أقل" if act < exp else "أكثر"
        await _upsert_alert(db, user_id, {
            "alert_type": "amount_diff",
            "severity": "warning" if diff_pct < 0.2 else "critical",
            "title": "فرق في مبلغ التسوية",
            "message": f"الطلب #{o.get('order_number', o['id'][:8])} "
                       f"تسوّى بـ {act:.2f} ر.س، {sign} من المتوقع "
                       f"({exp:.2f} ر.س) بنسبة {diff_pct * 100:.1f}%.",
            "related_entity_type": "order",
            "related_entity_id": o["id"],
            "related_entity_url": "/settlements-overview",
            "fingerprint": _fp("amount_diff", "order", o["id"]),
            "metadata": {"expected": exp, "actual": act,
                         "diff_pct": round(diff_pct * 100, 2),
                         "threshold_pct": round(pct * 100, 2)},
        })
        n += 1
    return n


async def _gen_missing_salla(db, user_id: str, settings: dict) -> int:
    """No Salla settlement file uploaded in the last N days."""
    days = int(settings.get("missing_salla_days", DEFAULT_MISSING_SALLA_DAYS))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    latest = await db.settlement_files.find_one(
        {"user_id": user_id, "provider": "salla"},
        sort=[("uploaded_at", -1)],
    )
    if not latest:
        # Brand-new account — skip noise.
        return 0
    up = latest.get("uploaded_at")
    if isinstance(up, str):
        try:
            up = datetime.fromisoformat(up.replace("Z", "+00:00"))
        except Exception:
            return 0
    if up.tzinfo is None:
        up = up.replace(tzinfo=timezone.utc)
    if up >= cutoff:
        return 0
    age = (datetime.now(timezone.utc) - up).days
    await _upsert_alert(db, user_id, {
        "alert_type": "missing_salla",
        "severity": "info",
        "title": "لم تُرفع فاتورة سلة جديدة",
        "message": f"مرّ {age} يوم منذ آخر فاتورة سلة مرفوعة. "
                   "تأكد من رفع التسويات الأخيرة.",
        "related_entity_type": "provider",
        "related_entity_id": "salla",
        "related_entity_url": "/salla-settlements",
        "fingerprint": _fp("missing_salla", "provider", "salla"),
        "metadata": {"days_since_last_upload": age,
                     "threshold_days": days,
                     "last_file_id": latest.get("id")},
    })
    return 1


async def _gen_high_courier_balance(db, user_id: str, settings: dict) -> int:
    """Shipping companies with positive outstanding balance > threshold."""
    threshold = float(settings.get("courier_balance_threshold",
                                    DEFAULT_COURIER_BALANCE_THRESHOLD))
    # Aggregate over shipping_company_ledger per company.
    agg = await db.shipping_company_ledger.aggregate([
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$company_id",
                    "balance": {"$sum": "$amount"}}},
    ]).to_list(500)
    n = 0
    for r in agg:
        bal = float(r.get("balance") or 0)
        if abs(bal) < threshold:
            continue
        cid = r["_id"]
        if not cid:
            continue
        company = await db.shipping_companies.find_one(
            {"id": cid, "user_id": user_id},
            {"_id": 0, "name": 1, "id": 1},
        )
        if not company:
            continue
        direction = "مستحقّة لك" if bal < 0 else "مستحقّة عليك"
        await _upsert_alert(db, user_id, {
            "alert_type": "high_courier_balance",
            "severity": "warning",
            "title": f"رصيد {company.get('name', 'شركة شحن')} مرتفع",
            "message": f"الرصيد الحالي {abs(bal):,.2f} ر.س {direction} — "
                       "تجاوز الحد المعتاد.",
            "related_entity_type": "shipping_company",
            "related_entity_id": cid,
            "related_entity_url": "/shipping-transfers",
            "fingerprint": _fp("high_courier_balance",
                                "shipping_company", cid),
            "metadata": {"balance": round(bal, 2),
                         "threshold": threshold,
                         "direction": "owed_to_you" if bal < 0 else "you_owe"},
        })
        n += 1
    return n


async def _gen_unmatched_order(db, user_id: str, settings: dict) -> int:
    """Orders with no `actual_payment_method` after N days from received_at
    (any payment method, including cash/mada)."""
    days = int(settings.get("unmatched_order_days", 10))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cnt = await db.orders.count_documents({
        "user_id": user_id,
        "received_at": {"$lte": cutoff},
        "$or": [{"actual_payment_method": {"$exists": False}},
                {"actual_payment_method": None},
                {"actual_payment_method": ""}],
        "payment_method": {"$nin": ["cash", "cod", "تحويل"]},
    })
    if cnt == 0:
        return 0
    await _upsert_alert(db, user_id, {
        "alert_type": "unmatched_order",
        "severity": "info" if cnt < 10 else "warning",
        "title": "طلبات غير مطابقة مع تسويات",
        "message": f"يوجد {cnt} طلب لم تُطابق مع أي تسوية بعد مرور أكثر "
                   f"من {days} يوم على استلامها.",
        "related_entity_type": "orders_bucket",
        "related_entity_id": f"unmatched_{days}d",
        "related_entity_url": "/settlements-overview",
        "fingerprint": _fp("unmatched_order", "orders_bucket",
                            f"older_than_{days}d"),
        "metadata": {"count": cnt, "days_threshold": days},
    })
    return 1


async def _gen_high_ad_debt(db, user_id: str, settings: dict) -> int:
    """Ad accounts where debt > X% of (debt + available balance)."""
    pct = float(settings.get("ad_debt_pct", DEFAULT_AD_DEBT_PCT))
    # All ad_account counterparties + their open debt.
    accts = await db.counterparties.find(
        {"user_id": user_id, "kind": "ad_account"},
        {"_id": 0, "id": 1, "name": 1, "balance": 1},
    ).to_list(200)
    n = 0
    for a in accts:
        balance = float(a.get("balance") or 0)
        debt_rows = await db.liabilities.aggregate([
            {"$match": {"user_id": user_id, "kind": "ad_account",
                        "counterparty_id": a["id"],
                        "status": {"$in": ["unpaid", "partial"]}}},
            {"$group": {"_id": None,
                        "open": {"$sum": {"$subtract": [
                            "$expected_amount", "$paid_amount"]}}}},
        ]).to_list(1)
        debt = float(debt_rows[0]["open"]) if debt_rows else 0.0
        if debt <= 0:
            continue
        denom = debt + max(balance, 0)
        ratio = debt / denom if denom > 0 else 1.0
        if ratio < pct:
            continue
        await _upsert_alert(db, user_id, {
            "alert_type": "high_ad_debt",
            "severity": "warning" if ratio < 0.8 else "critical",
            "title": f"مديونية {a.get('name')} مرتفعة",
            "message": f"المديونية الحالية {debt:,.2f} ر.س تمثّل "
                       f"{ratio * 100:.0f}% من السقف الائتماني الفعلي. "
                       "يُنصح بالسداد قريباً.",
            "related_entity_type": "ad_account",
            "related_entity_id": a["id"],
            "related_entity_url": "/ad-accounts",
            "fingerprint": _fp("high_ad_debt", "ad_account", a["id"]),
            "metadata": {"open_debt": round(debt, 2),
                         "balance": round(balance, 2),
                         "ratio_pct": round(ratio * 100, 1),
                         "threshold_pct": round(pct * 100, 1)},
        })
        n += 1
    return n


GENERATORS = [
    ("overdue_bnpl",          _gen_overdue_bnpl),
    ("amount_diff",           _gen_amount_diff),
    ("missing_salla",         _gen_missing_salla),
    ("high_courier_balance",  _gen_high_courier_balance),
    ("unmatched_order",       _gen_unmatched_order),
    ("high_ad_debt",          _gen_high_ad_debt),
]


# ── Models ─────────────────────────────────────────────────────────────
class SnoozeIn(BaseModel):
    hours: int = 24


class AlertSettingsIn(BaseModel):
    bnpl_overdue_days: Optional[int] = None
    amount_diff_pct: Optional[float] = None
    missing_salla_days: Optional[int] = None
    courier_balance_threshold: Optional[float] = None
    unmatched_order_days: Optional[int] = None
    ad_debt_pct: Optional[float] = None
    enabled_types: Optional[list[str]] = None


# ── Router ─────────────────────────────────────────────────────────────
async def _get_settings(db, user_id: str) -> dict:
    s = await db.alert_settings.find_one({"user_id": user_id}, {"_id": 0})
    return s or {}


async def _expire_snoozed(db, user_id: str) -> None:
    """Move alerts whose snooze window has elapsed back to 'new'."""
    now = _now_iso()
    await db.settlement_alerts.update_many(
        {"user_id": user_id, "status": "snoozed",
         "snoozed_until": {"$ne": None, "$lte": now}},
        {"$set": {"status": "new", "snoozed_until": None,
                  "updated_at": now}},
    )


def attach_alerts_routes(api, db, current_user_dep):
    router = APIRouter(prefix="/alerts", tags=["alerts"])

    @router.get("/unread-count")
    async def unread_count(user: dict = Depends(current_user_dep)):
        await _expire_snoozed(db, user["id"])
        n = await db.settlement_alerts.count_documents(
            {"user_id": user["id"], "status": "new"})
        return {"count": n}

    @router.get("")
    async def list_alerts(
        status: Optional[str] = None,
        limit: int = 100,
        user: dict = Depends(current_user_dep),
    ):
        await _expire_snoozed(db, user["id"])
        q = {"user_id": user["id"]}
        if status:
            q["status"] = status
        else:
            q["status"] = {"$in": ["new", "snoozed"]}
        rows = await db.settlement_alerts.find(q, {"_id": 0}) \
            .sort([("severity", -1), ("created_at", -1)]) \
            .limit(min(limit, 500)).to_list(500)
        # Group by severity for a quick badge.
        sev_count = {"critical": 0, "warning": 0, "info": 0}
        for r in rows:
            sev_count[r.get("severity", "info")] = \
                sev_count.get(r.get("severity", "info"), 0) + 1
        return {"alerts": rows, "by_severity": sev_count, "total": len(rows)}

    @router.post("/refresh")
    async def refresh(user: dict = Depends(current_user_dep)):
        settings = await _get_settings(db, user["id"])
        enabled = settings.get("enabled_types") or [t for t, _ in GENERATORS]
        await _expire_snoozed(db, user["id"])
        created: dict[str, int] = {}
        for atype, fn in GENERATORS:
            if atype not in enabled:
                continue
            try:
                created[atype] = await fn(db, user["id"], settings)
            except Exception as e:  # don't blow up the whole batch
                created[atype] = -1
                # Best-effort log
                print(f"[alerts] generator {atype} failed: {e}")
        n = await db.settlement_alerts.count_documents(
            {"user_id": user["id"], "status": "new"})
        return {"created": created, "unread": n}

    @router.post("/{alert_id}/read")
    async def mark_read(alert_id: str, user: dict = Depends(current_user_dep)):
        res = await db.settlement_alerts.update_one(
            {"id": alert_id, "user_id": user["id"]},
            {"$set": {"status": "read", "updated_at": _now_iso()}},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="التنبيه غير موجود.")
        return {"ok": True}

    @router.post("/read-all")
    async def mark_all_read(user: dict = Depends(current_user_dep)):
        res = await db.settlement_alerts.update_many(
            {"user_id": user["id"], "status": "new"},
            {"$set": {"status": "read", "updated_at": _now_iso()}},
        )
        return {"ok": True, "marked": res.modified_count}

    @router.post("/{alert_id}/dismiss")
    async def dismiss(alert_id: str, user: dict = Depends(current_user_dep)):
        res = await db.settlement_alerts.update_one(
            {"id": alert_id, "user_id": user["id"]},
            {"$set": {"status": "dismissed", "updated_at": _now_iso()}},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="التنبيه غير موجود.")
        return {"ok": True}

    @router.post("/{alert_id}/snooze")
    async def snooze(alert_id: str, payload: SnoozeIn,
                     user: dict = Depends(current_user_dep)):
        hours = max(1, min(payload.hours, 24 * 30))
        until = (datetime.now(timezone.utc) +
                 timedelta(hours=hours)).isoformat()
        res = await db.settlement_alerts.update_one(
            {"id": alert_id, "user_id": user["id"]},
            {"$set": {"status": "snoozed", "snoozed_until": until,
                      "updated_at": _now_iso()}},
        )
        if res.matched_count == 0:
            raise HTTPException(status_code=404, detail="التنبيه غير موجود.")
        return {"ok": True, "snoozed_until": until}

    @router.get("/settings")
    async def get_settings(user: dict = Depends(current_user_dep)):
        s = await _get_settings(db, user["id"])
        return {
            "bnpl_overdue_days": s.get("bnpl_overdue_days",
                                        DEFAULT_BNPL_OVERDUE_DAYS),
            "amount_diff_pct": s.get("amount_diff_pct",
                                      DEFAULT_AMOUNT_DIFF_PCT),
            "missing_salla_days": s.get("missing_salla_days",
                                         DEFAULT_MISSING_SALLA_DAYS),
            "courier_balance_threshold": s.get(
                "courier_balance_threshold",
                DEFAULT_COURIER_BALANCE_THRESHOLD),
            "unmatched_order_days": s.get("unmatched_order_days", 10),
            "ad_debt_pct": s.get("ad_debt_pct", DEFAULT_AD_DEBT_PCT),
            "enabled_types": s.get("enabled_types",
                                    [t for t, _ in GENERATORS]),
        }

    @router.patch("/settings")
    async def patch_settings(payload: AlertSettingsIn,
                              user: dict = Depends(current_user_dep)):
        update = {k: v for k, v in payload.model_dump().items()
                  if v is not None}
        if not update:
            return {"ok": True}
        update["updated_at"] = _now_iso()
        await db.alert_settings.update_one(
            {"user_id": user["id"]},
            {"$set": update, "$setOnInsert": {"user_id": user["id"]}},
            upsert=True,
        )
        return {"ok": True}

    api.include_router(router)


async def ensure_alerts_indexes(db) -> None:
    await db.settlement_alerts.create_index([("user_id", 1), ("status", 1)])
    await db.settlement_alerts.create_index(
        [("user_id", 1), ("fingerprint", 1), ("status", 1)])
    await db.settlement_alerts.create_index(
        [("user_id", 1), ("created_at", -1)])
    await db.alert_settings.create_index("user_id", unique=True)
