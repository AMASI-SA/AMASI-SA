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
from courier_cod_fee_rules import calculate_courier_cod_fee


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

    async def _cod_fee_map(uid: str) -> dict[str, dict]:
        """Return the full COD fee config per canonical courier key."""
        s = await db.settings.find_one({"user_id": uid},
                                       {"_id": 0, "shipping_companies": 1})
        out: dict[str, dict] = {}
        for c in (s or {}).get("shipping_companies", []) or []:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            key, _ = normalize_shipping_company(name)
            out[key] = c
        return out

    async def _cost_map(uid: str) -> dict[str, float]:
        """Iter-235/236b — fallback cost per shipping company.

        Reads `settings.shipping_companies[]` and accepts BOTH
        `cost` AND `cost_per_order` (the page persists both, but
        legacy rows may only have one). Keyed by the canonical key
        AND the raw configured name (case-insensitive).
        """
        s = await db.settings.find_one({"user_id": uid},
                                       {"_id": 0, "shipping_companies": 1})
        out: dict[str, float] = {}
        for c in (s or {}).get("shipping_companies", []) or []:
            name = (c.get("name") or "").strip()
            if not name:
                continue
            # Prefer cost_per_order (the field the page actually edits),
            # fall back to cost for legacy rows.
            try:
                cost = float(
                    c.get("cost_per_order")
                    if c.get("cost_per_order") is not None
                    else (c.get("cost") or 0)
                )
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

        # SSOT shipping-cost helper (single source for base/tax/total).
        from shipping_cost_ssot import get_company_configs, shipping_breakdown
        ssot_cfgs = await get_company_configs(db, uid)
        # Build a key-normalized lookup so we can resolve aliases too.
        ssot_cfgs_by_key: dict = {}
        for nm, cfg in ssot_cfgs.items():
            k, disp = normalize_shipping_company(nm)
            ssot_cfgs_by_key[k] = cfg
            ssot_cfgs_by_key[disp] = cfg
            ssot_cfgs_by_key[nm] = cfg

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
            "total_shipping_base": 0.0,
            "total_shipping_tax":  0.0,
            "total_shipping_cost": 0.0,   # = base + tax (SSOT)
            "total_cod": 0.0,
            "total_cod_fees": 0.0,
            "total_cod_fee_net": 0.0,
            "total_cod_fee_vat": 0.0,
            "cod_fee_rules_needing_review": 0,
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

            # SSOT — compute base / tax / total exactly once. The SSOT
            # helper applies the priority: company-config first, Salla
            # only as fallback. So we pass the raw Salla shipping_cost
            # and let SSOT resolve everything.
            ship_cost_from_order = float(o.get("shipping_cost") or 0)
            bd_order = {"shipping_company": comp_display,
                         "shipping_cost": ship_cost_from_order}
            bd_cfgs = {comp_display: ssot_cfgs_by_key.get(comp_key)
                                       or ssot_cfgs_by_key.get(comp_display)
                                       or {}}
            bd = shipping_breakdown(bd_order, bd_cfgs)
            ship_base = bd["base"]
            ship_tax = bd["tax"]
            ship_cost = bd["total"]    # base + tax (the unified figure)
            cost_source = bd["source"]  # company_config | salla | none
            # Audit-only diff vs Salla (does NOT affect any computation).
            # Positive = settings cheaper than Salla; negative = settings dearer.
            salla_ship_native = round(ship_cost_from_order, 2)
            if cost_source == "salla" or salla_ship_native == 0:
                diff_vs_salla = None      # nothing to compare against
            else:
                diff_vs_salla = round(ship_base - salla_ship_native, 2)
            cod_amt = float(o.get("cod_amount") or
                            (o.get("total_amount") or 0) if is_cod else 0)
            if not is_cod:
                cod_amt = 0.0
            explicit_cod_fee = float(o.get("cod_fee") or 0)
            fee_cfg = fee_map.get(comp_key) or {}
            has_tiers = bool(fee_cfg.get("cod_fee_tiers"))
            if cod_amt > 0 and has_tiers:
                fee_calc = calculate_courier_cod_fee(cod_amt, fee_cfg)
            elif cod_amt > 0 and explicit_cod_fee > 0:
                # Preserve an official per-order value when no tier contract
                # is configured. Its VAT split is unknown, so keep it whole.
                fee_calc = {
                    "fee_net": explicit_cod_fee,
                    "fee_vat": 0.0,
                    "fee_total": explicit_cod_fee,
                    "source": "order_explicit",
                    "needs_review": False,
                    "matched_rule": None,
                }
            elif cod_amt > 0:
                fee_calc = calculate_courier_cod_fee(cod_amt, fee_cfg)
            else:
                fee_calc = {
                    "fee_net": 0.0, "fee_vat": 0.0, "fee_total": 0.0,
                    "source": "not_cod", "needs_review": False,
                    "matched_rule": None,
                }
            cod_fee_net = round(float(fee_calc["fee_net"]), 2)
            cod_fee_vat = round(float(fee_calc["fee_vat"]), 2)
            cod_fee = round(float(fee_calc["fee_total"]), 2)

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
                # SSOT — base + tax + total fields exposed for the new UI
                "shipping_base":  round(ship_base, 2),
                "shipping_tax":   round(ship_tax, 2),
                "shipping_cost":  round(ship_cost, 2),    # = base + tax
                "shipping_vat_rate": bd["vat_rate"],
                # Audit-only fields (never used in totals/balances/GL).
                "salla_shipping_native": salla_ship_native,
                "diff_vs_salla":         diff_vs_salla,
                # Iter-235 — provenance flag for the new UI column.
                "shipping_cost_source": cost_source,
                "prepaid_shipping": mode == "prepaid",
                "cod_amount": round(cod_amt, 2),
                "cod_fee": round(cod_fee, 2),
                "cod_fee_net": cod_fee_net,
                "cod_fee_vat": cod_fee_vat,
                "cod_fee_source": fee_calc.get("source"),
                "cod_fee_rule_needs_review": bool(fee_calc.get("needs_review")),
                "cod_fee_rule": fee_calc.get("matched_rule"),
                "net_due": net_due,
                "settlement_status": sstatus,
                "settlement_type": "—",
            })
            totals["delivered_count"] += 1
            totals["total_shipping_base"] += ship_base
            totals["total_shipping_tax"]  += ship_tax
            totals["total_shipping_cost"] += ship_cost
            totals["total_cod"] += cod_amt
            totals["total_cod_fees"] += cod_fee
            totals["total_cod_fee_net"] += cod_fee_net
            totals["total_cod_fee_vat"] += cod_fee_vat
            if fee_calc.get("needs_review"):
                totals["cod_fee_rules_needing_review"] += 1
            totals["total_unsettled"] += net_due
            if mode == "prepaid":
                totals["total_prepaid_shipping"] += ship_cost
            else:
                totals["total_deferred_shipping"] += ship_cost

        for k in totals:
            if isinstance(totals[k], float):
                totals[k] = round(totals[k], 2)

        # Per-company breakdown + warning detection.
        by_company: dict[str, dict] = {}
        for r in rows:
            name = r["shipping_company"]
            entry = by_company.setdefault(name, {
                "shipping_company": name,
                "orders_count": 0,
                "total_shipping_base": 0.0,
                "total_shipping_tax":  0.0,
                "total_shipping_cost": 0.0,     # = base + tax (SSOT)
                "from_salla_count": 0,
                "from_company_settings_count": 0,
                "none_count": 0,
                "configured_cost": cost_map.get(
                    r["shipping_company_key"]) or cost_map.get(
                    name.lower()) or 0.0,
                "vat_rate": r.get("shipping_vat_rate", 0.0),
                "payment_mode": r["payment_mode"],
            })
            entry["orders_count"] += 1
            entry["total_shipping_base"] += r["shipping_base"]
            entry["total_shipping_tax"]  += r["shipping_tax"]
            entry["total_shipping_cost"] += r["shipping_cost"]
            src = r.get("shipping_cost_source") or "none"
            if src == "salla":
                entry["from_salla_count"] += 1
            elif src == "company_config":
                entry["from_company_settings_count"] += 1
            else:
                entry["none_count"] += 1
        # Round + add per-unit averages + warning flag.
        per_company = []
        warnings: list[dict] = []     # surfaced at top of UI
        for v in by_company.values():
            oc = max(v["orders_count"], 1)
            v["total_shipping_base"] = round(v["total_shipping_base"], 2)
            v["total_shipping_tax"]  = round(v["total_shipping_tax"], 2)
            v["total_shipping_cost"] = round(v["total_shipping_cost"], 2)
            v["cost_per_unit"]  = round(v["total_shipping_base"] / oc, 2)
            v["tax_per_unit"]   = round(v["total_shipping_tax"]  / oc, 2)
            v["total_per_unit"] = round(v["total_shipping_cost"] / oc, 2)
            v["effective_cost_per_order"] = v["total_per_unit"]
            # Warning: this company has rows priced from Salla because
            # no `cost_per_order` is configured in /shipping/settings.
            uses_salla = (v["from_salla_count"] > 0
                          and v["configured_cost"] <= 0)
            v["uses_salla_fallback"] = uses_salla
            if uses_salla:
                warnings.append({
                    "shipping_company": v["shipping_company"],
                    "orders_affected":  v["from_salla_count"],
                    "reason":           "missing_cost_in_settings",
                    "message":          (
                        f"شركة الشحن «{v['shipping_company']}» لا يوجد لها "
                        "سعر في إعدادات شركات الشحن، لذلك تم الاعتماد مؤقتاً "
                        "على سعر الشحن القادم من سلة. يرجى إضافتها في إعدادات "
                        "الشحن مع السعر الصحيح."
                    ),
                })
            per_company.append(v)
        per_company.sort(key=lambda x: x["total_shipping_cost"],
                         reverse=True)

        rows.sort(key=lambda r: r["order_date"] or "", reverse=True)
        return {"rows": rows, "totals": totals,
                "per_company": per_company,
                "warnings": warnings,
                "delivered_only": True, "limit": limit}

    parent_router.include_router(router)
