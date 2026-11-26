"""Read-only fee estimates using the owner's saved legacy payment-method terms."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from bnpl.settlements_service import _capture_fee_components
from excel_parser import normalize_name, resolve_payment_setting
from operational_fee_methods import fee_rail
from operational_provider_fees import provider_projection
from operational_provider_statement_fees import AVERAGE_CALCULATION, statement_average_projection
from operational_report_copy import copy_report_value
from payment_methods import KNOWN_PAYMENT_SUB_KEYS, normalize_payment_method

CENT = Decimal(".01")
ZERO = Decimal(0)
PROVIDERS = {"salla", "tamara", "tabby", "emkan", "paypal"}
RIYADH = timezone(timedelta(hours=3))


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = Decimal(str(value))
        return value if value.is_finite() and value >= 0 else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def cash(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def configured_payment_setting(settings, provider, method=None):
    """Use the same exact-name and canonical precedence as the settings export."""
    if provider == "paypal":
        from operational_payment_evidence import provider_for
        matches = [row for row in settings.get("payment_methods") or []
                   if isinstance(row, dict) and provider_for(row.get("name")) == "paypal"]
        return matches[0] if len(matches) == 1 else None
    key = fee_rail(method) if provider == "salla" else provider
    if key is None:
        return None
    exact_rows, canonical_rows = {}, {}
    for row in settings.get("payment_methods") or []:
        if not isinstance(row, dict) or not isinstance(row.get("name"), str):
            continue
        exact_rows[normalize_name(row["name"])] = row
        subkey, _name, _parent = normalize_payment_method(row["name"])
        if subkey in KNOWN_PAYMENT_SUB_KEYS:
            canonical_rows.setdefault(subkey, row)
    lookup = method if provider == "salla" and isinstance(method, str) else key
    return resolve_payment_setting(lookup, exact_rows, canonical_rows)


def configured_terms(settings, provider, method=None):
    """Validate every term of the selected row before using its saved values."""
    row = configured_payment_setting(settings, provider, method)
    if row is None:
        return None
    values = [number(row.get(k)) for k in ("commission_percent", "fixed_fee", "vat_percent")]
    if any(v is None for v in values):
        return None
    rate, fixed, vat = values
    if rate > 100 or vat > 100 or fixed != cash(fixed):
        return None
    return rate / 100, fixed, vat / 100


def has_capture_policies(policies, provider, currency="SAR"):
    return any(row.get('provider') == provider and row.get('currency') == currency
               and row.get('status') == 'active' and row.get('event_type', 'capture') == 'capture'
               for row in policies)


def _policy_day(value):
    if isinstance(value, dict):
        stamp = datetime.fromisoformat(str(value.get('date') or '').replace('Z', '+00:00'))
        if stamp.tzinfo is None and value.get('timezone'):
            stamp = stamp.replace(tzinfo=ZoneInfo(value['timezone']))
        value = stamp
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value or '')
    if len(text) == 10:
        return date.fromisoformat(text)
    stamp = value if isinstance(value, datetime) else datetime.fromisoformat(text.replace('Z', '+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('provider_fee_transaction_date_required')
    return stamp.astimezone(RIYADH).date()


def configured_projection(policies, provider, gross, *, currency="SAR", method=None,
                          refunded=ZERO, refunds=None, payment=None, created_at=None, observed_at=None,
                          source_mode="legacy_settings", as_of=None):
    """Price a known amount with the existing policy engine, without posting facts."""
    if not has_capture_policies(policies, provider, currency):
        if source_mode == 'mz2_policies' and provider != 'paypal':
            raise ValueError('provider_fee_policy_incomplete_or_ambiguous')
        return None
    source = payment or {}
    captures = source.get('captures') or []
    try:
        dates = [_policy_day(row['captured_at']) for row in captures]
        if not dates:
            dates = [_policy_day(created_at)]
            if observed_at:
                dates.append(_policy_day(observed_at))
    except (ValueError, TypeError, KeyError):
        raise ValueError('provider_fee_transaction_date_required') from None
    calculation = {
        'provider': provider, 'currency': currency, 'gross': str(gross),
        'payment_method': fee_rail(method) if provider == 'salla' else None,
        'plan_id': source.get('plan_id'), 'fee_plan_issue': source.get('fee_plan_issue'),
        'refunds': refunds if refunds is not None else source.get('refunds', []),
    }
    result = provider_projection(calculation, policies, max(dates), earliest_date=min(dates),
                                 as_of_date=_policy_day(as_of or observed_at) if as_of or observed_at else None)
    if number(result['refunded']) != number(refunded):
        raise ValueError('provider_refund_fee_source_incomplete')
    return result


def estimate_fee(settings, provider, gross, *, method=None, refunded=ZERO, policies=(),
                 refunds=None, payment=None, created_at=None, observed_at=None, source_mode="legacy_settings", as_of=None,
                 currency="SAR"):
    """Apply the existing per-order rounding contract to evidenced SAR payments."""
    gross, refunded = number(gross), number(refunded)
    if gross is None or refunded is None or refunded > gross:
        return None
    try:
        projection = configured_projection(policies, provider, gross, method=method, refunded=refunded,
            refunds=refunds, payment=payment, created_at=created_at, observed_at=observed_at,
            source_mode=source_mode, as_of=as_of, currency=currency)
    except (ValueError, TypeError, KeyError, InvalidOperation):
        return None
    if projection is not None:
        return number(projection['estimated_fees'])
    if currency != "SAR":
        return None  # Legacy fixed fees are denominated in SAR, never relabel them as native money.
    terms = configured_terms(settings, provider, method)
    if terms is None:
        return None
    if gross == 0:
        return ZERO
    rate, fixed, vat = terms
    if provider in {"salla", "paypal"}:
        if refunded:
            # Salla refund fee credits require their executed statement terms.
            return None
        raw = gross * rate + fixed
        return cash(raw) + cash(raw * vat)
    if provider not in PROVIDERS:
        return None
    fee, tax = _capture_fee_components(provider, gross, rate, fixed, vat)
    total = cash(Decimal(str(fee)) + Decimal(str(tax)))
    if refunded and provider == "tabby":
        # Existing Tabby contract caps its refundable percentage leg at 4.99%.
        # The total percentage, fixed fee and VAT always come from saved terms.
        refundable_rate = min(rate, Decimal(".0499"))
        credit = cash(refunded * refundable_rate)
        credit_vat = (credit * vat).quantize(CENT, rounding=ROUND_HALF_EVEN)
        total -= credit + credit_vat
    elif refunded and provider != "tamara":
        return None
    return max(total, ZERO)


def _uniform_policy_vat(policies):
    treatments = set()
    for policy in policies:
        components = policy.get("components") if policy.get("fee_model_version", 1) == 2 else [policy]
        for component in components or []:
            treatment = component.get("vat_treatment")
            if treatment in {"exempt", "not_applicable"}:
                treatments.add((treatment, ZERO))
            elif treatment in {"exclusive", "inclusive"}:
                rate = number(component.get("vat_rate"))
                if rate is None or rate > 100:
                    return None, True
                treatments.add((treatment, rate / 100))
            else:
                return None, True
    return (next(iter(treatments))[1], False) if len(treatments) == 1 else (None, bool(policies))


def _uniform_policy_fixed(policies):
    amounts = set()
    for policy in policies:
        components = policy.get("components") if policy.get("fee_model_version", 1) == 2 else [policy]
        if any(component.get("vat_treatment") == "inclusive" for component in components or []):
            return None  # Inclusive fixed money is not a proved pre-VAT fixed term.
        values = [number(component.get("fixed_amount")) for component in components or []]
        if not values or any(value is None or value != cash(value) for value in values):
            return None
        amounts.add(sum(values, ZERO))
    return next(iter(amounts)) if len(amounts) == 1 else None


def _average_gap(settings, provider, gross, args):
    """Permit missing terms/qualifiers, never a bad tariff, date or tax rule."""
    policies = args.get("policies", ())
    projection_args = {key: value for key, value in args.items() if key != "policies"}
    try:
        projection = configured_projection(policies, provider, gross, **projection_args)
    except ValueError as error:
        reason = str(error)
        if reason == "provider_fee_policy_incomplete_or_ambiguous":
            return not has_capture_policies(policies, provider, args["currency"]), None, None, False
        if reason not in {"provider_payment_plan_required", "provider_payment_method_required_for_fee"}:
            return False, None, None, False
    else:
        if projection is not None:
            return False, None, None, False
        if args["currency"] != "SAR":
            return True, None, None, False  # A SAR fixed-fee table is not native currency proof.
        selected = configured_payment_setting(settings, provider, args.get("method"))
        if selected is None:
            return True, None, None, False
        values = [number(selected.get(key)) for key in ("commission_percent", "fixed_fee", "vat_percent")]
        if any(selected.get(key) not in (None, "") and value is None for key, value in
               zip(("commission_percent", "fixed_fee", "vat_percent"), values)):
            return False, None, None, False
        rate, fixed, vat = values
        if (rate is not None and rate > 100 or vat is not None and vat > 100
                or fixed is not None and fixed != cash(fixed)):
            return False, None, None, False
        return any(value is None for value in values), vat / 100 if vat is not None else None, fixed, False
    payment = args.get("payment") or {}
    dates = [_policy_day(row["captured_at"]) for row in payment.get("captures", [])]
    if not dates:
        dates = [_policy_day(args.get("created_at"))]
        if args.get("observed_at"):
            dates.append(_policy_day(args["observed_at"]))
    at = max(dates)
    rail = fee_rail(args.get("method")) if provider == "salla" else None
    applicable = [policy for policy in policies if policy.get("provider") == provider
        and policy.get("currency") == args["currency"] and policy.get("status") == "active"
        and policy.get("event_type", "capture") == "capture" and _policy_day(policy["effective_from"]) <= at
        and (not policy.get("effective_to") or at <= _policy_day(policy["effective_to"]))
        and (not payment.get("plan_id") or policy.get("plan_id") in (None, "", payment["plan_id"]))
        and (not rail or policy.get("payment_method") in (None, "", rail))]
    verified = []
    for policy in applicable:
        candidate = {**payment, "plan_id": payment.get("plan_id") or policy.get("plan_id")}
        method = args.get("method") if rail else policy.get("payment_method") or args.get("method")
        try:
            result = configured_projection(policies, provider, gross,
                **{**projection_args, "payment": candidate, "method": method})
        except ValueError as error:
            if str(error) in {"provider_payment_plan_required", "provider_payment_method_required_for_fee"}:
                continue
            return False, None, None, False
        verified.extend(row for row in applicable if row.get("id") == result.get("policy_id"))
    vat, vat_conflict = _uniform_policy_vat(verified)
    return bool(verified), vat, _uniform_policy_fixed(verified), vat_conflict


def estimate_fee_details(settings, provider, gross, *, paid_proven=False,
                         native_currency=None, native_gross=None, fee_fx_basis=None,
                         started_at=None, average_profile=None, operations=None, operations_basis=None, **kwargs):
    """Use a source-derived variable estimate only after existing tariffs fail."""
    fee = estimate_fee(settings, provider, gross, **kwargs)
    result = {"fee": fee, "basis": "plan_estimate" if fee is not None else "unavailable"}
    payment = kwargs.get("payment") or {}
    if (fee is not None or not paid_proven or average_profile is None
            or average_profile.get("calculation") != AVERAGE_CALCULATION
            or (average_profile.get("provider"), average_profile.get("native_currency")) != (provider, native_currency)
            or payment.get("fee_plan_issue") or number(native_gross) is None or number(native_gross) <= ZERO
            or number(kwargs.get("refunded", ZERO)) != ZERO or payment.get("refunds") or kwargs.get("refunds")
            or payment.get("cancellations") or number(payment.get("cancelled", ZERO)) != ZERO):
        return result
    try:
        created = datetime.fromisoformat(str(kwargs.get("created_at") or "").replace("Z", "+00:00"))
        start = datetime.fromisoformat(str(started_at or "").replace("Z", "+00:00"))
        as_of = datetime.fromisoformat(str(kwargs.get("as_of") or "").replace("Z", "+00:00"))
        if any(value.tzinfo is None for value in (created, start, as_of)) or not start < created <= as_of:
            return result
        native_args = {**kwargs, "currency": native_currency}
        allowed, vat, fixed, vat_conflict = _average_gap(settings, provider, number(native_gross), native_args)
        if not allowed:
            return result
        components = statement_average_projection(average_profile, native_gross, operations=operations,
            configured_vat_rate=vat, configured_fixed_fee=fixed, vat_conflict=vat_conflict,
            operations_basis=operations_basis)
        if kwargs.get("currency", "SAR") != native_currency:
            if kwargs.get("currency") != "SAR" or not fee_fx_basis:
                return result
            from operational_provider_balance_sar import sar_amount
            previous = cumulative = ZERO
            for key in ("variable", "fixed", "vat"):
                if components[key] is not None:
                    cumulative += components[key]
                    boundary = sar_amount(cumulative, fee_fx_basis)
                    components[key] = boundary - previous
                    previous = boundary
            if components["fee"] is not None:
                components["fee"] = sar_amount(components["fee"], fee_fx_basis)
        return {"fee": components["fee"], "basis": "average_estimate", "components": components,
                "profile": average_profile}
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
        return result


def refresh_provider_fee_inputs(state, sources):
    """Retain report inputs independently of whether a paid claim passed posting gates."""
    if "provider_fee_settings" not in sources:
        return
    previous = state["engine"].get("provider_fee_inputs", {})
    orders = copy_report_value(previous.get("orders", {}))
    for order in sources.get("orders", []):
        payment = order.get("payment") or {}
        valuation = order.get("customer_payment_sar") or {}
        identity = str(order["id"])
        observation = order.get("retained_customer_adjustment") or {}
        saved = orders.get(identity) or {}
        saved_payment = saved.get("payment") or {}
        if (not payment and observation and observation.get("order_id") == identity
                and observation.get("order_number") == saved.get("order_number") == order.get("order_number")
                and observation.get("currency") == saved.get("currency") == order.get("currency") == "SAR"
                and observation.get("provider") == saved_payment.get("provider") in PROVIDERS
                and not saved_payment.get("split") and observation.get("proof_hash")
                and observation.get("customer_adjustment", {}).get("proof_hash") == observation["proof_hash"]):
            # Do not replace the saved capture, rail, plan or pricing date with
            # a zero-paid terminal snapshot. Only its review observation moves.
            saved_payment["customer_adjustment"] = deepcopy(observation["customer_adjustment"])
            saved["retained_customer_adjustment"] = deepcopy(observation)
        if (payment.get("provider") not in PROVIDERS and not payment.get("split")
                and not PROVIDERS.intersection(valuation.get("native", {}))):
            continue
        orders[identity] = {k: deepcopy(order[k]) for k in (
            "order_number", "currency", "created_at", "updated_at", "payment", "customer_payment_sar") if k in order}
    state["engine"]["provider_fee_inputs"] = {
        "settings": deepcopy(sources["provider_fee_settings"]), "orders": orders,
        "policies": deepcopy(sources.get("fee_policies", [])),
        "source_mode": sources.get("provider_fee_source_mode", "legacy_settings"),
        **{key: deepcopy(previous[key]) for key in ("statement_events", "statement_evidence_as_of", "average_profiles") if key in previous},
    }
