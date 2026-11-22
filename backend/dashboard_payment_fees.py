"""Read-only dashboard fee authority for an already selected order cohort.

Actual imported fee totals supersede estimates. Dated policies are evaluated by
the existing operational calculator; untouched legacy cohorts keep their exact
aggregate rounding. No sales/status selection or financial posting occurs here.
"""
from copy import deepcopy
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
import asyncio
import json

from excel_parser import match_settings, normalize_name, resolve_payment_setting
from dashboard_cpu_budget import CPUWorkBudget, COST_PROFIT_BUDGET_MS
from operational_configured_provider_fees import configured_projection, has_capture_policies, _policy_day, _average_gap, RIYADH
from operational_fee_methods import fee_rail, order_fee_rail
from operational_payment_evidence import provider_for, _method_rows, _instant, normalize_payment
from order_currency import order_total_sar, order_amount_to_sar, currency_code, money_decimal, _stored_original_total
from orders_db import orders_to_parsed_cooperative
from payment_methods import is_unselected_payment_method, normalize_payment_method, KNOWN_PAYMENT_SUB_KEYS
from provider_fee_sources import bind_statement_plan, load_statement_plans
from operational_provider_statement_fees import statement_average_projection
from provider_fee_weekly_sources import load_weekly_statement_averages, load_weekly_fee_source_state
from provider_fee_statement_actual_sources import load_operational_statement_fee_index

BASIS = "actual_statement_then_dated_provider_policy_then_legacy_settings"
AVERAGE_BASIS = "actual_statement_then_dated_provider_policy_then_weekly_statement_average_then_operational_balance_average_then_legacy_settings"
FEE_SOURCE_PROJECTION = {
    "_id": 0, "order_number": 1,
    **{f"raw_by_source.salla_direct.{key}": 1 for key in (
        "payment", "payment_method", "payment_methods", "created_at", "date", "updated_at", "currency",
        "refunded_amount", "status", "is_pending_payment", "payment_actions.refund_action", "payment_actions.remaining_action")},
}


def attach_fee_source_evidence(orders, projected_rows):
    """Attach only the projected fee proof in memory, never customer fields."""
    index = {str(row.get("order_number")): row for row in projected_rows}
    for order in orders:
        raw = (index.get(str(order.get("order_number")), {}).get("raw_by_source") or {}).get("salla_direct")
        if isinstance(raw, dict):
            order["_dashboard_fee_source"] = {key: deepcopy(raw[key]) for key in (
                "payment", "payment_method", "payment_methods", "created_at", "date", "updated_at", "currency",
                "refunded_amount", "status", "is_pending_payment") if key in raw}
            if "payment_actions" in raw:
                actions = raw["payment_actions"]
                order["_dashboard_fee_source"]["payment_actions"] = (
                    {key: deepcopy(actions[key]) for key in ("refund_action", "remaining_action") if key in actions} if isinstance(actions, dict)
                    else deepcopy(actions))


def dashboard_payment_method(order):
    value = (order.get("actual_payment_method") if order.get("payment_fee_status") == "actual" else None) or order.get("payment_method")
    return str(value or "غير محدد").strip() or "غير محدد"


_method = dashboard_payment_method


def _provider(method):
    return provider_for(method) or ("salla" if fee_rail(method) else None)


