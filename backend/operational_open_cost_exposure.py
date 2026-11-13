"""Read-only open-work cost exposure, independent of financial recognition.

Historical estimates never become opening balances, invoices, bank movements or
payables. Current amounts come from the reconciled operational engine only.
"""
from operational_financial_activity_runtime import preparation_checkpoint, prepare_in_thread
from dashboard_cpu_budget import CPUWorkBudget, PARSER_BUDGET_MS
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import re

from operational_cost_completeness import cost_completeness
from operational_prestart_cod_forecast import prestart_open_cod
from operational_open_customer_cod import open_customer_cod
from operational_courier_tariffs import matches as matches_courier_key, with_tariffs
from operational_product_cost_policy import unreceived_eligible
from operational_remaining_bundle_sources import load_remaining_bundles
from operational_read_runtime import SourceReadError, SnapshotReadCache, load_collections, source_read_budget

from operational_balance_sources import (
    append_components, cod_facts, cost_components, instant, number, rows,
    invoice_component_amounts, shipping_charge, supplier_receipt_proven,
    unique_index, usable,
)

ZERO = Decimal("0")
CLOSED = frozenset(("delivered", "تم التوصيل", "cancelled", "canceled", "deleted",
    "ملغي", "ملغى", "محذوف", "refunded", "مسترجع", "returned", "restored", "تم الاسترجاع"))
OPEN = frozenset(("under_review", "waiting_review", "waiting_for_review", "pending_review",
    "pending", "waiting", "in_progress", "processing", "reviewed", "ready", "completed",
    "shipping", "shipped", "delivering", "out_for_delivery", "انتظار المراجعة",
    "بإنتظار المراجعة", "بانتظار المراجعة", "تم المراجعة", "تمت المراجعة", "قيد التنفيذ",
    "جاري التنفيذ", "تم التنفيذ", "جاري التوصيل", "تم الشحن"))
READS = ("unified_orders", "mezan_product_cost_profiles_v2", "mezan_products_v2",
    "mezan_product_resource_bindings_v2", "mezan_product_option_cost_bindings_v2",
    "mezan_cost_resources_v2", "mezan_preparation_pieces_v1", "mezan_supplier_invoices_v2",
    "mezan_suppliers_v2", "store_drivers", "store_delivery_assignments", "mz2_shipping_setup_v2")
POOL_NAME = "منتجات لم تُستلم من المورد"
_READ_CACHE = SnapshotReadCache(ttl=20, max_entries=32)
READ_TIMEOUT = 45


