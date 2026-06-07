"""Settlement Cycle settings + Smart Alerts engine (Iter-90).

Phase A — Settings (CRUD)
Phase B — Health endpoint that runs the state machine on REAL data:
          orders (`unified_orders`) ↔ transfers (`account_transfers`)
          using FIFO consumption to bucket every uncovered SAR into:
            🟢 in_cycle    — issuance window not yet closed
            🟡 awaiting    — issuance done, waiting for next transfer weekday
            🟠 due_today   — expected arrival = today
            🔴 overdue     — expected arrival passed

  Endpoints (all under /api):
    GET  /api/settlement-cycle/settings
    PUT  /api/settlement-cycle/settings
    POST /api/settlement-cycle/reset
    GET  /api/settlement-cycle/health
"""

from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from auth import get_current_user_from_db
from order_status_policy import get_policy_map, resolve_category
from payment_gateway_metrics import (
    PAYMENT_METHOD_REGISTRY,
    resolve_canonical,
)
from reconciliation_routes import ACCOUNT_KEY_TO_CENTRAL_KEYS


GATEWAYS = ["salla", "tamara", "tabby", "emkan"]

# All-week is the safest default — the merchant restricts via the UI.
DEFAULT_CYCLE = {
    "issuance_days": 8,
    "transfer_days": 2,
    "transfer_weekdays": [0, 1, 2, 3, 4, 5, 6],
    "alerts_enabled": True,
}


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _parse_date(s) -> Optional[date]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s.date()
    if isinstance(s, date):
        return s
    s = str(s)[:10]
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


# Python date.weekday(): Mon=0..Sun=6
# Our convention (matches Frontend): Sun=0..Sat=6
def _pyweekday_to_app(d: date) -> int:
    return (d.weekday() + 1) % 7   # Mon(0) → 1, Sun(6) → 0


def _next_transfer_day(start: date, allowed_weekdays: list[int]) -> date:
    """Smallest date ≥ start whose app-weekday is in allowed list."""
    if not allowed_weekdays:
        return start
    for i in range(0, 14):
        c = start + timedelta(days=i)
        if _pyweekday_to_app(c) in allowed_weekdays:
            return c
    return start


def _expected_arrival(order_date: date, cycle: dict) -> date:
    """order_date + issuance_days → next transfer weekday + transfer_days."""
    settle = order_date + timedelta(days=int(cycle["issuance_days"]))
    transfer = _next_transfer_day(settle, cycle["transfer_weekdays"])
    return transfer + timedelta(days=int(cycle["transfer_days"]))


def _bucket(arrival: date, today: date, settle: date, transfer: date) -> str:
    if today < settle:
        return "in_cycle"
    if today < transfer:
        return "awaiting"
    if today < arrival:
        return "awaiting"        # transfer initiated, en route
    if today == arrival:
        return "due_today"
    return "overdue"


class CycleRow(BaseModel):
    gateway: str
    issuance_days: int = Field(ge=0, le=90)
    transfer_days: int = Field(ge=0, le=30)
    transfer_weekdays: list[int]
    alerts_enabled: bool = True


class CycleUpdate(BaseModel):
    items: list[CycleRow]