async def load_dashboard_fee_context(db, owner, orders, *, as_of=None, fee_context=None):
    from payment_method_fee_status import load_fee_setup_context
    supplied = fee_context if fee_context is not None else await load_fee_setup_context(db, owner)
    request_reads = supplied.get("_request_fee_reads")
    # The optional memo is owned by one compute request. Tasks never enter the
    # copied fee context, collector, API response, or persistent card cache.
    context = deepcopy({key: value for key, value in supplied.items() if key != "_request_fee_reads"})
    context.update(as_of=as_of or context.get("evidence_as_of") or datetime.now(timezone.utc), statement_plans={})

    async def read_once(key, reader):
        if request_reads is None:
            return await reader()
        if key not in request_reads:
            request_reads[key] = asyncio.create_task(reader())
        return deepcopy(await request_reads[key])
    providers = {p.get("provider") for p in context["policies"] if p.get("status") == "active"
                 and p.get("event_type", "capture") == "capture" and p.get("plan_id")}
    if context["complete"] and providers:
        numbers = [order.get("order_number") for order in orders
                   if order.get("payment_fee_status") != "actual" and _provider(_method(order)) in providers]
        if len(numbers) > 20000:
            context["complete"] = False
            return context
        try:
            context["statement_plans"] = await load_statement_plans(db, owner, providers=providers, order_numbers=numbers)
        except Exception as error:
            # A plan read failure must never fall through to an unrelated rate.
            from fastapi import HTTPException
            from pymongo.errors import PyMongoError
            if not isinstance(error, (PyMongoError, TimeoutError, HTTPException, ValueError)):
                raise
            context["complete"] = False
    tabby_governed = any(row.get("provider") == "tabby" and row.get("status") == "active"
                        and row.get("event_type", "capture") == "capture" for row in context.get("policies", []))
    if context.get("complete") and tabby_governed:
        candidates = [order for order in orders if _provider(_method(order)) == "tabby"]
        currencies = sorted({currency_code(order.get("original_currency") or order.get("currency"), default="SAR")
                             for order in candidates if order.get("payment_fee_status") != "actual"})
        if candidates:
            evidence_as_of = context.setdefault("evidence_as_of", datetime.now(timezone.utc))
            if "weekly_fee_source_state" not in context and not context.get("weekly_fee_source_error"):
                try:
                    context["weekly_fee_source_state"] = await load_weekly_fee_source_state(db, owner)
                except ValueError:
                    context["weekly_fee_source_error"] = True
            source_state = None if context.get("weekly_fee_source_error") else context.get("weekly_fee_source_state")
            scope = (owner, str(context["as_of"]), str(evidence_as_of), (source_state or {}).get("revision"))
            context["weekly_statement_averages"] = (
                {} if context.get("weekly_fee_source_error") or not currencies else await read_once(
                    ("weekly", *scope, tuple(currencies)), lambda: load_weekly_statement_averages(
                        db, owner, as_of=context["as_of"], currencies=currencies,
                        source_state=source_state, evidence_as_of=evidence_as_of)))
            numbers = tuple(sorted({str(order["order_number"]) for order in candidates if order.get("order_number") is not None}))
            context["operational_statement_fees"] = await read_once(
                ("actual", *scope, numbers), lambda: load_operational_statement_fee_index(
                    db, owner, numbers, source_state=source_state, accepted_as_of=evidence_as_of))
    return context


def merge_statement_estimate_metadata(target, source):
    """Accumulate only affected orders; never label every child of a mixed row."""
    if not source.get("fees_estimated"):
        return
    target["fees_estimated"] = True
    target["estimated_orders_count"] = target.get("estimated_orders_count", 0) + source["estimated_orders_count"]
    sources = {json.dumps(row, sort_keys=True, separators=(",", ":")): row
               for row in [*target.get("fee_estimate_sources", []), *source.get("fee_estimate_sources", [])]}
    target["fee_estimate_sources"] = [deepcopy(sources[key]) for key in sorted(sources)]


def _statement_average_estimate(order, context, raw, payment, native_currency, created, refunds, refunded):
    """Price only a paid Tabby order whose sole missing qualifier is its plan."""
    average = context.get("weekly_statement_averages", {}).get(native_currency) or {}
    basis = "weekly_statement_average"
    if not average.get("available"):
        average = average.get("operational_fallback") or {}
        basis = "operational_balance_average"
    if not average.get("available") or not isinstance(average.get("profile"), dict):
        raise ValueError("provider_weekly_statement_average_unavailable")
    profile = average["profile"]
    if (profile.get("provider") != "tabby" or profile.get("native_currency") != native_currency
            or payment.get("plan_id") or payment.get("fee_plan_issue") or refunds or refunded
            or raw.get("is_pending_payment") is True):
        raise ValueError("provider_weekly_average_evidence_incomplete")
    account = payment.get("merchant_account")
    if account and profile.get("merchant_account") != account:
        raise ValueError("provider_weekly_average_account_conflict")
    native_gross = _stored_original_total(order)
    if native_gross is None or native_gross <= 0:
        raise ValueError("provider_weekly_average_paid_evidence_required")
    as_of = context["as_of"]
    if isinstance(as_of, date) and not isinstance(as_of, datetime) or isinstance(as_of, str) and len(as_of) == 10:
        as_of = datetime.combine(_policy_day(as_of), time.max, tzinfo=RIYADH)
    normalized, diagnostics = normalize_payment(raw, native_currency, native_gross, created, as_of, page_balance=True)
    # Salla's paid observation proves the order-page amount but need not supply
    # a capture timestamp. This authorizes a fee estimate, never a ledger fact.
    if not normalized or any(item["code"] != "provider_capture_evidence_incomplete" for item in diagnostics):
        raise ValueError("provider_weekly_average_paid_evidence_required")
    observed = (normalized.get("paid_observation") or {}).get("amount")
    captures = payment.get("captures") or []  # Already identity/date/amount checked by _override.
    if observed is not None and _decimal(observed) != native_gross:
        raise ValueError("provider_weekly_average_paid_evidence_required")
    if not captures and (observed is None or _decimal(observed) != native_gross):
        raise ValueError("provider_weekly_average_paid_evidence_required")
    arguments = {"policies": context["policies"], "currency": native_currency, "method": _method(order),
        "payment": payment, "refunds": refunds, "refunded": refunded, "created_at": created,
        "observed_at": raw.get("updated_at"), "as_of": context["as_of"], "source_mode": context["source_mode"]}
    allowed, vat, fixed, vat_conflict = _average_gap({}, "tabby", native_gross, arguments)
    if not allowed:
        raise ValueError("provider_weekly_average_policy_conflict")
    projection = statement_average_projection(profile, native_gross, operations=1,
        configured_vat_rate=vat, configured_fixed_fee=fixed, vat_conflict=vat_conflict,
        operations_basis="capture_facts" if captures else "one_provider_order_estimate")
    if projection.get("fee") is None or not projection.get("complete"):
        raise ValueError("provider_weekly_average_tax_incomplete")
    native_net = projection["variable"] + projection["fixed"]
    net_sar = order_amount_to_sar(native_net, order)
    total_sar = order_amount_to_sar(projection["fee"], order)
    if net_sar is None or total_sar is None:
        raise ValueError("payment_sales_currency_unverified")
    metadata = {"provider": "tabby", "native_currency": native_currency, "profile_id": profile["profile_id"], "basis": basis,
        "rate_percent": profile["rate_percent"], "fixed_amount_per_sale": format(projection["fixed"], "f"),
        "vat_rate_percent": format(Decimal(projection["vat_rate"]) * 100, "f"),
        "eligible_sale_rows": profile["eligible_sale_rows"],
        "fixed_basis": projection["fixed_basis"], "vat_basis": projection["vat_basis"]}
    if basis == "weekly_statement_average":
        metadata.update(window_start=average["window_start"], window_end=average["window_end"],
            stale_source=average["stale_source"], source_statement_count=len(average["source_statement_ids"]))
    else:
        metadata.update({key: profile.get(key) for key in ("source_as_of", "accepted_at", "source_date_basis")})
        metadata.update({key: average.get(key) for key in ("period_start", "period_end")})
    return Decimal(str(net_sar)), Decimal(str(total_sar)) - Decimal(str(net_sar)), basis, metadata