def _fmt(value):
    return str(value.quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def _status(raw):
    value = raw.get("status")
    if isinstance(value, dict):
        value = value.get("slug") or value.get("name")
    return str(value or "").strip().lower()


def _cutover_group(status):
    return "awaiting_review" if status in {
        "under_review", "waiting_review", "waiting_for_review", "pending_review", "pending",
        "waiting", "انتظار المراجعة", "بانتظار المراجعة", "بإنتظار المراجعة"
    } else "under_fulfillment"


def _name(record, fallback):
    return str(record.get("name") or record.get("company_name") or record.get("display_name") or fallback)


def _claims(invoice):
    claims = []
    for line in invoice.get("lines") or []:
        preparation_checkpoint()
        if not isinstance(line, dict):
            continue
        for pid in line.get("piece_ids") or []:
            preparation_checkpoint()
            if line.get("product_charge_eligible") is not False:
                claims.append(str(pid))
            for service in line.get("services") or []:
                preparation_checkpoint()
                if isinstance(service, dict) and service.get("service_id"):
                    claims.append(str(pid) + ":service:" + str(service["service_id"]))
    preparation_checkpoint()
    return claims


def _coverage(data, suppliers, started, stamp, issues):
    covered, poststart, uncertain = defaultdict(set), set(), set()
    invoices = [r for r in data["mezan_supplier_invoices_v2"]
        if not r.get("experiment_mode") and not r.get("experiment_run_id") and r.get("approved_at")]
    counts = Counter(str(r.get("id") or "") for r in invoices)
    for invoice in invoices:
        preparation_checkpoint()
        claims = _claims(invoice)
        try:
            approved = instant(invoice["approved_at"])
            if approved > stamp:
                continue
            if not invoice.get("id") or counts[str(invoice["id"])] != 1:
                raise ValueError("supplier_invoice_identity_ambiguous")
            if str(invoice.get("supplier_id") or "") not in suppliers:
                raise ValueError("supplier_invoice_supplier_identity_incomplete")
            if not claims or len(claims) != len(set(claims)):
                raise ValueError("supplier_invoice_component_incomplete")
            for line in invoice.get("lines") or []:
                preparation_checkpoint()
                ids = line.get("piece_ids") or []
                if line.get("product_price_authority") != "mezan_v2" or not ids:
                    raise ValueError("supplier_invoice_component_incomplete")
                if line.get("quantity") is not None and number(line["quantity"]) != len(ids):
                    raise ValueError("supplier_invoice_component_incomplete")
            try:
                invoice_component_amounts(invoice)
            except (ValueError, KeyError, TypeError):
                raise ValueError("supplier_invoice_tax_or_amount_incomplete") from None
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            uncertain.update(claims)
            code = str(exc)
            issues[code if code.startswith("supplier_invoice_") else "supplier_invoice_date_invalid"] += 1
            continue
        for component in claims:
            preparation_checkpoint()
            covered[component].add(str(invoice["id"]))
            if approved >= started:
                poststart.add(component)
    preparation_checkpoint()
    return covered, poststart, uncertain


async def project(db, owner, state, *, as_of=None):
    """Reuse identical saved-snapshot estimates briefly; never refresh finance."""
    async def load():
        with source_read_budget(READ_TIMEOUT):
            try:
                # The entire projection is read-only, including its later
                # bundle/catalog reads. This timeout never encloses a CAS.
                return await asyncio.wait_for(_project(db, owner, state, as_of=as_of), READ_TIMEOUT)
            except asyncio.TimeoutError:
                raise SourceReadError('operational_source_read_timeout', 'open_cost_exposure') from None
    return await _READ_CACHE.get(db, owner, state, as_of, load)


async def _project(db, owner, state, *, as_of=None):
    """Owner-scoped, bounded reads. No source writes or financial side effects."""
    preparation_checkpoint()
    cpu_budget = CPUWorkBudget(PARSER_BUDGET_MS)
    async def cpu_checkpoint():
        preparation_checkpoint()
        await cpu_budget.checkpoint()
        preparation_checkpoint()
    if state.get("status") not in {"active", "frozen"} or not state.get("started_at"):
        return {"status": "not_started", "groups": [], "currencies": {}, "issues": []}
    if state.get("status") == "frozen":
        # A current database read must not masquerade as the frozen historical snapshot.
        return {"status": "frozen", "groups": [], "currencies": {}, "issues": []}
    started = instant(state["started_at"])
    stamp = instant(as_of or datetime.now(timezone.utc))
    if stamp < started:
        raise ValueError("operational_work_forecast_time_invalid")
    data = await load_collections(db, owner, READS, reader=rows)
    issues = Counter()
    declared_scopes = {}
    unscoped_codes = set()
    values = defaultdict(lambda: {"amount": ZERO, "units": 0, "orders": set()})
    currencies = defaultdict(lambda: defaultdict(lambda: ZERO))

    def add(period, kind, stage, identity, name, currency, amount, units, oid):
        # Names are labels, not identity: two suppliers can have the same name.
        group = values[(period, kind, stage, identity, name, currency)]
        group["amount"] += amount
        group["units"] += units
        group["orders"].add(oid)

    profiles = unique_index(data["mezan_product_cost_profiles_v2"], "salla_product_id")
    catalogue = unique_index(data["mezan_products_v2"], "salla_product_id")
    profile_counts = Counter(str(r.get("salla_product_id") or "") for r in data["mezan_product_cost_profiles_v2"])
    suppliers = unique_index(data["mezan_suppliers_v2"], "id")
    drivers = unique_index(data["store_drivers"], "id")
    setups = data["mz2_shipping_setup_v2"]
    setup = setups[0] if len(setups) == 1 else {}
    if len(setups) > 1:
        issues["shipping_setup_ambiguous"] += 1
    else:
        setup, conflicts = with_tariffs(setup, owner, state)
        if conflicts:
            issues["operational_courier_tariff_authority_conflict"] += len(conflicts)
    pieces = [r for r in data["mezan_preparation_pieces_v1"]
        if not r.get("experiment_mode") and not r.get("experiment_run_id")]
    piece_counts = Counter(str(r.get("id") or "") for r in pieces)
    piece_index = unique_index(pieces, "id")
    pieces_by_item = defaultdict(list)
    for piece in pieces:
        await cpu_checkpoint()
        pieces_by_item[(str(piece.get("order_number")), str(piece.get("order_item_id")))].append(piece)
    coverage, approved_after_start, uncertain = await prepare_in_thread(_coverage, data, suppliers, started, stamp, issues)
    assignments = defaultdict(list)
    for entry in data["store_delivery_assignments"]:
        await cpu_checkpoint()
        if entry.get("status") in {"assigned", "out_for_delivery", "delivered"}:
            assignments[str(entry.get("order_number"))].append(entry)

    raw_orders = []
    for record in data["unified_orders"]:
        await cpu_checkpoint()
        source = record.get("raw_by_source")
        raw = source.get("salla_direct") if isinstance(source, dict) else None
        if not isinstance(raw, dict):
            issues["canonical_salla_source_missing_for_open_cost_preview"] += 1
            continue
        raw_orders.append((record, raw))
    ids = Counter(str(r.get("id") or "") for _, r in raw_orders)
    nums = Counter(str(r.get("reference_id") or r.get("order_number") or "") for _, r in raw_orders)
    bundle_costs = await load_remaining_bundles(db, owner, [raw for _, raw in raw_orders], stamp,
        preparation_pieces=pieces, operational_state=state)
    current_stages, current_statuses = {}, {}
    identified_orders = set()
    for record, raw in raw_orders:
        await cpu_checkpoint()
        oid = str(raw.get("id") or "")
        num = str(raw.get("reference_id") or raw.get("order_number") or "")
        try:
            created = instant(raw.get("date") or raw.get("created_at") or raw.get("order_date"))
        except (ValueError, TypeError):
            issues["order_created_at_incomplete"] += 1
            continue
        if created > stamp:
            continue
        status = _status(raw)
        if not oid or not num or ids[oid] != 1 or nums[num] != 1:
            issues["historical_order_identity_ambiguous"] += 1
            continue
        identified_orders.add(oid)
        if created > started:
            current_stages[oid] = _cutover_group(status)
            current_statuses[oid] = status
            continue
        if status not in OPEN | CLOSED:
            issues["historical_order_status_unverified"] += 1
            continue
        watermark = record.get("g47_salla_snapshot")
        if isinstance(watermark, dict) and watermark.get("requires_authoritative_refresh"):
            issues["historical_order_refresh_required"] += 1
            continue
        amounts = raw.get("amounts") if isinstance(raw.get("amounts"), dict) else {}
        total = amounts.get("total")
        currency = raw.get("currency") or (total.get("currency") if isinstance(total, dict) else None)
        stage = _cutover_group(status)
        items = raw.get("items")
        if not isinstance(items, list) or not items:
            issues["historical_items_incomplete"] += 1
            items = []
        line_ids = Counter(str(i.get("id") or i.get("item_id") or i.get("order_item_id") or "")
                           for i in items if isinstance(i, dict))
        for item in items:
            await cpu_checkpoint()
            if not isinstance(item, dict):
                issues["historical_items_incomplete"] += 1
                continue
            line_id = str(item.get("id") or item.get("item_id") or item.get("order_item_id") or "")
            if not line_id or line_ids[line_id] != 1:
                issues["historical_order_item_identity_ambiguous"] += 1
                continue
            stable = "salla:" + num + ":" + line_id
            bundle = bundle_costs.get((num, stable))
            if bundle is not None:
                for problem in bundle["issues"]:
                    await cpu_checkpoint()
                    issues[problem["code"]] += 1
                for component in bundle["items"]:
                    await cpu_checkpoint()
                    component_id = component["id"]
                    if component_id in uncertain or len(coverage[component_id]) > 1:
                        issues["historical_supplier_component_ambiguous"] += 1
                        continue
                    if coverage[component_id]:
                        if component_id in approved_after_start:
                            issues["prestart_order_postcutover_invoice_needs_reconciliation"] += 1
                        continue
                    received = component.get("supplier_receipt_proven") is True
                    if not received and not unreceived_eligible(status):
                        continue
                    party = str(component.get("supplier_id") or "") if received else ""
                    if received and party not in suppliers:
                        issues["historical_received_supplier_identity_missing"] += 1
                        continue
                    amount = number(component["cost"])
                    if amount:
                        bucket = "prestart_supplier_received_uninvoiced" if received else "prestart_supplier_unreceived"
                        label = _name(suppliers[party], "مورد الاستلام") if received else POOL_NAME
                        product_stage = "received_without_approved_invoice" if received else stage
                        add("prestart", "supplier", product_stage, party, label, "SAR", amount, 1, oid)
                        currencies["SAR"][bucket] += amount
                        currencies["SAR"]["prestart_supplier_expected"] += amount
                continue
            product = item.get("product") if isinstance(item.get("product"), dict) else {}
            product_id = str(item.get("parent_product_id") or item.get("product_id") or product.get("id") or "")
            try:
                if profile_counts[product_id] > 1:
                    raise ValueError("historical_product_cost_profile_ambiguous")
                profile = profiles.get(product_id, {})
                variant = str(item.get("variant_id") or "")
                has_base = (profile.get("base_cost") is not None or
                            variant and variant in (profile.get("variant_costs") or {}))
                fallback_used = False
                if not has_base:
                    catalog = catalogue.get(product_id)
                    if not catalog:
                        raise ValueError("historical_catalog_cost_missing")
                    if variant:
                        matches = [v for v in catalog.get("variants", [])
                            if isinstance(v, dict) and str(v.get("id") or "") == variant]
                        if len(matches) != 1:
                            raise ValueError("historical_variant_catalog_cost_ambiguous")
                        price = matches[0].get("cost_price_from_salla")
                    else:
                        price = catalog.get("cost_price_from_salla")
                    from operational_product_cost_policy import require_sar_cost
                    require_sar_cost(catalog)
                    fallback = number(price)
                    if fallback <= 0:
                        raise ValueError("historical_catalog_cost_missing")
                    profile = dict(profile, base_cost=str(fallback))
                    fallback_used = True
                unit, services = cost_components(data, product_id, item, profile)
                qty = number(item.get("quantity"))
                if qty <= 0 or qty != qty.to_integral_value():
                    raise ValueError("historical_quantity_invalid")
                matched = pieces_by_item.get((num, stable), [])
                projected = []
                if matched:
                    if len(matched) != qty or len({p.get("unit_index") for p in matched}) != len(matched):
                        raise ValueError("historical_piece_coverage_incomplete")
                    for piece in matched:
                        await cpu_checkpoint()
                        pid = str(piece.get("id") or "")
                        if not pid or piece_counts[pid] != 1:
                            raise ValueError("historical_piece_identity_ambiguous")
                        order = {"items": []}
                        append_components(order, pid, unit, services, 1, piece.get("supplier_id"), piece, as_of=stamp)
                        projected.extend((c, piece) for c in order["items"])
                else:
                    order = {"items": []}
                    append_components(order, stable, unit, services, qty, as_of=stamp)
                    projected.extend((c, None) for c in order["items"])
            except (ValueError, TypeError, KeyError, AttributeError):
                issues["historical_supplier_cost_incomplete"] += 1
                continue
            included = False
            for component, piece in projected:
                await cpu_checkpoint()
                key = str(component.get("id") or "")
                if not key or key in uncertain or len(coverage[key]) > 1:
                    issues["historical_supplier_component_ambiguous"] += 1
                    continue
                if coverage[key]:
                    if key in approved_after_start:
                        issues["prestart_order_postcutover_invoice_needs_reconciliation"] += 1
                    continue
                try:
                    amount = number(component["cost"]) * number(component.get("quantity", "1"))
                    if amount != amount.quantize(Decimal(".01")):
                        raise ValueError("rounding")
                except (ValueError, TypeError, KeyError):
                    issues["historical_supplier_cost_incomplete"] += 1
                    continue
                received = component.get("supplier_receipt_proven") is True
                if received:
                    party = str(component.get("supplier_id") or "")
                    if party not in suppliers:
                        issues["historical_received_supplier_identity_missing"] += 1
                        continue
                    label = _name(suppliers[party], "مورد الاستلام")
                    product_stage = "received_without_approved_invoice"
                    bucket = "prestart_supplier_received_uninvoiced"
                else:
                    if not unreceived_eligible(status):
                        continue
                    party, label, product_stage = "", POOL_NAME, stage
                    bucket = "prestart_supplier_unreceived"
                add("prestart", "supplier", product_stage, party, label, "SAR", amount,
                    int(number(component.get("quantity", "1"))), oid)
                currencies["SAR"][bucket] += amount
                currencies["SAR"]["prestart_supplier_expected"] += amount
                included = True
            if fallback_used and included:
                issues["historical_current_catalog_estimate_used"] += 1

        # Receipt obligations survive a closed order; unreceived estimates do not.
        if status in CLOSED:
            continue
        # A prepaid service was already charged to its approved cashbox at
        # completion. It cannot remain an open delivery-fee estimate as well.
        if state.get('engine', {}).get('facts', {}).get('prepaid_shipping:' + oid):
            continue
        # Shipping quote is independent from product-cost completeness.
        shipping = raw.get("salla_shipping_current") or raw.get("shipping") or {}
        if not isinstance(shipping, dict):
            issues["historical_shipping_quote_incomplete"] += 1
            continue
        if shipping.get("delivered_at") or _status(shipping) in {"delivered", "تم التوصيل"}:
            continue
        assigned = assignments.get(num, [])
        if len(assigned) > 1:
            issues["historical_shipping_assignment_ambiguous"] += 1
            continue
        if assigned and assigned[0].get("status") == "delivered":
            continue
        party_type, party, label = "courier", "", ""
        if assigned:
            party_type = "store_driver"
            party = str(assigned[0].get("driver_id") or "")
            if party in drivers:
                label = _name(drivers[party], "مندوب المتجر")
            else:
                party = ""
        else:
            carrier = shipping.get("company_code") or shipping.get("company")
            candidates = [c for c in setup.get("couriers", []) if usable(c)
                and c.get("confirmed_by") and c.get("confirmed_at")
                and matches_courier_key(c, carrier)]
            if len(candidates) == 1 and candidates[0].get("courier_key"):
                party = str(candidates[0]["courier_key"])
                label = _name(candidates[0], "شركة شحن")
        if not party:
            issues["historical_shipping_unassigned"] += 1
            continue
        try:
            rates = [r for r in setup.get("contracts", [])
                if r.get("status") == "approved" and r.get("party_type") == party_type
                and str(r.get("party_id")) == party and r.get("context") == "delivery"
                and instant(r["effective_from"]) <= stamp
                and (not r.get("effective_to") or stamp < instant(r["effective_to"]))]
            if len(rates) != 1 or rates[0].get("currency", "SAR") != "SAR":
                raise ValueError("historical_shipping_contract_incomplete")
            payment = raw.get("payment") if isinstance(raw.get("payment"), dict) else {}
            method = str(payment.get("method") or raw.get("payment_method") or "").lower()
            is_cod = method in {"cod", "cash_on_delivery", "cashondelivery", "cash on delivery",
                "الدفع عند الاستلام", "دفع عند الاستلام", "دفع عند الإستلام"}
            if is_cod and currency != "SAR":
                raise ValueError("historical_shipping_cod_currency_requires_review")
            due = number(cod_facts(raw, number(amounts.get("total", raw.get("total"))))["outstanding"]) if is_cod else None
            # This historical adapter already admits only SAR contracts, with
            # an explicit SAR default for its legacy setup records above.
            quote = shipping_charge({**rates[0], 'currency': 'SAR'}, owner, party, stamp, due,
                                    setup, cod_currency=currency)
            if not quote.get("fee_complete"):
                raise ValueError("historical_shipping_fee_component_incomplete")
            amount = number(quote["cost"])
            if amount != amount.quantize(Decimal(".01")):
                raise ValueError("rounding")
            add("prestart", party_type, "not_delivered", party, label, "SAR", amount, 1, oid)
            currencies["SAR"]["prestart_shipping_expected"] += amount
        except (ValueError, TypeError, KeyError, AttributeError):
            issues["historical_shipping_quote_incomplete"] += 1

    engine = state.get("engine") or {}
    engine_at = None
    try:
        if engine.get("as_of"):
            engine_at = instant(engine["as_of"])
            if not started <= engine_at <= stamp:
                raise ValueError("engine_time")
        elif engine.get("obligations"):
            raise ValueError("engine_time")
    except (ValueError, TypeError):
        issues["current_cost_snapshot_time_incomplete"] += 1
        engine_at = None
    for source_issue in engine.get("issues") or []:
        await cpu_checkpoint()
        code = source_issue if isinstance(source_issue, str) else (source_issue.get("code") if isinstance(source_issue, dict) else None)
        shipping_component = isinstance(source_issue, dict) and source_issue.get("component") in {
            "shipping_fee", "carrier_identity", "driver_custody", "prepaid_shipping"}
        if code and (shipping_component or re.search(r"supplier|invoice|shipping|carrier|canonical|product_cost|preparation|bundle|cost_snapshot|order_cost_authority", code)):
            current_code = "current_" + code
            issues[current_code] += 1
            if shipping_component and current_code not in unscoped_codes:
                declared_scopes[current_code] = {"shipping_expected"}
            elif not shipping_component:
                # Counts merge equal codes, but a scoped occurrence must not
                # narrow a separate occurrence whose dependency is unknown.
                unscoped_codes.add(current_code)
                declared_scopes.pop(current_code, None)
    for obligation in (engine.get("obligations") or {}).values() if engine_at else []:
        await cpu_checkpoint()
        if obligation.get("superseded") or obligation.get("derived_credit"):
            continue
        kind = obligation.get("kind")
        if kind not in {"supplier", "shipping"}:
            continue
        currency = str(obligation.get("currency") or "")
        try:
            if not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError("currency")
            expected = number(obligation.get("expected", "0"))
            confirmed = number(obligation.get("confirmed", "0"))
        except ValueError:
            issues["operational_cost_obligation_incomplete"] += 1
            continue
        currencies[currency]["current_" + kind + "_confirmed"] += confirmed
        parts = str(obligation.get("id") or "").split(":", 2)
        oid = parts[1] if len(parts) > 1 else str(obligation.get("id"))
        party = str(obligation.get("party_id") or "")
        name = str(obligation.get("name") or "جهة غير مكتملة")
        if expected:
            if kind == "supplier":
                component_id = parts[2] if len(parts) > 2 else ""
                pid, sep, sid = component_id.partition(":service:")
                physical = piece_index.get(pid)
                received = supplier_receipt_proven(physical, engine_at)
                if sep:
                    plans = [s for s in (physical or {}).get("services", []) if str(s.get("service_id") or "") == sid]
                    received = received and len(plans) == 1 and plans[0].get("status") == "completed"
                if obligation.get("source_cost_stage") == "bundle_legacy_received":
                    # Frozen by the operational source/reducer at engine_at;
                    # a completed service stays received while its parent is
                    # assigned elsewhere for another service.
                    received = bool(party)
                if not received or not party:
                    status = current_statuses.get(oid)
                    if status is not None and not unreceived_eligible(status):
                        expected = ZERO
                    party, name = "", POOL_NAME
                    stage = current_stages.get(oid, "unreceived")
                    bucket = "poststart_supplier_unreceived"
                else:
                    stage, bucket = "received_without_approved_invoice", "poststart_supplier_received_uninvoiced"
                currencies[currency][bucket] += expected
            else:
                stage = "not_delivered"
            if expected:
                add("poststart", kind, stage, party, name, currency, expected, 1, oid)
        currencies[currency]["current_" + kind + "_expected"] += expected
        if confirmed:
            add("poststart", kind, "confirmed_in_financial_balances", str(obligation.get("party_id") or ""),
                str(obligation.get("name") or "جهة غير مكتملة"), currency, confirmed, 1, oid)
    # Two suppliers can receive different items from one order. Keep IDs inside this
    # projection and return only the distinct count per report table and currency.
    stages = {"received_without_approved_invoice": "products_received_uninvoiced",
              "awaiting_review": "products_unreceived", "under_fulfillment": "products_unreceived",
              "unreceived": "products_unreceived", "not_delivered": "shipping_expected"}
    table_orders = defaultdict(lambda: defaultdict(set))
    for (_, _, stage, _, _, currency), group in values.items():
        await cpu_checkpoint()
        if stage in stages:
            table_orders[currency][stages[stage]].update(group["orders"])
    counts = {currency: {table: len(ids) if ids <= identified_orders else None for table, ids in tables.items()}
              for currency, tables in table_orders.items()}
    groups = [{"period": period, "kind": kind, "stage": stage, "party_id": party,
        "party_name": name, "party_type": kind, "currency": currency,
        "amount": _fmt(v["amount"]), "units": v["units"], "orders": len(v["orders"])}
        for (period, kind, stage, party, name, currency), v in values.items()]
    groups.sort(key=lambda r: (r["period"], r["kind"], r["stage"], r["party_name"], r["party_id"]))
    customer_cod, prestart_cod = {}, {"by_currency": {}}
    if engine_at == stamp:
        customer_cod = await open_customer_cod(db, owner, raw_orders, data, state,
            started, stamp, ids, nums, include_coverage=True, coverage_index=True)
        prestart_cod = await prepare_in_thread(prestart_open_cod, raw_orders, data, state, started,
            stamp, ids, nums, OPEN, coverage_index=True)
        # The two readers deliberately reject each other's source shapes.
        # Only the same validated order identity accepted by either reader (or
        # an exact saved courier claim) clears that rejection. Unknown orders
        # stay withheld once, rather than disappearing between the cohorts.
        covered = {tuple(key) for source in (customer_cod, prestart_cod)
                   for key in source.pop("_covered_order_keys")}
        withheld = set()
        for source in (customer_cod, prestart_cod):
            await cpu_checkpoint()
            missing = {tuple(key) for key in source.pop("_withheld_order_keys")} - covered - withheld
            source["withheld_candidates"] = len(missing)
            withheld.update(missing)
    preparation_checkpoint()
    return {"status": "preview", "as_of": stamp.isoformat(),
        "engine_as_of": engine_at.isoformat() if engine_at else None, "started_at": started.isoformat(),
        "currencies": {c: {k: _fmt(v) for k, v in sorted(details.items())} for c, details in sorted(currencies.items())},
        "distinct_order_counts": counts,
        "open_customer_cod": customer_cod,
        "prestart_open_cod": prestart_cod,
        "bucket_completeness": cost_completeness(issues, declared_scopes),
        "groups": groups, "issues": [{"code": c, "count": n} for c, n in sorted(issues.items())],
        "complete": not any(c != "historical_current_catalog_estimate_used" for c in issues),
        "warning": "historical_preview_never_add_to_confirmed_or_expected_payable_without_owner_evidence"}
