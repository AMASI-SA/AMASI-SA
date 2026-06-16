"""Iter-192-ext — Shipping Ledger (per-order, delivered-only).

A read-only analytic endpoint that powers the new «دفتر الشحن
التفصيلي» frontend page. Strict rules:

  • Only orders whose order_status_policy category resolves to
    "confirmed" (delivered / completed / تم التوصيل) are included.
    Anything else lives in diagnostics — never in this ledger.
  • For each delivered order we compute:
      - shipping_cost, COD amount, COD fee, net due
      - settlement_status: settled / partial / unsettled
      - settlement_type: which legs of the courier_cod_settle were
        used to clear this row's COD (best-effort match by courier).
  • For PREPAID couriers, the row carries a `prepaid_shipping=True`
    flag so the UI clearly shows the cost as "مدفوعة مقدماً" and
    excludes it from courier-payable totals.

This module writes NOTHING. It only reads `unified_orders`,
`general_ledger`, and `settings.shipping_companies`.
"""
from typing import Optional

from fastapi import APIRouter, Depends, Query

from shipping_companies import normalize_shipping_company
from order_status_policy import get_policy_map, resolve_category


def attach_shipping_ledger_routes(parent_router: APIRouter, db,
                                  current_user) -> None:
    router = APIRouter(prefix="/shipping-ledger", tags=["shipping-ledger"])

    async def _payment_mode_map(uid: str) -> dict[str, str]:
        s = await db.settings.find_one({"user_id": uid},
                                       {"_id": 0, "shipping_companies": 1})
        out: dict[str, str] = {}
        for c in (s or {}).get("shipping_companies", []) or []:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            key, _ = normalize_shipping_company(name)
            out[key] = "deferred" if c.get("is_deferred") else "prepaid"
            out[name] = out[key]
        return out

    async def _cod_fee_map(uid: str) -> dict[str, tuple[float, float]]:
        """{canonical_key: (percent, fixed_per_order)}"""
        s = await db.settings.find_one({"user_id": uid},
                                       {"_id": 0, "shipping_companies": 1})
        out: dict[str, tuple[float, float]] = {}
        for c in (s or {}).get("shipping_companies", []) or []:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            key, _ = normalize_shipping_company(name)
            out[key] = (
                float(c.get("cod_fee_percent") or 0),
                float(c.get("cod_fee_fixed_per_order") or 0),
            )
        return out

    async def _cost_map(uid: str) -> dict[str, float]:
        """Iter-235 — fallback cost per shipping company.

        Reads `settings.shipping_companies[].cost` and exposes it
        keyed by both the canonical key AND the raw configured name
        (case-insensitive) so any spelling that arrives on the
        order's `shipping_company` field resolves correctly.
        """
        s = await db.settings.find_one({"user_id": uid},
                                       {"_id": 0, "shipping_companies": 1})
        out: dict[str, float] = {}
        for c in (s or {}).get("shipping_companies", []) or []:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            try:
                cost = float(c.get("cost") or 0)
            except (TypeError, ValueError):
                cost = 0.0
            if cost <= 0:
                continue
            key, _ = normalize_shipping_company(name)
            out[key] = cost
            out[name.lower()] = cost
        return out

    @router.get("")
    async def shipping_ledger(
        user: dict = Depends(current_user),
        date_from: Optional[str] = Query(None),
        date_to: Optional[str] = Query(None),
        courier: Optional[str] = Query(None),
        payment_mode: Optional[str] = Query(None, regex="^(prepaid|deferred)$"),
        payment_method: Optional[str] = Query(None),
        settlement_status: Optional[str] = Query(
            None, regex="^(unsettled|partial|settled)$"),
        has_cod: Optional[bool] = Query(None),
        limit: int = Query(2000, ge=1, le=10000),
    ):
        uid = user["id"]
        policy = await get_policy_map(db, uid)
        pm_map = await _payment_mode_map(uid)
        fee_map = await _cod_fee_map(uid)
        cost_map = await _cost_map(uid)

        q: dict = {"user_id": uid, "is_pre_accounting": {"$ne": True}}
        if date_from or date_to:
            q["order_date"] = {}
            if date_from:
                q["order_date"]["$gte"] = date_from
            if date_to:
                q["order_date"]["$lte"] = date_to + "T23:59:59"

        rows: list[dict] = []
        totals = {
            "delivered_count": 0,
            "total_shipping_cost": 0.0,
            "total_cod": 0.0,
            "total_cod_fees": 0.0,
            "total_settled": 0.0,
            "total_unsettled": 0.0,
            "total_prepaid_shipping": 0.0,
            "total_deferred_shipping": 0.0,
        }

        async for o in db.unified_orders.find(q, {
            "_id": 0, "id": 1, "order_id": 1, "reference_id": 1,
            "order_date": 1, "received_at": 1, "created_at": 1,
            "order_status": 1, "shipping_company": 1, "payment_method": 1,
            "actual_payment_method": 1, "total_amount": 1,
            "shipping_cost": 1, "cod_amount": 1, "cod_fee": 1,
        }).limit(limit):
            raw_status = (o.get("order_status") or "").strip()
            cat = resolve_category(raw_status, policy)
            if cat != "confirmed":
                continue   # delivered/completed only

            comp_raw = (o.get("shipping_company") or "").strip()
            comp_key, comp_display = normalize_shipping_company(comp_raw)
            mode = pm_map.get(comp_key) or pm_map.get(comp_raw) or "prepaid"
            method = (o.get("actual_payment_method")
                      or o.get("payment_method") or "")
            is_cod = ("cod" in method.lower()
                      or "الاستلام" in method
                      or "الدفع عند" in method)

            ship_cost_from_order = float(o.get("shipping_cost") or 0)
            # Iter-235 — fallback to per-company configured cost when
            # Salla didn't disclose shipping_cost on the order (common
            # for COD / BNPL flows). Mirrors the logic already used by
            # `/api/shipping-accounts` so both pages stay consistent.
            if ship_cost_from_order > 0:
                ship_cost = ship_cost_from_order
                cost_source = "salla"
            else:
                fallback = (
                    cost_map.get(comp_key)
                    or cost_map.get(comp_display.lower())
                    or cost_map.get(comp_raw.lower())
                    or 0.0
                )
                ship_cost = float(fallback)
                cost_source = (
                    "company_settings" if ship_cost > 0 else "none"
                )
            cod_amt = float(o.get("cod_amount") or
                            (o.get("total_amount") or 0) if is_cod else 0)
            if not is_cod:
                cod_amt = 0.0
            cod_fee = float(o.get("cod_fee") or 0)
            if cod_amt > 0 and cod_fee == 0:
                pct, fixed = fee_map.get(comp_key, (0.0, 0.0))
                cod_fee = round(cod_amt * pct + fixed, 2)

            # net due to merchant from courier = cod - shipping(if deferred) - cod_fee
            shipping_against_cod = ship_cost if mode == "deferred" else 0.0
            net_due = round(cod_amt - shipping_against_cod - cod_fee, 2)

            # Filters
            if courier and comp_display != courier and comp_key != courier:
                continue
            if payment_mode and mode != payment_mode:
                continue
            if payment_method and payment_method.lower() not in method.lower():
                continue
            if has_cod is True and not is_cod:
                continue
            if has_cod is False and is_cod:
                continue

            # Settlement is best-effort at row level. The Universal
            # Ledger doesn't link COD settlements back to individual
            # orders yet (Iter-193 will). For now we mark every row in
            # this iteration as "unsettled" — the page-level summary
            # tells you total open COD per courier.
            sstatus = "unsettled"
            if settlement_status and settlement_status != sstatus:
                continue

            rows.append({
                "id": o.get("id"),
                "order_id": o.get("order_id") or o.get("reference_id"),
                "order_date": o.get("order_date") or o.get("received_at")
                              or o.get("created_at"),
                "shipping_company": comp_display,
                "shipping_company_key": comp_key,
                "payment_mode": mode,
                "payment_method": method,
                "order_status": raw_status,
                "shipping_cost": round(ship_cost, 2),
                # Iter-235 — provenance flag for the new UI column.
                "shipping_cost_source": cost_source,
                "prepaid_shipping": mode == "prepaid",
                "cod_amount": round(cod_amt, 2),
                "cod_fee": round(cod_fee, 2),
                "net_due": net_due,
                "settlement_status": sstatus,
                "settlement_type": "—",
            })
            totals["delivered_count"] += 1
            totals["total_shipping_cost"] += ship_cost
            totals["total_cod"] += cod_amt
            totals["total_cod_fees"] += cod_fee
            totals["total_unsettled"] += net_due
            if mode == "prepaid":
                totals["total_prepaid_shipping"] += ship_cost
            else:
                totals["total_deferred_shipping"] += ship_cost

        for k in totals:
            if isinstance(totals[k], float):
                totals[k] = round(totals[k], 2)

        # Iter-235 — Per-company breakdown so the UI can show the
        # effective cost being used for each shipping carrier.
        by_company: dict[str, dict] = {}
        for r in rows:
            name = r["shipping_company"]
            entry = by_company.setdefault(name, {
                "shipping_company": name,
                "orders_count": 0,
                "total_shipping_cost": 0.0,
                "from_salla_count": 0,
                "from_company_settings_count": 0,
                "none_count": 0,
                "configured_cost": cost_map.get(
                    r["shipping_company_key"]) or cost_map.get(
                    name.lower()) or 0.0,
                "payment_mode": r["payment_mode"],
            })
            entry["orders_count"] += 1
            entry["total_shipping_cost"] += r["shipping_cost"]
            src = r.get("shipping_cost_source") or "none"
            if src == "salla":
                entry["from_salla_count"] += 1
            elif src == "company_settings":
                entry["from_company_settings_count"] += 1
            else:
                entry["none_count"] += 1
        # Round + add an effective_cost_per_order quick stat.
        per_company = []
        for v in by_company.values():
            v["total_shipping_cost"] = round(v["total_shipping_cost"], 2)
            v["effective_cost_per_order"] = round(
                v["total_shipping_cost"] / v["orders_count"], 2,
            ) if v["orders_count"] > 0 else 0.0
            per_company.append(v)
        per_company.sort(key=lambda x: x["total_shipping_cost"],
                         reverse=True)

        rows.sort(key=lambda r: r["order_date"] or "", reverse=True)
        return {"rows": rows, "totals": totals,
                "per_company": per_company,
                "delivered_only": True, "limit": limit}

    parent_router.include_router(router)