def _decimal(value):
    if value is None or isinstance(value, bool) or value == "":
        raise ValueError("payment_fee_amount_missing")
    try:
        value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError("payment_fee_amount_invalid") from None
    if not value.is_finite() or abs(value) > Decimal("999999999999.99"):
        raise ValueError("payment_fee_amount_invalid")
    return value


def _full_timestamp(value):
    if isinstance(value, dict):
        value = value.get("date")
    return isinstance(value, datetime) or isinstance(value, str) and len(value) > 10


def _check_event_date(stamp, created, as_of, reason):
    day = _policy_day(stamp)
    if day > _policy_day(as_of) or created and day < _policy_day(created):
        raise ValueError(reason)
    if _full_timestamp(stamp):
        instant = _instant(stamp)
        if _full_timestamp(as_of) and instant > _instant(as_of):
            raise ValueError(reason)
        if _full_timestamp(created) and instant < _instant(created):
            raise ValueError(reason)


def _check_accepted_current_basis(order, accepted, native_currency):
    """An accepted fee cannot cover a newer capture/refund merely by amount."""
    raw = order.get("_dashboard_fee_source")
    if raw is None:
        raw = (order.get("raw_by_source") or {}).get("salla_direct", {})
    if not isinstance(raw, dict):
        raise ValueError("provider_statement_fee_order_conflict")
    payment, actions = raw.get("payment") or {}, raw.get("payment_actions") or {}
    if not isinstance(payment, dict) or not isinstance(actions, dict):
        raise ValueError("provider_statement_fee_order_conflict")
    provider = accepted["provider"]
    principal, refunded = _decimal(accepted["principal_amount"]), _decimal(accepted["refunded_amount"])
    if (payment.get("split") or any(_provider(value) != provider for value in (
            payment.get("method"), raw.get("payment_method")) if value)):
        raise ValueError("provider_statement_fee_order_conflict")
    if payment.get("merchant_account") and payment["merchant_account"] != accepted.get("merchant_account"):
        raise ValueError("provider_statement_fee_account_conflict")
    if any(currency_code(value) != native_currency for value in (raw.get("currency"), payment.get("currency")) if value is not None):
        raise ValueError("provider_statement_fee_currency_conflict")

    def amount(value):
        if isinstance(value, dict) and currency_code(value.get("currency"), default=native_currency) != native_currency:
            raise ValueError("provider_statement_fee_currency_conflict")
        result = money_decimal(value)
        if result is None or result < 0:
            raise ValueError("provider_statement_fee_amount_conflict")
        return result

    methods = raw.get("payment_methods") or []
    if not isinstance(methods, list) or any(not isinstance(row, dict) for row in methods):
        raise ValueError("provider_statement_fee_order_conflict")
    if any(_provider(row.get("payment_method")) != provider for row in methods):
        raise ValueError("provider_split_payment_review_required")
    monetary = any(any(key in row for key in ("amount", "transaction_reference")) for row in methods)
    references = {str(payment[key]) for key in ("reference", "transaction_reference") if payment.get(key)}
    if monetary:
        native_rows = _method_rows(raw, provider, native_currency, page_balance=True)
        if sum((row[1] for row in native_rows), Decimal(0)) != principal:
            raise ValueError("provider_statement_fee_amount_conflict")
        references.update(row[0] for row in native_rows if row[0])
    captures = payment.get("captures") or []
    if not isinstance(captures, list) or any(not isinstance(row, dict) or not row.get("id") for row in captures):
        raise ValueError("provider_statement_fee_order_conflict")
    if captures:
        unique = {}
        for capture in captures:
            identity, value = str(capture["id"]), amount(capture.get("amount"))
            if capture.get("currency") is not None and currency_code(capture["currency"]) != native_currency:
                raise ValueError("provider_statement_fee_currency_conflict")
            if identity in unique and unique[identity] != value:
                raise ValueError("provider_statement_fee_order_conflict")
            unique[identity] = value
        if sum(unique.values()) != principal:
            raise ValueError("provider_statement_fee_amount_conflict")
        references.update(unique)
    if references and accepted.get("capture_references") and references != set(accepted["capture_references"]):
        raise ValueError("provider_statement_fee_capture_basis_changed")
    refund_action, remaining_action = actions.get("refund_action") or {}, actions.get("remaining_action") or {}
    if not isinstance(refund_action, dict) or not isinstance(remaining_action, dict):
        raise ValueError("provider_statement_fee_order_conflict")
    for container in (payment, refund_action, remaining_action):
        if container.get("paid_amount") is not None and amount(container["paid_amount"]) != principal:
            raise ValueError("provider_statement_fee_amount_conflict")
    observations = [_decimal(order.get("actual_refund_amount", 0)) + _decimal(order.get("actual_partial_refund_amount", 0))]
    observations.extend(amount(value) for value in (
        payment.get("refunded"), payment.get("refunded_amount"), raw.get("refunded_amount"), refund_action.get("refund_amount"))
        if value is not None)
    refunds = payment.get("refunds") or []
    native_refunds = refund_action.get("refunded_transactions") or []
    if (not isinstance(refunds, list) or len(refunds) > 100
            or not isinstance(native_refunds, list) or len(native_refunds) > 100
            or any(not isinstance(row, dict) for row in [*refunds, *native_refunds])):
        raise ValueError("provider_statement_fee_refund_basis_changed")
    if native_refunds and (not accepted.get("refund_references") or any(
            row.get("status") not in {"executed", "settled", "Refund Executed", "Settled"} for row in native_refunds)):
        raise ValueError("provider_statement_fee_refund_basis_changed")
    executed = {}
    for refund in [*refunds, *native_refunds]:
        if refund.get("status") not in {"executed", "settled", "Refund Executed", "Settled"}:
            continue
        if not refund.get("id"):
            raise ValueError("provider_statement_fee_refund_basis_changed")
        identity, value = str(refund["id"]), amount(refund.get("amount"))
        if refund.get("currency") is not None and currency_code(refund["currency"]) != native_currency:
            raise ValueError("provider_statement_fee_currency_conflict")
        if identity in executed and executed[identity] != value:
            raise ValueError("provider_statement_fee_refund_basis_changed")
        executed[identity] = value
    if executed:
        observations.append(sum(executed.values()))
        if accepted.get("refund_references") and set(executed) != set(accepted["refund_references"]):
            raise ValueError("provider_statement_fee_refund_basis_changed")
    if any(value < 0 or value > 0 and value != refunded for value in observations):
        raise ValueError("provider_statement_fee_refund_basis_changed")
    if refund_action.get("refunded_transactions") and not any(value > 0 for value in observations):
        raise ValueError("provider_statement_fee_refund_basis_changed")
    status = raw.get("status") or order.get("order_status")
    status = status.get("slug") or status.get("name") if isinstance(status, dict) else status
    cancelled = (_decimal(order.get("actual_canceled_amount", 0)) or _decimal(payment.get("cancelled", 0))
                 or payment.get("cancellations") or status in {"cancelled", "canceled", "ملغي", "ملغى"})
    if (cancelled or status in {"refunded", "restored", "مسترجع"}) and refunded != principal:
        raise ValueError("provider_statement_fee_refund_basis_changed")
    if raw.get("is_pending_payment") is True:
        raise ValueError("provider_statement_fee_order_conflict")


