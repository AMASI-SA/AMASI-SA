"""Single Source of Truth — Shipping cost breakdown.

ONE place that calculates how much a shipment costs the merchant:

    total = base + tax

where:
  • base     = order.shipping_cost (per-order) if positive,
                else company_config.cost_per_order (the merchant's
                pre-negotiated unit cost on the shipping company doc).
  • vat_rate = company_config.vat_rate (decimal, e.g. 0.15 for 15%).
                Default 0.0 if not configured.
  • tax      = base * vat_rate
  • total    = base + tax

Historical / accounting invariant
=================================
Posted journal entries (general_ledger rows) are NEVER mutated by this
module — it is a pure calculator for live reports and live ledger views
(deferred-shipping balance, executive summary, FP, P&L…). When the
merchant changes a company's VAT% later, only LIVE reports recompute;
already-posted journal entries keep their original numbers.

Every report module that reads shipping cost MUST go through:

    from shipping_cost_ssot import shipping_breakdown, get_company_configs
"""
from __future__ import annotations


def shipping_breakdown(order: dict, company_cfgs: dict) -> dict:
    """Return the cost split for ONE order.

    Priority (per merchant accounting policy):
      1. Company-config `cost_per_order` (or legacy `cost`) — the
         pre-negotiated rate the merchant maintains in
         /shipping/settings.
      2. Only if the company is unknown OR has no cost configured
         → fall back to the order-level shipping_cost from Salla
         (temporary; the UI surfaces a warning so the merchant adds it).

    Returns: {base, tax, total, vat_rate, source}
      source ∈ {"company_config", "salla", "none"}.
    """
    company = (order.get("shipping_company") or "").strip()
    cfg = (
        company_cfgs.get(company)
        or company_cfgs.get(company.lower())
        or company_cfgs.get(company.strip("'\""))
        or company_cfgs.get(company.strip("'\"").lower())
        or {}
    )

    # Try company-config first
    cfg_cost = cfg.get("cost_per_order")
    if cfg_cost is None:
        cfg_cost = cfg.get("cost")
    try:
        cfg_base = float(cfg_cost or 0)
    except (TypeError, ValueError):
        cfg_base = 0.0

    if cfg_base > 0:
        base = cfg_base
        source = "company_config"
    else:
        # Fall back to Salla's per-order shipping_cost
        try:
            salla_ship = float(order.get("shipping_cost") or 0)
        except (TypeError, ValueError):
            salla_ship = 0.0
        if salla_ship > 0:
            base = salla_ship
            source = "salla"
        else:
            base = 0.0
            source = "none"

    try:
        # Accept either decimal (vat_rate: 0.15) OR percentage
        # (vat_percent: 15.0). The settings page stores vat_percent
        # for shipping companies, so we coerce both into a decimal.
        raw_rate = cfg.get("vat_rate")
        raw_pct = cfg.get("vat_percent")
        if raw_rate is not None and raw_rate != "":
            vat_rate = float(raw_rate)
        elif raw_pct is not None and raw_pct != "":
            vat_rate = float(raw_pct) / 100.0
        else:
            vat_rate = 0.0
    except (TypeError, ValueError):
        vat_rate = 0.0

    tax = round(base * vat_rate, 2)
    total = round(base + tax, 2)

    return {
        "base":     round(base, 2),
        "tax":      tax,
        "total":    total,
        "vat_rate": vat_rate,
        "source":   source,
    }


async def get_company_configs(db, user_id: str) -> dict:
    """Fetch every shipping_company config for a user, keyed by name.

    Reads from the canonical location: `settings.shipping_companies[]`
    (the array maintained by the `/shipping/settings` page). Empty
    entries (no name) are skipped. Both the raw name and its lowercase
    variant are added as keys to make lookup resilient to casing.
    """
    cfgs: dict[str, dict] = {}
    s = await db.settings.find_one(
        {"user_id": user_id},
        {"_id": 0, "shipping_companies": 1},
    ) or {}
    for c in s.get("shipping_companies") or []:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        # Strip any single-quote wrapping (some seeds store names like
        # "'مندوب الرياض'" — caused by JSON-quoted strings).
        cleaned = name.strip("'\"")
        cfgs[cleaned] = c
        cfgs[cleaned.lower()] = c
        if cleaned != name:
            cfgs[name] = c
            cfgs[name.lower()] = c
    return cfgs


def aggregate_breakdown(orders: list[dict], company_cfgs: dict) -> dict:
    """Sum breakdown across a list of orders. Useful for executive
    summary cards and exports.

    Returns: {total_base, total_tax, total_with_tax, orders_count,
              per_company: {name: {orders_count, base, tax, total,
              cost_per_unit, tax_per_unit, total_per_unit, vat_rate}}}
    """
    out = {
        "total_base":      0.0,
        "total_tax":       0.0,
        "total_with_tax":  0.0,
        "orders_count":    0,
        "per_company":     {},
    }
    for o in orders:
        bd = shipping_breakdown(o, company_cfgs)
        company = (o.get("shipping_company") or "—").strip() or "—"
        out["total_base"] += bd["base"]
        out["total_tax"] += bd["tax"]
        out["total_with_tax"] += bd["total"]
        out["orders_count"] += 1
        pc = out["per_company"].setdefault(company, {
            "name": company, "orders_count": 0,
            "base": 0.0, "tax": 0.0, "total": 0.0,
            "vat_rate": bd["vat_rate"],
        })
        pc["orders_count"] += 1
        pc["base"] += bd["base"]
        pc["tax"] += bd["tax"]
        pc["total"] += bd["total"]

    out["total_base"] = round(out["total_base"], 2)
    out["total_tax"] = round(out["total_tax"], 2)
    out["total_with_tax"] = round(out["total_with_tax"], 2)
    for pc in out["per_company"].values():
        oc = pc["orders_count"] or 1
        pc["base"] = round(pc["base"], 2)
        pc["tax"] = round(pc["tax"], 2)
        pc["total"] = round(pc["total"], 2)
        pc["cost_per_unit"] = round(pc["base"] / oc, 2)
        pc["tax_per_unit"] = round(pc["tax"] / oc, 2)
        pc["total_per_unit"] = round(pc["total"] / oc, 2)
    return out