def attach_settlement_cycle_routes(parent_router: APIRouter, db) -> None:
    router = APIRouter(prefix="/settlement-cycle", tags=["reconciliation"])

    async def current_user(request: Request) -> dict:
        return await get_current_user_from_db(request, db)

    async def _load_cycles(uid: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        async for doc in db.settlement_cycle_settings.find(
            {"user_id": uid}, {"_id": 0}
        ):
            g = doc.get("gateway")
            if g in GATEWAYS:
                out[g] = doc
        # Apply defaults for any missing gateway
        for g in GATEWAYS:
            if g not in out:
                out[g] = {"gateway": g, **DEFAULT_CYCLE, "is_default": True}
            else:
                out[g] = {**DEFAULT_CYCLE, **out[g], "is_default": False}
        return out

    # ── Settings ────────────────────────────────────────────────────────
    @router.get("/settings")
    async def get_settings(user: dict = Depends(current_user)):
        cycles = await _load_cycles(user["id"])
        return {
            "gateways": [
                {
                    "key": g,
                    "name_ar": PAYMENT_METHOD_REGISTRY[
                        ACCOUNT_KEY_TO_CENTRAL_KEYS[g][0]
                    ]["name_ar"] if g != "salla" else "سلة (مدى/بطاقة/Apple Pay/STC)",
                    **cycles[g],
                }
                for g in GATEWAYS
            ],
            "defaults": DEFAULT_CYCLE,
            "weekday_labels": ["الأحد", "الإثنين", "الثلاثاء", "الأربعاء",
                               "الخميس", "الجمعة", "السبت"],
        }

    @router.put("/settings")
    async def update_settings(payload: CycleUpdate = Body(...),
                              user: dict = Depends(current_user)):
        uid = user["id"]
        now = datetime.now(timezone.utc).isoformat()
        from pymongo import UpdateOne
        ops = []
        for r in payload.items:
            if r.gateway not in GATEWAYS:
                raise HTTPException(400, f"بوابة غير مدعومة: {r.gateway}")
            ops.append(UpdateOne(
                {"user_id": uid, "gateway": r.gateway},
                {"$set": {
                    "user_id": uid,
                    "gateway": r.gateway,
                    "issuance_days": int(r.issuance_days),
                    "transfer_days": int(r.transfer_days),
                    "transfer_weekdays": sorted({int(x) for x in r.transfer_weekdays
                                                 if 0 <= int(x) <= 6}),
                    "alerts_enabled": bool(r.alerts_enabled),
                    "updated_at": now,
                }},
                upsert=True,
            ))
        if ops:
            await db.settlement_cycle_settings.bulk_write(ops, ordered=False)
        return {"ok": True, "updated": len(ops)}

    @router.post("/reset")
    async def reset_settings(user: dict = Depends(current_user)):
        r = await db.settlement_cycle_settings.delete_many(
            {"user_id": user["id"]}
        )
        return {"ok": True, "deleted": r.deleted_count}

    # ── Health (state machine) ──────────────────────────────────────────
    @router.get("/health")
    async def health(user: dict = Depends(current_user)):
        uid = user["id"]
        cycles = await _load_cycles(uid)
        today = _today_utc()
        policy_overrides = await get_policy_map(db, uid)

        # 1) Pull all confirmed orders, group by gateway (FIFO by order_date)
        orders_by_gateway: dict[str, list[tuple[date, float, str]]] = {
            g: [] for g in GATEWAYS
        }
        cursor = db.unified_orders.find(
            {"user_id": uid},
            {"_id": 0, "payment_method": 1, "actual_payment_method": 1,
             "order_date": 1, "total_amount": 1, "order_status": 1,
             "order_number": 1},
        ).sort("order_date", 1)

        # Map central canonical → account-level gateway key (salla bucket
        # absorbs mada/applepay/stcpay/credit_card)
        central_to_gw: dict[str, str] = {}
        for gw, central_keys in ACCOUNT_KEY_TO_CENTRAL_KEYS.items():
            if gw in GATEWAYS:
                for ck in central_keys:
                    central_to_gw[ck] = gw

        async for o in cursor:
            category = resolve_category(o.get("order_status"), policy_overrides)
            if category != "confirmed":
                continue
            raw_pm = o.get("actual_payment_method") or o.get("payment_method")
            canon = resolve_canonical(raw_pm)
            gw = central_to_gw.get(canon)
            if not gw:
                continue
            od = _parse_date(o.get("order_date"))
            if od is None:
                continue
            amount = float(o.get("total_amount") or 0)
            if amount <= 0:
                continue
            orders_by_gateway[gw].append((od, amount, o.get("order_number", "")))

        # 2) Pull recorded transfers per gateway account
        transfers_by_gateway: dict[str, float] = {g: 0.0 for g in GATEWAYS}
        accs = {a["id"]: a async for a in db.accounts.find(
            {"user_id": uid, "account_type": "payment_platform"},
            {"_id": 0, "id": 1, "normalized_payment_method": 1},
        )}
        async for t in db.account_transfers.find(
            {"user_id": uid, "from_account_id": {"$in": list(accs.keys())}},
            {"_id": 0, "from_account_id": 1, "amount": 1},
        ):
            acc = accs.get(t["from_account_id"])
            if not acc:
                continue
            gw = acc.get("normalized_payment_method")
            if gw in transfers_by_gateway:
                transfers_by_gateway[gw] += float(t.get("amount") or 0)

        # 3) For each gateway: FIFO-consume transferred amount, bucket the rest
        rows = []
        grand = {"expected": 0.0, "transferred": 0.0, "pending": 0.0,
                 "overdue": 0.0, "overdue_count": 0}
        for gw in GATEWAYS:
            cycle = cycles[gw]
            orders = sorted(orders_by_gateway[gw], key=lambda x: x[0])
            transferred = round(transfers_by_gateway[gw], 2)
            expected = round(sum(a for _, a, _ in orders), 2)

            # FIFO consume the transferred amount from oldest orders
            remaining = transferred
            pending_orders: list[tuple[date, float, str]] = []
            for od, amt, num in orders:
                if remaining >= amt:
                    remaining -= amt
                    continue
                if remaining > 0:
                    pending_orders.append((od, round(amt - remaining, 2), num))
                    remaining = 0
                else:
                    pending_orders.append((od, amt, num))

            # 4) Bucket pending orders
            buckets = {
                "in_cycle":  {"amount": 0.0, "count": 0},
                "awaiting":  {"amount": 0.0, "count": 0},
                "due_today": {"amount": 0.0, "count": 0},
                "overdue":   {"amount": 0.0, "count": 0,
                              "oldest_due_date": None, "max_days_late": 0,
                              "oldest_order_date": None, "oldest_amount": 0.0,
                              "oldest_order_number": None},
            }
            for od, amt, num in pending_orders:
                settle = od + timedelta(days=int(cycle["issuance_days"]))
                transfer_dt = _next_transfer_day(settle, cycle["transfer_weekdays"])
                arrival = transfer_dt + timedelta(days=int(cycle["transfer_days"]))
                state = _bucket(arrival, today, settle, transfer_dt)
                buckets[state]["amount"] += amt
                buckets[state]["count"]  += 1
                if state == "overdue":
                    days_late = (today - arrival).days
                    if buckets["overdue"]["oldest_due_date"] is None \
                       or arrival < buckets["overdue"]["oldest_due_date"]:
                        buckets["overdue"]["oldest_due_date"] = arrival
                        buckets["overdue"]["oldest_order_date"] = od
                        buckets["overdue"]["oldest_amount"] = amt
                        buckets["overdue"]["oldest_order_number"] = num
                    if days_late > buckets["overdue"]["max_days_late"]:
                        buckets["overdue"]["max_days_late"] = days_late

            # round
            for b in buckets.values():
                b["amount"] = round(b["amount"], 2)
            buckets["overdue"]["oldest_due_date"] = (
                buckets["overdue"]["oldest_due_date"].isoformat()
                if buckets["overdue"]["oldest_due_date"] else None
            )
            buckets["overdue"]["oldest_order_date"] = (
                buckets["overdue"]["oldest_order_date"].isoformat()
                if buckets["overdue"]["oldest_order_date"] else None
            )

            pending_total = round(
                buckets["in_cycle"]["amount"] + buckets["awaiting"]["amount"]
                + buckets["due_today"]["amount"] + buckets["overdue"]["amount"],
                2,
            )

            # Next expected transfer date = next allowed weekday from today
            next_transfer = _next_transfer_day(today, cycle["transfer_weekdays"])

            row = {
                "gateway": gw,
                "name_ar": "سلة" if gw == "salla"
                           else PAYMENT_METHOD_REGISTRY[
                               ACCOUNT_KEY_TO_CENTRAL_KEYS[gw][0]
                           ]["name_ar"],
                "cycle": {
                    "issuance_days": int(cycle["issuance_days"]),
                    "transfer_days": int(cycle["transfer_days"]),
                    "transfer_weekdays": cycle["transfer_weekdays"],
                    "alerts_enabled": bool(cycle["alerts_enabled"]),
                },
                "totals": {
                    "expected": expected,
                    "transferred": transferred,
                    "pending": pending_total,
                    "overdue": buckets["overdue"]["amount"],
                    "overdue_count": buckets["overdue"]["count"],
                },
                "buckets": buckets,
                "next_transfer_date": next_transfer.isoformat(),
                "today": today.isoformat(),
            }
            rows.append(row)

            grand["expected"] += expected
            grand["transferred"] += transferred
            grand["pending"] += pending_total
            grand["overdue"] += buckets["overdue"]["amount"]
            grand["overdue_count"] += buckets["overdue"]["count"]

        for k in ("expected", "transferred", "pending", "overdue"):
            grand[k] = round(grand[k], 2)

        return {
            "today": today.isoformat(),
            "totals": grand,
            "rows": rows,
        }

    parent_router.include_router(router)