def _override(order, context):
    """Return net/tax/source, or None for an unchanged legacy cohort."""
    method = _method(order)
    provider = _provider(method)
    if order_total_sar(order) is None:
        raise ValueError("payment_sales_currency_unverified")
    accepted = (context or {}).get("operational_statement_fees", {}).get(str(order.get("order_number")))
    if accepted and accepted.get("available"):
        native_currency = currency_code(order.get("original_currency") or order.get("currency"), default="SAR")
        if accepted.get("provider") != provider or accepted.get("native_currency") != native_currency:
            raise ValueError("provider_statement_fee_order_conflict")
        principal = _decimal(accepted.get("principal_amount"))
        if principal != _stored_original_total(order):
            raise ValueError("provider_statement_fee_amount_conflict")
        _check_accepted_current_basis(order, accepted, native_currency)
        native_net, native_vat = _decimal(accepted.get("fee_net")), _decimal(accepted.get("fee_vat"))
        if native_net + native_vat != _decimal(accepted.get("fee_total")):
            raise ValueError("provider_statement_fee_amount_conflict")
        net, total = order_amount_to_sar(native_net, order), order_amount_to_sar(native_net + native_vat, order)
        if net is None or total is None:
            raise ValueError("payment_sales_currency_unverified")
        return Decimal(str(net)), Decimal(str(total)) - Decimal(str(net)), "actual_statement"
    if order.get("payment_fee_status") == "actual":
        # Imported actual_* values are SAR and already include signed rebates.
        # Missing VAT is not proof of zero VAT.
        return (_decimal(order.get("actual_payment_fee")), _decimal(order.get("actual_payment_vat")), "actual_statement")
    if accepted and not (accepted.get("reason_code") == "provider_statement_fee_coverage_incomplete"
                         and accepted.get("plan_id") and not accepted.get("plan_issue")):
        raise ValueError(accepted.get("reason_code") or "provider_statement_fee_review_required")
    if is_unselected_payment_method(method):
        # An order waiting for payment has no chargeable method yet. Do not
        # ask the merchant to configure a commission or make profit complete.
        raise ValueError("payment_awaiting_collection")
    if context is None:
        return None
    if not context.get("complete") and provider:
        raise ValueError("provider_fee_source_unavailable")
    governed = provider and (context.get("source_mode") == "mz2_policies" and provider != "paypal"
                             or has_capture_policies(context.get("policies", []), provider))
    if not governed:
        return None
    gross = order_total_sar(order)
    if gross is None:
        raise ValueError("payment_sales_currency_unverified")
    raw = order.get("_dashboard_fee_source")
    if raw is None:
        raw = (order.get("raw_by_source") or {}).get("salla_direct", {})
    if not isinstance(raw, dict):
        raise ValueError("provider_fee_source_incomplete")
    payment = raw.get("payment") or {}
    if not isinstance(payment, dict):
        raise ValueError("provider_fee_source_incomplete")
    payment = bind_statement_plan({**payment, "provider": provider}, context.get("statement_plans", {}), order.get("order_number"))
    if accepted and accepted.get("plan_id"):
        payment = bind_statement_plan(payment, {(provider, str(order.get("order_number"))): {
            "plan_id": accepted["plan_id"], "plan_evidence": {"source": "operational_accepted_statement",
                "source_event_keys": accepted.get("source_event_keys", [])}}}, order.get("order_number"))
    identities = [value for value in (payment.get("method"), raw.get("payment_method")) if value]
    if any(_provider(value) != provider for value in identities):
        raise ValueError("provider_payment_method_conflict")
    # Do not price the entire order as a single processor when its tenders differ.
    methods = raw.get("payment_methods", [])
    if methods is None:
        methods = []
    if not isinstance(methods, list) or len(methods) > 100 or any(not isinstance(item, dict) for item in methods):
        raise ValueError("provider_payment_shape_invalid")
    if payment.get("split"):
        raise ValueError("provider_split_payment_review_required")
    routes = {_provider(item.get("payment_method")) for item in methods}
    if routes and routes != {provider}:
        raise ValueError("provider_split_payment_review_required")
    raw_has_method = payment.get("method") or raw.get("payment_method") or methods
    rail = order_fee_rail(raw) if raw_has_method else fee_rail(method)
    if provider == "salla" and rail is None:
        # A normalized top-level label cannot resolve conflicting native rails.
        raise ValueError("provider_payment_method_required_for_fee")
    native_currency = currency_code(order.get("original_currency") or order.get("currency"), default="SAR")
    if any(currency_code(value) != native_currency for value in (raw.get("currency"), payment.get("currency")) if value is not None):
        raise ValueError("provider_payment_currency_conflict")
    monetary_methods = any(any(key in item for key in ("amount", "transaction_reference", "plan_id")) for item in methods)
    if monetary_methods:
        native_rows = _method_rows(raw, provider, native_currency, page_balance=True)
        if native_rows:
            _reference, native_amount, native_plan = native_rows[0]
            native_sar = order_amount_to_sar(native_amount, order)
            if native_sar is None or Decimal(str(native_sar)) != Decimal(str(gross)):
                raise ValueError("provider_payment_amount_conflict")
            if native_plan is not None:
                if payment.get("plan_id") is not None and payment["plan_id"] != native_plan:
                    raise ValueError("provider_payment_plan_conflict")
                payment = {**payment, "plan_id": native_plan}
    elif methods:
        # Wrapper + card-network descriptors may describe one funding source,
        # but they are not permission to combine multiple monetary tenders.
        if provider != "salla" and len(methods) > 1:
            raise ValueError("provider_split_payment_review_required")
        if any(currency_code(item["currency"]) != native_currency for item in methods if item.get("currency") is not None):
            raise ValueError("provider_payment_currency_conflict")
    # configured_projection deliberately has no cancellation contract. Keep
    # these orders incomplete until a statement gives authoritative actual fees.
    if (payment.get("cancellations") or _decimal(payment.get("cancelled", 0)) != 0
            or _decimal(order.get("actual_canceled_amount", 0)) != 0):
        raise ValueError("provider_cancellation_execution_evidence_required")
    created = raw.get("created_at") or raw.get("date") or order.get("order_date_raw") or order.get("order_date")
    incomplete_capture = False
    if not payment.get("captures") and (
            payment.get("paid_at") is not None or payment.get("paid_amount") is not None
            or payment.get("status") in {"paid", "captured"}):
        # Reuse canonical evidence normalization rather than selecting a tariff
        # by creation date when an authoritative native capture date exists.
        as_of = context["as_of"]
        if ((isinstance(as_of, date) and not isinstance(as_of, datetime))
                or (isinstance(as_of, str) and len(as_of) == 10)):
            as_of = datetime.combine(_policy_day(as_of), time.max, tzinfo=RIYADH)
        normalized, diagnostics = normalize_payment(
            raw, native_currency, _stored_original_total(order), created, as_of, page_balance=True)
        if diagnostics or not normalized or not normalized.get("captures"):
            # Native paid observations still support a clearly marked average
            # when Tabby's plan is missing. Keep the dated/capture authority
            # incomplete; the fallback independently checks full payment below.
            incomplete_capture = bool(provider == "tabby" and not payment.get("plan_id") and normalized
                and diagnostics and all(item["code"] == "provider_capture_evidence_incomplete" for item in diagnostics)
                and money_decimal((normalized.get("paid_observation") or {}).get("amount")) == _stored_original_total(order))
            if not incomplete_capture:
                raise ValueError(diagnostics[0]["code"] if diagnostics else "provider_capture_evidence_incomplete")
            if payment.get("paid_at") is not None:
                _check_event_date(payment["paid_at"], created, context["as_of"], "provider_capture_timestamp_conflict")
        else:
            payment = {**payment, "captures": normalized["captures"]}
    refunds = payment.get("refunds") or []
    if not isinstance(refunds, list):
        raise ValueError("provider_refund_fee_source_incomplete")
    refunded = _decimal(order.get("actual_refund_amount", 0)) + _decimal(order.get("actual_partial_refund_amount", 0))
    executed = {}
    for refund in refunds:
        if not isinstance(refund, dict):
            raise ValueError("provider_refund_fee_source_incomplete")
        if refund.get("status") not in {"executed", "settled", "Refund Executed", "Settled"}:
            continue
        if currency_code(refund.get("currency"), default=native_currency) != native_currency:
            raise ValueError("provider_refund_currency_conversion_required")
        stamp = refund.get("refunded_at") or refund.get("executed_at")
        if stamp is None:
            raise ValueError("provider_refund_timestamp_required")
        _check_event_date(stamp, created, context["as_of"], "provider_refund_timestamp_conflict")
        identity = refund.get("id")
        if not identity:
            raise ValueError("refund_identity_missing")
        value = _decimal(refund.get("amount"))
        if identity in executed and executed[identity] != value:
            raise ValueError("refund_identity_conflict")
        executed[identity] = value
    if executed:
        if native_currency != "SAR":
            raise ValueError("provider_refund_currency_conversion_required")
        if refunded and refunded != sum(executed.values()):
            raise ValueError("provider_refund_fee_source_incomplete")
        refunded = sum(executed.values())
    actions = raw.get("payment_actions") or {}
    if not isinstance(actions, dict):
        raise ValueError("provider_refund_fee_source_incomplete")
    refund_action = actions.get("refund_action") or {}
    if not isinstance(refund_action, dict):
        raise ValueError("provider_refund_fee_source_incomplete")
    if refund_action.get("refunded_transactions"):
        # Native action records need their own execution/identity mapping. They
        # cannot silently disappear when canonical payment.refunds is absent.
        raise ValueError("provider_refund_execution_evidence_required")
    for observed in (payment.get("refunded"), payment.get("refunded_amount"), raw.get("refunded_amount"), refund_action.get("refund_amount")):
        if observed is None:
            continue
        if isinstance(observed, dict) and currency_code(observed.get("currency"), default=native_currency) != native_currency:
            raise ValueError("provider_refund_currency_conversion_required")
        value = money_decimal(observed)
        if value is None or value < 0:
            raise ValueError("provider_refund_fee_source_incomplete")
        if value > 0 and (native_currency != "SAR" or not executed or value != refunded):
            raise ValueError("provider_refund_execution_evidence_required")
    captures = payment.get("captures") or []
    if order.get("order_date_inferred") and not captures and not (raw.get("created_at") or raw.get("date")):
        raise ValueError("provider_fee_transaction_date_required")
    if captures:
        if not isinstance(captures, list):
            raise ValueError("provider_capture_evidence_incomplete")
        captured = {}
        for capture in captures:
            if not isinstance(capture, dict) or not capture.get("id"):
                raise ValueError("provider_capture_evidence_incomplete")
            amount = money_decimal(capture.get("amount"))
            currency = currency_code(capture.get("currency") or payment.get("currency") or raw.get("currency"), default=native_currency)
            if amount is None or amount < 0 or currency != native_currency:
                raise ValueError("provider_capture_evidence_incomplete")
            key = str(capture["id"])
            value = (amount, capture.get("captured_at"))
            if key in captured and captured[key] != value:
                raise ValueError("provider_capture_conflict")
            captured[key] = value
        if len(captured) > 1:
            raise ValueError("provider_split_payment_review_required")
        captured_sar = order_amount_to_sar(sum(value[0] for value in captured.values()), order)
        if captured_sar is None or Decimal(str(captured_sar)) != Decimal(str(gross)):
            raise ValueError("provider_capture_original_basis_required")
    try:
        dates = [_policy_day(row["captured_at"]) for row in captures] if captures else [_policy_day(created)]
    except (ValueError, TypeError, KeyError):
        raise ValueError("provider_fee_transaction_date_required") from None
    if any(day > _policy_day(context["as_of"]) for day in dates):
        raise ValueError("provider_fee_transaction_date_invalid")
    authoritative_created = created if (raw.get("created_at") or raw.get("date") or not order.get("order_date_inferred")) else None
    for capture in captures:
        _check_event_date(capture["captured_at"], authoritative_created, context["as_of"], "provider_capture_timestamp_conflict")
    try:
        projection = configured_projection(context["policies"], provider, gross, method=rail if provider == "salla" else method,
            payment=payment, refunds=refunds, refunded=refunded, source_mode=context["source_mode"],
            created_at=created, observed_at=raw.get("updated_at"), as_of=context["as_of"])
    except ValueError as error:
        if provider != "tabby" or str(error) != "provider_payment_plan_required":
            raise
        return _statement_average_estimate(order, context, raw, payment, native_currency, created, refunds, refunded)
    if incomplete_capture:
        raise ValueError("provider_capture_evidence_incomplete")
    if projection is None:
        raise ValueError("provider_fee_policy_incomplete_or_ambiguous")
    return (_decimal(projection.get("estimated_fee_net")), _decimal(projection.get("estimated_fee_tax")), "dated_provider_policy")


