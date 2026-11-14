"""Campaign-day margin from the same read-only cost authorities as reports.

One bounded cohort is priced once and reused across the visible campaign batch.
No provider calls, saved allocations, operating writes or per-order DB reads.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP

from order_currency import SALLA_RAW_CURRENCY_PROJECTION, salla_order_currency_fields
from report_option_recovery import attach_report_option_evidence_cooperative

MAX_COST_LINES = 12000
COST_TIMEOUT_SECONDS = 3
COST_ORDER_PROJECTION = {
    **SALLA_RAW_CURRENCY_PROJECTION,
    "raw_by_source.salla_direct.reference_id": 1, "raw_by_source.salla_direct.items": 1,
    **{key: 1 for key in ("products", "items", "payment_method", "shipping_company", "shipping_cost",
        "payment_fee_status", "actual_payment_fee", "actual_refund_amount", "actual_partial_refund_amount")},
}


def number(value):
    # Order Engine is imported by the cost authorities too. Keep domain imports
    # at request execution, after route/module initialization has completed.
    from marketing_report_costs import number as optional_number
    return optional_number(value)


def money(values):
    return float(sum((Decimal(str(v)) for v in values), Decimal(0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def blank_profit_fields():
    return {"product_cost_sar": None, "shipping_cost_sar": None, "payment_fees_sar": None,
        "order_payment_fees_sar": None, "ad_bank_fees_sar": None, "total_cost_sar": None,
        "known_cost_sar": None, "product_cost_complete": None, "costs_complete": False,
        "operating_expenses_sar": None, "operating_expenses_ratio": None,
        "operating_expenses_complete": False, "operating_basis": None,
        "net_profit_sar": None, "net_profit_available": False, "profit_provisional": False,
        "cost_reasons": [], "profit_scope": "campaign_margin_after_allocated_operating_expenses"}


class _ReadCollection:
    """Keep shared readers bounded without changing their canonical algorithms."""
    def __init__(self, collection):
        self.collection = collection

    def find(self, *args, **kwargs):
        return self.collection.find(*args, **kwargs).max_time_ms(1500)

    def aggregate(self, *args, **kwargs):
        return self.collection.aggregate(*args, **{**kwargs, "maxTimeMS": 1500})

    async def find_one(self, *args, **kwargs):
        return await self.collection.find_one(*args, **{**kwargs, "max_time_ms": 1500})


class _ReadDB:
    def __init__(self, db):
        self.db = db

    def __getitem__(self, name):
        return _ReadCollection(self.db[name])

    def __getattr__(self, name):
        return self[name]


def _current_lines(order):
    """Exact native line coverage, never a name/SKU join to current revenue."""
    from order_cost_authority import decimal, line_aliases, order_items
    from .mapper import _map_item
    native = ((order.get("raw_by_source") or {}).get("salla_direct") or {}).get("items")
    items = order_items(order)
    if not isinstance(native, list) or not native or len(native) != len(items):
        return None
    by_id = {}
    for raw in native:
        identity = raw.get("id") if isinstance(raw, dict) else None
        if not isinstance(identity, (str, int)) or isinstance(identity, bool) or not str(identity).strip() or str(identity) in by_id:
            return None
        by_id[str(identity)] = raw
    matched, seen = {}, set()
    for item in items:
        aliases = line_aliases(item)
        prefix = "salla:" + str(order["order_number"]) + ":"
        aliases |= {value.removeprefix(prefix) for value in aliases if value.startswith(prefix)}
        candidates = aliases.intersection(by_id)
        if len(candidates) != 1 or candidates.intersection(seen):
            return None
        identity = next(iter(candidates))
        raw = by_id[identity]
        quantity = decimal(raw.get("quantity"))
        if quantity is None or quantity <= 0 or quantity != decimal(item.get("quantity")):
            return None
        product = raw.get("product") if isinstance(raw.get("product"), dict) else {}
        source_products = {str(value) for value in (raw.get("product_id"), product.get("id")) if value not in (None, "")}
        projected_products = {str(item.get(key)) for key in ("product_id", "parent_product_id", "salla_product_id") if item.get(key) not in (None, "")}
        if len(source_products) != 1 or not source_products.issubset(projected_products):
            return None
        variant = raw.get("variant") if isinstance(raw.get("variant"), dict) else {}
        product_variant = product.get("variant") if isinstance(product.get("variant"), dict) else {}
        variants = {str(value).strip() for value in (variant.get("id"), product_variant.get("id"),
            raw.get("variant_id"), raw.get("product_sku_id"), product.get("variant_id")) if value not in (None, "")}
        source = _map_item(order_number=str(order["order_number"]), item=raw, index=0).model_dump()
        parents = {str(value).strip() for value in (raw.get("parent_product_id"), product.get("parent_id"))
            if value not in (None, "")}
        canonical_product, canonical_parent = source.get("product_id") or "", source.get("parent_product_id") or ""
        if (len(parents) > 1 or parents and parents != {canonical_parent}
                or canonical_product != str(item.get("product_id") or item.get("salla_product_id") or "").strip()
                or canonical_parent != str(item.get("parent_product_id") or "").strip()
                or str(item.get("salla_product_id") or "").strip() not in {"", canonical_product, canonical_parent}):
            return None
        canonical_variant = source.get("variant_id") or ""
        if (len(variants) > 1 or variants and variants != {canonical_variant}
                or canonical_variant != str(item.get("variant_id") or "").strip()):
            return None
        seen.add(identity)
        matched[id(item)] = source
    return matched


def _current_choices_match(item, source, product, bindings, *, composite):
    from order_cost_authority import economic_item
    from report_option_recovery import report_cost_item
    from report_option_identity import resolve_report_choices
    current = economic_item(report_cost_item(item))
    if resolve_report_choices(current, product, bindings)[0] != resolve_report_choices(economic_item(source), product, bindings)[0]:
        return False
    if composite:
        from current_bundle_reporting_estimate import _choice_pairs
        try:
            return _choice_pairs(current, product or {}) == _choice_pairs(source, product or {})
        except ValueError:
            return False
    return True


async def _price_orders(db, owner, source_orders):
    from dashboard_cpu_budget import CPUWorkBudget, COST_PROFIT_BUDGET_MS
    from order_cost_authority import calculate_catalog_line_cost, load_order_cost_authority, order_items, resolve_order_line_cost
    from order_cost_reporting_repair import _catalog_context
    from order_profitability_summary import single_order_costs
    from order_status_policy import default_category_for
    from product_catalog_cost_resolution import NAME_ALIAS_PREFIX, resolve_current_catalog_line_product
    if not source_orders:
        return {}
    budget = CPUWorkBudget(COST_PROFIT_BUDGET_MS)
    line_count = 0
    # Admission precedes copying. A synchronous over-limit prefix cannot be
    # interrupted by wait_for, so yield while checking the complete cohort.
    for source in source_orders:
        await budget.checkpoint()
        promoted = source.get("products") or source.get("items") or []
        native = ((source.get("raw_by_source") or {}).get("salla_direct") or {}).get("items") or []
        if not isinstance(promoted, list) or not isinstance(native, list) or max(len(promoted), len(native)) > 1000:
            raise ValueError("campaign_cost_line_limit")
        line_count += max(len(promoted), len(native))
        if line_count > MAX_COST_LINES:
            raise ValueError("campaign_cost_line_limit")
    orders = []
    for source in source_orders:
        await budget.checkpoint()
        row = deepcopy(source)
        raw = (row.get("raw_by_source") or {}).get("salla_direct") or {}
        currency = salla_order_currency_fields(raw)
        # Percentage fees and foreign shipping use the exact raw sale counted
        # by this card, never an older promoted SAR amount from the order DTO.
        row.update(currency, total_amount=currency["original_total_amount"], currency=currency["original_currency"])
        orders.append(row)
    await attach_report_option_evidence_cooperative(orders, orders, checkpoint=budget.checkpoint)
    # Shared inputs are loaded once for the distinct cohort, not per campaign.
    context = await _catalog_context(db, owner, orders)
    authority = await load_order_cost_authority(db, owner, orders, budget=budget, current_profitability_estimate=True)
    settings = await db["settings"].find_one({"user_id": owner},
        {"_id": 0, "payment_methods": 1, "shipping_companies": 1}) or {}
    configs = {}
    for config in settings.get("shipping_companies") or []:
        name = str(config.get("name") or "").strip().strip("'\"")
        if name:
            configs[name] = configs[name.lower()] = config
    by_id, by_variant, by_sku = context["indexes"]
    # A name unique within the bounded subset is not proven tenant-unique.
    by_sku = {key: value for key, value in by_sku.items() if not key.startswith(NAME_ALIAS_PREFIX)}
    result = {}
    for order in orders:
        await budget.checkpoint()
        lines = []
        reasons = set()
        current_lines = _current_lines(order)
        source_complete = current_lines is not None
        if not source_complete:
            reasons.add("current_order_lines_unverified")
        for item in order_items(order):
            await budget.checkpoint()
            product = resolve_current_catalog_line_product(item, products_by_id=by_id,
                products_by_variant=by_variant, products_by_sku=by_sku)
            product_id = str((product or {}).get("salla_product_id") or "")
            bindings = context["option_bindings"].get(product_id, [])
            if current_lines is not None and not _current_choices_match(item, current_lines[id(item)], product, bindings,
                    composite=product_id in authority.get("composite_products", set())):
                source_complete = False
                reasons.add("current_order_choices_unverified")
            catalog = calculate_catalog_line_cost(item, product=product, profile=context["profiles"].get(product_id),
                product_bindings=context["product_bindings"].get(product_id, []),
                option_bindings=bindings, resources=context["resources"], current_report=True)
            cost = resolve_order_line_cost(item, order_number=str(order["order_number"]),
                catalog_cost=catalog, authority=authority, product_id=product_id)
            lines.append(cost)
            reasons.update(cost.get("missing_cost_reasons") or [])
        # The executive amount gate permits a valid Salla cost fallback. It
        # rejects unresolved base/components/options, not just missing Mezan data.
        products_complete = source_complete and bool(lines) and all(line.get("base_complete") is True
            and number(line.get("line_total")) is not None for line in lines)
        raw_items = order.get("products") or order.get("items") or []
        products_complete = products_complete and len(lines) == len(raw_items)
        if not products_complete:
            reasons.add("product_cost_incomplete")
        adjusted = default_category_for(order.get("order_status")) in {"cancelled", "refunded"} or any(
            (number(order.get(field)) or 0) > 0 for field in ("actual_refund_amount", "actual_partial_refund_amount"))
        # Existing campaign commerce is gross. Reducing costs alone for an
        # unresolved refund would overstate margin, so preserve the unknown.
        if adjusted:
            reasons.add("refund_cost_reconciliation_required")
        costs = single_order_costs(order, settings, configs)
        shipping, fees = number(costs.get("shipping_cost_sar")), number(costs.get("payment_fees_sar"))
        if shipping is None or shipping < 0:
            shipping = None
            reasons.add("shipping_cost_missing")
        if fees is None or fees < 0:
            fees = None
            reasons.add("payment_fees_missing")
        result[str(order["order_number"])] = {
            "known_product_cost_sar": money(number(line.get("line_total")) or 0 for line in lines),
            "product_cost_complete": products_complete,
            "shipping_cost_sar": shipping, "order_payment_fees_sar": fees,
            "complete": products_complete and shipping is not None and fees is not None and not adjusted,
            "reasons": reasons,
        }
    return result


async def _ad_settings(db, owner):
    from ads_manager.account_cost_settings import COLLECTION as AD_COST_SETTINGS
    rows = await db[AD_COST_SETTINGS].find({"user_id": owner}, {"_id": 0}).limit(251).to_list(length=251)
    if len(rows) > 250:
        raise ValueError("campaign_ad_settings_limit")
    return rows


def _ad_bank_fee(record, settings):
    from dashboard_v2_ad_costs import apply_cost_settings_to_fact_rows
    if settings is None or not record.get("account_id") or not record.get("provider"):
        return None
    provider = str(record["provider"]) + "_ads"
    account_id = str(record["account_id"]).removeprefix("act_")
    # Hover account identity is normalized. Normalize a copied fee scope too;
    # contradictory saved aliases must not silently choose the zero default.
    matching = [setting for setting in settings if setting.get("provider") == provider
        and str(setting.get("external_account_id") or "").removeprefix("act_") == account_id]
    if len(matching) > 1:
        return None
    scoped_settings = [{**setting, "external_account_id": account_id} for setting in matching]
    amount = number(record.get("spend_sar"))
    # The shared pure helper supplies the executive's configured/default fee
    # policy. A zero placeholder only inspects that policy for unknown spend;
    # it never certifies the absent spend as zero.
    source = {"ad_account_id": account_id, "spend_sar": amount if amount is not None else 0}
    result = apply_cost_settings_to_fact_rows({provider: [source]}, [], scoped_settings)
    account = result["accounts"][0]
    if not account["apply_bank_commission"] or account["bank_commission_pct"] == 0:
        return 0.0
    return result["total_fee_sar"] if amount is not None else None


async def _bounded(awaitable):
    try:
        return await asyncio.wait_for(awaitable, timeout=COST_TIMEOUT_SECONDS)
    except Exception:
        # Cost availability must not erase independently valid commerce/spend.
        return None


async def enrich_campaign_profit(db, owner, records, cohorts, now):
    from .campaign_hover_operating import read_operating_basis
    eligible = {cid: record for cid, record in records.items() if record.get("orders") is not None}
    if not eligible:
        return
    distinct = {str(order["order_number"]): order for cid in eligible for order in cohorts[cid]}
    readonly = _ReadDB(db)
    priced, settings, basis = await asyncio.gather(
        _bounded(_price_orders(readonly, owner, list(distinct.values()))),
        _bounded(_ad_settings(readonly, owner)),
        _bounded(read_operating_basis(db, owner, now)),
    )
    for cid, record in eligible.items():
        selected = [priced.get(str(order["order_number"])) for order in cohorts[cid]] if priced is not None else None
        if selected is not None and any(row is None for row in selected):
            selected = None
        reasons = set()
        bank_fee = _ad_bank_fee(record, settings)
        if bank_fee is None:
            reasons.add("ad_bank_fee_unavailable")
        if selected is None:
            reasons.add("cost_read_unavailable")
        else:
            for row in selected:
                reasons.update(row["reasons"])
            products_complete = all(row["product_cost_complete"] for row in selected)
            product_total = money(row["known_product_cost_sar"] for row in selected)
            shipping = money(row["shipping_cost_sar"] for row in selected) if all(row["shipping_cost_sar"] is not None for row in selected) else None
            order_fees = money(row["order_payment_fees_sar"] for row in selected) if all(row["order_payment_fees_sar"] is not None for row in selected) else None
            fees = money([order_fees, bank_fee]) if order_fees is not None and bank_fee is not None else None
            complete = all(row["complete"] for row in selected) and fees is not None
            record.update(product_cost_complete=products_complete,
                product_cost_sar=product_total if products_complete else None,
                shipping_cost_sar=shipping, order_payment_fees_sar=order_fees, payment_fees_sar=fees,
                ad_bank_fees_sar=bank_fee, costs_complete=complete,
                known_cost_sar=money([product_total, bank_fee or 0, *[row["shipping_cost_sar"] or 0 for row in selected],
                    *[row["order_payment_fees_sar"] or 0 for row in selected]]),
                total_cost_sar=money([product_total, shipping, fees]) if complete else None)
        sales = number(record.get("sales_sar"))
        ratio = number((basis or {}).get("ratio"))
        operating_complete = bool((basis or {}).get("complete") is True and ratio is not None and ratio >= 0 and sales is not None)
        operating = money([Decimal(str(sales)) * Decimal(str(ratio))]) if operating_complete else None
        if not operating_complete:
            reasons.add("operating_basis_incomplete")
        record.update(operating_basis=basis, operating_expenses_ratio=ratio if (basis or {}).get("complete") is True else None,
            operating_expenses_sar=operating, operating_expenses_complete=operating_complete)
        spend = number(record.get("spend_sar"))
        available = record["costs_complete"] and operating_complete and sales is not None and spend is not None
        record.update(net_profit_sar=money([sales, -spend, -record["total_cost_sar"], -operating]) if available else None,
            net_profit_available=bool(available), profit_provisional=bool(available and record.get("spend_warning")),
            cost_reasons=sorted(reasons))
