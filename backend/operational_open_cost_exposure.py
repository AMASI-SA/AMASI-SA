"""Read-only open-work cost exposure; never imports historical orders into finance.

Post-cutover amounts come exclusively from the reconciled operational engine.
Pre-cutover unfinished work is an inspection-only preview, not a payable,
opening balance, invoice, ledger, shipping delivery or stock movement.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from operational_balance_sources import (
    append_components, cod_facts, cost_components, instant, number, rows,
    invoice_component_amounts, shipping_charge, supplier_receipt_proven,
    unique_index, usable,
)

ZERO = Decimal("0")
CLOSED = frozenset(("delivered", "تم التوصيل", "cancelled", "canceled",
                     "ملغي", "ملغى", "refunded", "مسترجع", "returned"))
OPEN = frozenset(("under_review", "waiting_review", "waiting_for_review", "pending_review",
                  "pending", "waiting", "in_progress", "processing",
                  "reviewed", "ready", "completed", "shipping", "shipped",
                  "delivering", "out_for_delivery",
                  "انتظار المراجعة", "بإنتظار المراجعة", "بانتظار المراجعة",
                  "تم المراجعة", "تمت المراجعة", "قيد التنفيذ", "جاري التنفيذ",
                  "تم التنفيذ", "جاري التوصيل", "تم الشحن"))
RECEIVED = frozenset(("received", "supplier_received", "تم الاستلام", "مستلم"))
READS = ("unified_orders", "mezan_product_cost_profiles_v2", "mezan_products_v2",
         "mezan_product_resource_bindings_v2", "mezan_product_option_cost_bindings_v2",
         "mezan_cost_resources_v2", "mezan_preparation_pieces_v1",
         "mezan_supplier_invoices_v2", "mezan_suppliers_v2", "store_drivers",
         "store_delivery_assignments", "mz2_shipping_setup_v2")


def _fmt(value):
    return str(value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def _status(raw):
    source = raw.get("status")
    if isinstance(source, dict):
        source = source.get("slug") or source.get("name")
    return str(source or "").strip().lower()


def _cutover_group(status):
    if status in {"under_review", "waiting_review", "waiting_for_review", "pending_review",
                  "pending", "waiting", "انتظار المراجعة",
                  "بانتظار المراجعة", "بإنتظار المراجعة"}:
        return "awaiting_review"
    return "under_fulfillment"


def _money_group(values, key, amount):
    values[key] += amount


async def project(db, owner, state, *, as_of=None):
    """Bounded, owner-scoped read; no financial writes, silent currency conversion or fallbacks."""
    if state.get("status") not in {"active", "frozen"} or not state.get("started_at"):
        return {"status": "not_started", "groups": [], "currencies": {}, "issues": []}
    started = instant(state["started_at"])
    stamp = instant(as_of or datetime.now(timezone.utc))
    if stamp < started:
        raise ValueError("operational_work_forecast_time_invalid")
    data = {name: await rows(db, owner, name) for name in READS}
    issues = Counter()
    values = defaultdict(lambda: {"amount": ZERO, "units": 0, "orders": set()})
    currencies = defaultdict(lambda: defaultdict(lambda: ZERO))
    profiles = unique_index(data["mezan_product_cost_profiles_v2"], "salla_product_id")
    catalogue = unique_index(data["mezan_products_v2"], "salla_product_id")
    suppliers = unique_index(data["mezan_suppliers_v2"], "id")
    drivers = unique_index(data["store_drivers"], "id")
    setups = data["mz2_shipping_setup_v2"]
    setup = setups[0] if len(setups) == 1 else {}
    if len(setups) > 1:
        issues["shipping_setup_ambiguous"] += 1
    native_pieces = [r for r in data["mezan_preparation_pieces_v1"]
                     if not r.get("experiment_mode") and not r.get("experiment_run_id")]
    pieces_by_item = defaultdict(list)
    for piece in native_pieces:
        pieces_by_item[(str(piece.get("order_number")), str(piece.get("order_item_id")))].append(piece)

    # Only native approved invoice component identities establish coverage.
    # An invoice approved before start may already be in the owner's opening
    # balance; never add it to an estimated supplier payable again.
    coverage = defaultdict(set)
    approved_after_start = defaultdict(set)
    invoices = [r for r in data["mezan_supplier_invoices_v2"]
                if not r.get("experiment_mode") and not r.get("experiment_run_id")
                and r.get("approved_at") and r.get("supplier_id") in suppliers]
    counts = Counter(str(r.get("id") or "") for r in invoices)
    for invoice in invoices:
        if not invoice.get("id") or counts[str(invoice["id"])] != 1:
            issues["supplier_invoice_identity_ambiguous"] += 1
            continue
        try:
            approved_at = instant(invoice["approved_at"])
        except (ValueError, TypeError):
            issues["supplier_invoice_date_invalid"] += 1
            continue
        if approved_at > stamp:
            continue
        try:
            invoice_component_amounts(invoice)
        except (ValueError, KeyError, TypeError):
            issues["supplier_invoice_tax_or_amount_incomplete"] += 1
            continue
        for line in invoice.get("lines", []):
            piece_ids = line.get("piece_ids") or []
            if (line.get("product_price_authority") != "mezan_v2" or not piece_ids
                    or len(piece_ids) != len(set(piece_ids))):
                issues["supplier_invoice_component_incomplete"] += 1
                continue
            for pid in piece_ids:
                if line.get("product_charge_eligible") is not False:
                    key = str(pid)
                    coverage[key].add(str(invoice["id"]))
                    if approved_at >= started:
                        approved_after_start[key].add(str(invoice["id"]))
                for service in line.get("services") or []:
                    if not service.get("service_id"):
                        issues["supplier_invoice_component_incomplete"] += 1
                        continue
                    key = str(pid) + ":service:" + str(service["service_id"])
                    coverage[key].add(str(invoice["id"]))
                    if approved_at >= started:
                        approved_after_start[key].add(str(invoice["id"]))

    assignments = defaultdict(list)
    for entry in data["store_delivery_assignments"]:
        if entry.get("status") in {"assigned", "out_for_delivery", "delivered"}:
            assignments[str(entry.get("order_number"))].append(entry)

    source_orders = []
    canonical_ids = Counter()
    canonical_numbers = Counter()
    for record in data["unified_orders"]:
        raw = (record.get("raw_by_source") or {}).get("salla_direct")
        if not isinstance(raw, dict):
            issues["canonical_salla_source_missing_for_open_cost_preview"] += 1
            continue
        try:
            created = instant(raw.get("date") or raw.get("created_at") or raw.get("order_date"))
        except (ValueError, TypeError):
            issues["order_created_at_incomplete"] += 1
            continue
        if created > started or created > stamp:
            continue
        status = _status(raw)
        if status in CLOSED:
            continue
        if status not in OPEN:
            issues["historical_order_status_unverified"] += 1
            continue
        oid = str(raw.get("id") or "")
        num = str(raw.get("reference_id") or raw.get("order_number") or "")
        canonical_ids[oid] += 1
        canonical_numbers[num] += 1
        source_orders.append((record, raw, oid, num, status))
    # Duplicate current source identities are not an excuse to duplicate costs.
    for record, raw, oid, num, status in source_orders:
        if not oid or not num or canonical_ids[oid] != 1 or canonical_numbers[num] != 1:
            issues["historical_order_identity_ambiguous"] += 1
            continue
        if (record.get("g47_salla_snapshot") or {}).get("requires_authoritative_refresh"):
            issues["historical_order_refresh_required"] += 1
            continue
        source_total = (raw.get("amounts") or {}).get("total")
        total_currency = source_total.get("currency") if isinstance(source_total, dict) else None
        currency = str(raw.get("currency") or total_currency or "")
        # The MZ2 cost profiles and delivery contracts are SAR amounts; the
        # historic Salla order currency is not permission to invent FX.
        if currency != "SAR":
            issues["historical_cost_currency_requires_review"] += 1
            continue
        stage = _cutover_group(status)
        items = raw.get("items") or []
        if not isinstance(items, list):
            issues["historical_items_incomplete"] += 1
            continue
        seen_lines = set()
        for item in items:
            line_id = str(item.get("id") or item.get("item_id") or item.get("order_item_id") or "")
            if not line_id or line_id in seen_lines:
                issues["historical_order_item_identity_ambiguous"] += 1
                continue
            seen_lines.add(line_id)
            stable = "salla:" + num + ":" + line_id
            product = item.get("product") or {}
            product_id = str(item.get("parent_product_id") or item.get("product_id") or product.get("id") or "")
            try:
                profile = profiles.get(product_id, {})
                variant = str(item.get("variant_id") or "")
                base_available = (profile.get("base_cost") is not None or
                                  variant and variant in (profile.get("variant_costs") or {}))
                if not base_available:
                    # Current Salla catalog cost is allowed only as a clearly
                    # flagged historical forecast; never as payable evidence.
                    catalog = catalogue.get(product_id)
                    if not catalog:
                        raise ValueError("historical_catalog_cost_missing")
                    if variant:
                        matches = [v for v in catalog.get("variants", [])
                                   if isinstance(v, dict) and str(v.get("id") or "") == variant]
                        if len(matches) != 1:
                            raise ValueError("historical_variant_catalog_cost_ambiguous")
                        raw_price = matches[0].get("cost_price_from_salla")
                    else:
                        raw_price = catalog.get("cost_price_from_salla")
                    fallback = number(raw_price)
                    if fallback <= 0:
                        raise ValueError("historical_catalog_cost_missing")
                    profile = dict(profile, base_cost=str(fallback))
                    issues["historical_current_catalog_estimate_used"] += 1
                per_unit, services = cost_components(data, product_id, item, profile)
                quantity = number(item.get("quantity"))
                if quantity <= 0 or quantity != quantity.to_integral_value():
                    raise ValueError("historical_quantity_invalid")
                projected = []
                pieces = pieces_by_item.get((num, stable), [])
                if pieces:
                    if len(pieces) != quantity or len({p.get("unit_index") for p in pieces}) != len(pieces):
                        raise ValueError("historical_piece_coverage_incomplete")
                    for piece in pieces:
                        if piece.get("supplier_id") and str(piece["supplier_id"]) not in suppliers:
                            raise ValueError("historical_supplier_identity_incomplete")
                        order = {"items": []}
                        append_components(order, str(piece.get("id") or ""), per_unit, services, 1,
                                          piece.get("supplier_id"), piece)
                        projected.extend((component, piece) for component in order["items"])
                else:
                    order = {"items": []}
                    append_components(order, stable, per_unit, services, quantity)
                    projected.extend((component, None) for component in order["items"])
                for component, piece in projected:
                    component_key = str(component["id"])
                    if not component_key or len(coverage[component_key]) > 1:
                        issues["historical_supplier_component_ambiguous"] += 1
                        continue
                    if coverage[component_key]:
                        if approved_after_start[component_key]:
                            issues["prestart_order_postcutover_invoice_needs_reconciliation"] += 1
                        continue
                    amount = number(component["cost"]) * number(component.get("quantity", "1"))
                    if amount != amount.quantize(Decimal(".01")):
                        raise ValueError("historical_cost_rounding_incomplete")
                    received = supplier_receipt_proven(piece)
                    if received:
                        try:
                            received = instant(piece["received_at"]) <= stamp
                        except (ValueError, TypeError, KeyError):
                            received = False
                            issues["historical_supplier_receipt_timestamp_invalid"] += 1
                    if received:
                        product_stage = "received_without_approved_invoice"
                        party_id = str(piece.get("supplier_id") or "")
                        party_name = ((suppliers.get(party_id) or {}).get("name")
                                      or "استلام دون مورد موثق")
                        if not party_id:
                            issues["historical_received_supplier_identity_missing"] += 1
                        _money_group(currencies["SAR"], "prestart_supplier_received_uninvoiced", amount)
                    else:
                        # Even if it has been assigned or sent, this remains one
                        # unreceived-cost pool, never a payable for that supplier.
                        product_stage = stage
                        party_name = "منتجات لم تُستلم من المورد"
                        _money_group(currencies["SAR"], "prestart_supplier_unreceived", amount)
                    key = ("prestart", "supplier", product_stage, party_name, "SAR")
                    group = values[key]
                    group["amount"] += amount
                    group["units"] += int(number(component.get("quantity", "1")))
                    group["orders"].add(oid)
                    _money_group(currencies["SAR"], "prestart_supplier_expected", amount)
            except (ValueError, TypeError, KeyError):
                issues["historical_supplier_cost_incomplete"] += 1

        # Shipping remains forecast, never a confirmed debt before delivery.
        shipping = raw.get("salla_shipping_current") or raw.get("shipping") or {}
        if not isinstance(shipping, dict):
            shipping = {}
        shipping_state = shipping.get("status")
        if isinstance(shipping_state, dict):
            shipping_state = shipping_state.get("slug") or shipping_state.get("name")
        if (shipping.get("delivered_at") or
                str(shipping_state or "").strip().lower() in {"delivered", "تم التوصيل"}):
            continue
        assigned = assignments.get(num, [])
        if len(assigned) > 1:
            issues["historical_shipping_assignment_ambiguous"] += 1
            continue
        if assigned and assigned[0].get("status") == "delivered":
            continue
        party_type = "courier"
        party_id = None
        party_name = ""
        if assigned:
            party_type = "store_driver"
            party_id = str(assigned[0].get("driver_id") or "")
            party_name = (drivers.get(party_id) or {}).get("name") or ""
            if not party_name:
                party_id = None
        else:
            carrier_key = shipping.get("company_code") or shipping.get("company")
            candidates = [c for c in setup.get("couriers", [])
                          if usable(c) and c.get("confirmed_by") and c.get("confirmed_at")
                          and carrier_key in (c.get("salla_carrier_keys") or [])]
            if len(candidates) == 1:
                party_id = str(candidates[0]["courier_key"])
                party_name = str(candidates[0].get("name") or party_id)
        if not party_id:
            issues["historical_shipping_unassigned"] += 1
            continue
        rates = []
        for rate in setup.get("contracts", []):
            if (rate.get("status") != "approved" or rate.get("party_type") != party_type
                    or str(rate.get("party_id")) != party_id or rate.get("context") != "delivery"):
                continue
            try:
                if instant(rate["effective_from"]) <= stamp and (not rate.get("effective_to") or stamp < instant(rate["effective_to"])):
                    rates.append(rate)
            except (ValueError, KeyError, TypeError):
                issues["historical_shipping_contract_incomplete"] += 1
        if len(rates) != 1:
            issues["historical_shipping_contract_incomplete"] += 1
            continue
        try:
            payment = raw.get("payment") if isinstance(raw.get("payment"), dict) else {}
            method = str(payment.get("method") or raw.get("payment_method") or "").lower()
            is_cod = method in {"cod", "cash_on_delivery", "cashondelivery", "cash on delivery",
                                "الدفع عند الاستلام", "دفع عند الاستلام", "دفع عند الإستلام"}
            due = None
            if is_cod:
                total = number((raw.get("amounts") or {}).get("total", raw.get("total")))
                due = number(cod_facts(raw, total)["outstanding"])
            result = shipping_charge(rates[0], owner, party_id, stamp, due, setup)
            if not result.get("fee_complete"):
                issues["historical_shipping_fee_component_incomplete"] += 1
                continue
            amount = number(result["cost"])
            if amount != amount.quantize(Decimal(".01")):
                raise ValueError("historical_shipping_rounding_incomplete")
            key = ("prestart", party_type, "not_delivered", party_name, "SAR")
            group = values[key]
            group["amount"] += amount
            group["units"] += 1
            group["orders"].add(oid)
            _money_group(currencies["SAR"], "prestart_shipping_expected", amount)
        except (ValueError, TypeError, KeyError):
            issues["historical_shipping_quote_incomplete"] += 1

    # For current orders, stage classification comes from canonical order state.
    # All amounts remain those of the existing idempotently reconciled engine.
    current_stages = {}
    current_count = Counter()
    engine_at = instant((state.get("engine") or {}).get("as_of") or stamp)
    for record in data["unified_orders"]:
        raw = (record.get("raw_by_source") or {}).get("salla_direct")
        if not isinstance(raw, dict):
            continue
        try:
            created = instant(raw.get("date") or raw.get("created_at") or raw.get("order_date"))
            if not started < created <= engine_at:
                continue
            identity = str(raw.get("id") or "")
            if identity:
                current_count[identity] += 1
                current_stages[identity] = _cutover_group(_status(raw))
        except (ValueError, TypeError):
            continue

    # Current active expenses use the frozen/reconciled engine; never re-estimate
    # those orders through this separate, historical-only projection.
    for obligation in (state.get("engine", {}).get("obligations") or {}).values():
        if obligation.get("superseded") or obligation.get("derived_credit"):
            continue
        kind = obligation.get("kind")
        if kind not in {"supplier", "shipping"}:
            continue
        currency = str(obligation.get("currency") or "")
        try:
            expected = number(obligation.get("expected", "0"))
            confirmed = number(obligation.get("confirmed", "0"))
        except ValueError:
            issues["operational_cost_obligation_incomplete"] += 1
            continue
        _money_group(currencies[currency], "current_" + kind + "_expected", expected)
        _money_group(currencies[currency], "current_" + kind + "_confirmed", confirmed)
        oid = str(obligation.get("id") or "").split(":", 2)[1:2]
        order_id = oid[0] if oid else ""
        if expected:
            if kind == "supplier" and not obligation.get("party_id"):
                stage = current_stages.get(order_id, "unreceived")
                if current_count[order_id] != 1:
                    stage = "unreceived"
                name = "منتجات لم تُستلم من المورد"
                _money_group(currencies[currency], "poststart_supplier_unreceived", expected)
            elif kind == "supplier":
                stage = "received_without_approved_invoice"
                name = str(obligation.get("name") or "مورد الاستلام")
                _money_group(currencies[currency], "poststart_supplier_received_uninvoiced", expected)
            else:
                stage = "not_delivered"
                name = str(obligation.get("name") or "جهة الشحن غير مكتملة")
            group = values[("poststart", kind, stage, name, currency)]
            group["amount"] += expected
            group["units"] += 1
            group["orders"].add(order_id or str(obligation.get("id")))
        if confirmed:
            name = str(obligation.get("name") or "جهة غير مكتملة")
            group = values[("poststart", kind, "confirmed_in_financial_balances", name, currency)]
            group["amount"] += confirmed
            group["units"] += 1
            group["orders"].add(order_id or str(obligation.get("id")))

    summaries = {currency: {key: _fmt(value) for key, value in sorted(details.items())}
                 for currency, details in sorted(currencies.items())}
    groups = [{"period": period, "kind": kind, "stage": stage, "party_name": party_name,
               "party_type": ("supplier" if kind == "supplier" else kind),
               "currency": currency, "amount": _fmt(value["amount"]),
               "units": value["units"], "orders": len(value["orders"])}
              for (period, kind, stage, party_name, currency), value in values.items()]
    groups.sort(key=lambda r: (r["period"], r["kind"], r["stage"], r["party_name"]))
    return {"status": "preview", "as_of": stamp.isoformat(), "engine_as_of": engine_at.isoformat(), "started_at": started.isoformat(),
            "currencies": summaries, "groups": groups,
            "issues": [{"code": code, "count": count} for code, count in sorted(issues.items())],
            "warning": "historical_preview_never_add_to_confirmed_or_expected_payable_without_owner_evidence"}