def _legacy_terms_valid(method, settings):
    exact, canonical = {}, {}
    for row in settings:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        exact[normalize_name(row["name"])] = row
        key, _display, _parent = normalize_payment_method(row["name"])
        if key in KNOWN_PAYMENT_SUB_KEYS:
            canonical.setdefault(key, row)
    selected = resolve_payment_setting(method, exact, canonical)
    if selected is None:
        return False
    try:
        rate, fixed, vat = [_decimal(selected.get(key)) for key in ("commission_percent", "fixed_fee", "vat_percent")]
    except ValueError:
        return False
    return 0 <= rate <= 100 and 0 <= vat <= 100 and fixed >= 0 and fixed == fixed.quantize(Decimal(".01"))


async def dashboard_fee_breakdown(orders, settings, context=None):
    """Numeric known amounts plus explicit completeness; consumers mask unknowns."""
    priced = [{**order, "payment_method": _method(order)} for order in orders]
    parsed = await orders_to_parsed_cooperative(priced)
    legacy, overrides, errors, term_validity = [], {}, {}, {}
    budget = CPUWorkBudget(COST_PROFIT_BUDGET_MS)
    for order in priced:
        await budget.checkpoint()
        name = order["payment_method"]
        try:
            result = _override(order, context)
        except (ValueError, TypeError, KeyError, ArithmeticError) as error:
            reason = str(error)
            reason = reason if isinstance(error, ValueError) and reason.replace("_", "").isalnum() and reason.isascii() else "provider_fee_source_incomplete"
            errors.setdefault(name, set()).add(reason)
            continue
        if result is None:
            if name not in term_validity:
                term_validity[name] = _legacy_terms_valid(name, settings)
            if term_validity[name]:
                legacy.append(order)
            else:
                errors.setdefault(name, set()).add("payment_method_unconfigured")
        else:
            overrides.setdefault(name, []).append(result)
    legacy_parsed = await orders_to_parsed_cooperative(legacy)
    # Invalid unused legacy rows cannot poison a complete actual/dated scope.
    safe_settings = [row for row in settings if isinstance(row, dict) and isinstance(row.get("name"), str)
                     and _legacy_terms_valid(row["name"], [row])]
    legacy_rows = {row["name"]: row for row in match_settings(legacy_parsed, safe_settings, [])["payment_breakdown"]}
    output = []
    for group in parsed["payment_methods"]:
        await budget.checkpoint()
        name = group["name"]
        row = deepcopy(legacy_rows.get(name) or {})
        net = Decimal(str(row.get("base_commission", 0)))
        vat = Decimal(str(row.get("vat_amount", 0)))
        sources = set()
        if row:
            sources.add("legacy_settings")
            if row.get("matched") is not True:
                errors.setdefault(name, set()).add("payment_method_unconfigured")
        for override in overrides.get(name, []):
            fee, tax, source = override[:3]
            net += fee
            vat += tax
            sources.add(source)
            if source in {"weekly_statement_average", "operational_balance_average"}:
                merge_statement_estimate_metadata(row, {"fees_estimated": True, "estimated_orders_count": 1,
                                                "fee_estimate_sources": [override[3]]})
        complete = not errors.get(name)
        fee = round(float(net + vat), 2)
        output.append({**row, **group, "base_commission": round(float(net), 2), "vat_amount": round(float(vat), 2),
            "fee_amount": fee, "net_amount": round(group["total_sales"] - fee, 2), "matched": complete,
            "fees_complete": complete, "fee_review_reasons": sorted(errors.get(name, [])),
            "fee_sources": sorted(sources),
            "fee_calculation_basis": AVERAGE_BASIS if row.get("fees_estimated") else BASIS if overrides.get(name) or errors.get(name) else row.get("fee_calculation_basis")})
    estimates = {}
    for row in output:
        merge_statement_estimate_metadata(estimates, row)
    return {"payment_breakdown": output, "total_payment_fees": round(sum(row["fee_amount"] for row in output), 2),
            "payment_fees_complete": all(row["fees_complete"] for row in output),
            "fee_review_reasons": sorted({reason for reasons in errors.values() for reason in reasons}),
            "basis": AVERAGE_BASIS if estimates else BASIS,
            **({"payment_fees_estimated": True, "payment_fee_estimate_sources": estimates["fee_estimate_sources"],
                "estimated_orders_count": estimates["estimated_orders_count"]} if estimates else {})}


