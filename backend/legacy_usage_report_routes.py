"""Iter-246 — Legacy Systems Usage Report.

Read-only diagnostic that surveys the four collections the merchant
wants to deprecate in favour of the Iter-245 `financial_movements`
system:

  • `purchase_invoices`        (legacy /purchase-invoices)
  • `daily_costs`              (legacy /daily-costs)
  • `operating_salaries`,
    `operating_rentals`,
    `operating_prepaid_expenses` (legacy /operating-expenses)
  • `liabilities` (ad-account liabilities driving /financial-input-hub)

For each collection we return:
  - total record count
  - latest activity timestamp (max(updated_at, created_at, date))
  - records in the last 30 days
  - records in the last 7 days
  - is_active (boolean) — true when any record was created in the
    last 7 days (i.e. data is still flowing)

The endpoint is forward-only — NO writes, NO migrations, NO deletes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_dt(v: Any) -> Optional[datetime]:
    """Coerce a stored date/timestamp into UTC datetime, best-effort."""
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        # Accept ISO-8601 and bare YYYY-MM-DD.
        s = v.strip()
        if not s:
            return None
        try:
            if "T" in s or " " in s:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(s + "T00:00:00+00:00")
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            return None
    return None


async def _audit_collection(db, uid: str, coll_name: str,
                            date_fields: list[str]) -> dict:
    coll = db[coll_name]
    total = await coll.count_documents({"user_id": uid})
    if total == 0:
        return {
            "collection": coll_name,
            "total": 0,
            "last_activity": None,
            "last_30d": 0,
            "last_7d": 0,
            "is_active": False,
        }

    # Walk a sample of the most recent rows to find the freshest date
    # without paying for an in-Mongo $max aggregation across mixed
    # storage types.  100 rows is plenty for an audit page.
    proj = {"_id": 0}
    for f in date_fields:
        proj[f] = 1
    cursor = coll.find({"user_id": uid}, proj).sort(
        [("created_at", -1)]).limit(100)
    docs = await cursor.to_list(100)

    latest: Optional[datetime] = None
    for d in docs:
        for f in date_fields:
            t = _to_dt(d.get(f))
            if t and (latest is None or t > latest):
                latest = t

    now = _now()
    last_30d = await coll.count_documents({
        "user_id": uid,
        "created_at": {"$gte": (now - timedelta(days=30)).isoformat()},
    })
    last_7d = await coll.count_documents({
        "user_id": uid,
        "created_at": {"$gte": (now - timedelta(days=7)).isoformat()},
    })

    return {
        "collection": coll_name,
        "total": total,
        "last_activity": latest.isoformat() if latest else None,
        "last_30d": last_30d,
        "last_7d": last_7d,
        "is_active": last_7d > 0,
    }


def make_legacy_usage_report_router(db, current_user):
    router = APIRouter(prefix="/legacy-usage-report",
                       tags=["legacy-usage-report"])

    @router.get("")
    async def report(user: dict = Depends(current_user)):
        uid = user["id"]

        # Map of UI screen → legacy collections backing it.
        screens = [
            {
                "screen": "purchase_invoices",
                "screen_label": "فواتير المشتريات (Legacy)",
                "ui_path": "/purchase-invoices",
                "replaced_by": "/new-transaction → فاتورة مورد",
                "collections": [
                    ("purchase_invoices",
                     ["updated_at", "created_at", "invoice_date"]),
                ],
            },
            {
                "screen": "daily_costs",
                "screen_label": "التكاليف اليومية (Legacy)",
                "ui_path": "/daily-costs",
                "replaced_by": "/new-transaction → مصروف عام",
                "collections": [
                    ("daily_costs",
                     ["updated_at", "created_at", "date"]),
                ],
            },
            {
                "screen": "operating_expenses",
                "screen_label": "المصروفات التشغيلية (Legacy)",
                "ui_path": "/operating-expenses",
                "replaced_by": "/new-transaction → مصروف عام",
                "collections": [
                    ("operating_salaries",
                     ["updated_at", "created_at", "month"]),
                    ("operating_rentals",
                     ["updated_at", "created_at", "month"]),
                    ("operating_prepaid_expenses",
                     ["updated_at", "created_at", "month"]),
                ],
            },
            {
                "screen": "financial_input_hub",
                "screen_label": "مركز الإدخال المالي (Legacy)",
                "ui_path": "/financial-input-hub",
                "replaced_by": "/new-transaction (الموحَّد)",
                "collections": [
                    ("liabilities",
                     ["updated_at", "created_at", "doc_date"]),
                ],
            },
        ]

        out_screens = []
        for sc in screens:
            colls = []
            agg_total = 0
            agg_last = None
            agg_30d = 0
            agg_7d = 0
            for cname, fields in sc["collections"]:
                row = await _audit_collection(db, uid, cname, fields)
                colls.append(row)
                agg_total += row["total"]
                agg_30d += row["last_30d"]
                agg_7d += row["last_7d"]
                if row["last_activity"]:
                    dt = _to_dt(row["last_activity"])
                    if dt and (agg_last is None or dt > agg_last):
                        agg_last = dt
            out_screens.append({
                "screen": sc["screen"],
                "screen_label": sc["screen_label"],
                "ui_path": sc["ui_path"],
                "replaced_by": sc["replaced_by"],
                "total_records": agg_total,
                "last_activity": agg_last.isoformat() if agg_last else None,
                "last_30d": agg_30d,
                "last_7d": agg_7d,
                "is_active": agg_7d > 0,
                "collections": colls,
            })

        return {
            "ok": True,
            "generated_at": _now().isoformat(),
            "iter": "iter246",
            "screens": out_screens,
            "summary": {
                "total_legacy_records": sum(
                    s["total_records"] for s in out_screens),
                "active_screens": [
                    s["screen"] for s in out_screens if s["is_active"]],
                "dead_screens": [
                    s["screen"] for s in out_screens
                    if not s["is_active"] and s["total_records"] > 0
                ],
            },
        }

    return router