def enforce_dashboard_fee_completeness(response):
    """Keep partial known fees out of totals and profit, including after ad fees."""
    authority = response.get("payment_fee_authority")
    if not authority:
        return response
    totals = response["totals"]
    totals["payment_fees_complete"] = authority["complete"]
    totals["payment_fee_review_reasons"] = authority["reason_codes"]
    if authority.get("payment_fees_estimated"):
        totals["payment_fees_estimated"] = True
        totals["payment_fee_estimate_sources"] = deepcopy(authority["payment_fee_estimate_sources"])
    unknown = set(authority["incomplete_buckets"])
    fields = set()
    if not authority["complete"]:
        fields.update(("total_payment_fees", "net_profit", "expected_salla_transfer", "total_vat"))
        config = response.get("net_sales_config") or {}
        if config.get("deduct_payment_fees", True) or config.get("deduct_vat", False):
            fields.add("net_sales")
    for provider in ("tabby", "tamara", "emkan"):
        if provider in unknown:
            fields.update((provider + "_fees", "bnpl_fees", "bnpl_net"))
    if "bank_transfer" in unknown:
        fields.update(("bank_fees", "bank_net"))
    detail = totals.get("electronic_net_breakdown") or {}
    if not authority.get("electronic_before_filter_complete", authority.get("electronic_complete", True)):
        detail["fees_before_filter"] = None
    if not authority.get("electronic_complete", True):
        fields.update(("other_payment_fees", "electronic_net", "electronic_net_before_settlements"))
        detail["fees_after_filter"] = None
    for field in fields:
        totals[field] = None
    for row in response.get("payment_breakdown", []):
        if row.get("key") in unknown:
            row["fees_complete"] = False
            for field in ("fee_amount", "vat_amount"):
                if row.get(field) is not None:
                    row["known_" + field + "_sar"] = row[field]
                row[field] = None
            for sub in row.get("sub_methods", []):
                sub["fee_amount"] = None
    return response
