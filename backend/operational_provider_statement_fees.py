"""Pure fee-only projection of accepted statement events over a saved snapshot.

Raw imports are never a fee source here. Accepted evidence cannot create a
capture, a refund, a settlement or bank cash, and is rechecked on every read.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from hashlib import sha256
import json
import re

CENT = Decimal(".01")
ZERO = Decimal(0)
LIMIT = Decimal("999999999999.99")
MAX_AVERAGE_PROFILES = 32
AVERAGE_SCHEMA = "statement_variable_weighted_average_v1"
AVERAGE_CALCULATION = "sale_variable_fees_before_vat_over_sale_principal"
PROVIDERS = frozenset(("tabby", "tamara", "salla"))
REVIEW = "provider_statement_fee_review_required"
RIYADH = timezone(timedelta(hours=3))


def statement_evidence_fingerprint(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), default=str).encode()).hexdigest()


def _cash(value):
    if value is None or isinstance(value, bool):
        raise ValueError(REVIEW)
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or abs(amount) > LIMIT or amount != amount.quantize(CENT):
            raise ValueError(REVIEW)
        return amount
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(REVIEW) from None


def _positive(value):
    amount = _cash(value)
    if amount <= ZERO:
        raise ValueError(REVIEW)
    return amount


def _stamp(value):
    stamp = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError(REVIEW)
    return stamp


def _profile_key(profile):
    return statement_evidence_fingerprint([profile["owner_id"], profile["provider"],
                                          profile["native_currency"], profile.get("merchant_account")])


def _profile_id(profile):
    return statement_evidence_fingerprint({key: value for key, value in profile.items()
        if key not in {"profile_id", "status", "conflicting_sources"}})


def _profile_order(profile):
    source = profile["source_as_of"]
    at = datetime.fromisoformat(source).replace(tzinfo=RIYADH) if len(source) == 10 else _stamp(source)
    return at, _stamp(profile["accepted_at"])


def derive_statement_average_profile(state, normalized_statement, *, statement_id, accepted_at):
    """Use all sale rows or none; other event types never enter the ratio.

    Ratios remain exact numerator/denominator pairs. Display percentages are
    never pricing inputs; variable, fixed and VAT money components round only
    after their own final calculation.
    """
    data = normalized_statement
    try:
        accepted = _stamp(accepted_at)
        if (not state.get("owner_id") or data.get("schema_version") != "operational_statement_v1"
                or data.get("provider") not in PROVIDERS or not statement_id
                or any(not isinstance(data.get(key), str) or not re.fullmatch(r"[a-f0-9]{64}", data[key])
                       for key in ("file_hash", "fingerprint"))):
            return None
        sales = [row for row in data.get("rows", []) if row.get("event_type") == "sale"]
        if not sales or len(sales) > 10000:
            return None
        currencies = {row.get("native_currency") for row in sales}
        accounts = {row.get("merchant_account") for row in sales if row.get("merchant_account")}
        if data.get("merchant_account"):
            accounts.add(data["merchant_account"])
        if len(currencies) != 1 or len(accounts) > 1:
            return None
        currency = next(iter(currencies))
        if not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
            return None
        principal = fee_net = fee_vat = variable_net = fixed_net = ZERO
        days = []
        seen = set()
        vat_rates = set()
        missing_vat_rate = False
        conflicting_vat_rate = False
        for row in sales:
            fingerprint = row.get("row_fingerprint")
            if (row.get("blockers") or row.get("provider") != data["provider"]
                    or not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint)
                    or fingerprint in seen or not row.get("order_number")):
                return None
            seen.add(fingerprint)
            amount = _positive(row.get("principal_amount"))
            net, tax, total = (_cash(row.get(key)) for key in ("fee_net", "fee_vat", "fee_total"))
            if min(net, tax, total) < ZERO or net + tax != total or amount - total != _cash(row.get("transferred_amount")):
                return None
            variable, fixed = (_cash(row.get(key)) for key in ("fee_variable_net", "fee_fixed_net"))
            if min(variable, fixed) < ZERO or variable + fixed != net:
                return None
            rate = row.get("fee_vat_rate")
            if "statement_tax_rate_invalid" in row.get("fee_component_issues", []):
                conflicting_vat_rate = True
            if rate is None:
                missing_vat_rate = True
            else:
                rate = Decimal(str(rate))
                if not rate.is_finite() or not ZERO <= rate <= 1:
                    return None
                vat_rates.add(rate)
                # Source tax can be rounded per component. Tabby has at most
                # three taxable legs (two variable plus fixed), Tamara two;
                # versus rounded aggregate tax the cent bounds are .02/.01.
                # A zero base/rate has no rounding uncertainty.
                tolerance = (Decimal(".02") if data["provider"] == "tabby" else CENT) if net and rate else ZERO
                expected_tax = (net * rate).quantize(CENT, rounding=ROUND_HALF_UP)
                if abs(tax - expected_tax) > tolerance:
                    conflicting_vat_rate = True
            principal += amount
            fee_net += net
            fee_vat += tax
            variable_net += variable
            fixed_net += fixed
            if row.get("event_date"):
                days.append(date.fromisoformat(row["event_date"]))
        if days and max(days) > accepted.astimezone(RIYADH).date():
            return None
        if data.get("statement_date"):
            source = date.fromisoformat(data["statement_date"])
            if source > accepted.astimezone(RIYADH).date() or days and source < max(days):
                return None
            source_as_of, source_basis, precision = source.isoformat(), "statement_date", "day"
        elif days:
            source_as_of, source_basis, precision = max(days).isoformat(), "sale_event_date", "day"
        else:
            source_as_of, source_basis, precision = accepted_at, "accepted_at", "instant"
        total = fee_net + fee_vat
        # Keep the compact active profile within the same validated money range.
        for amount in (principal, fee_net, fee_vat, total, variable_net, fixed_net):
            _cash(amount)
        vat_rate = next(iter(vat_rates)) if len(vat_rates) == 1 and not missing_vat_rate and not conflicting_vat_rate else None
        profile = {"schema_version": AVERAGE_SCHEMA, "owner_id": state["owner_id"],
            "provider": data["provider"], "native_currency": currency,
            "merchant_account": next(iter(accounts)) if accounts else None,
            "statement_id": statement_id, "statement_reference": data.get("statement_reference"),
            "file_hash": data["file_hash"], "source_fingerprint": data["fingerprint"],
            "source_as_of": source_as_of, "source_date_basis": source_basis, "date_precision": precision,
            "accepted_at": accepted_at, "eligible_sale_rows": len(sales),
            "principal_total": format(principal, ".2f"), "fee_net_total": format(fee_net, ".2f"),
            "fee_vat_total": format(fee_vat, ".2f"), "fee_total": format(total, ".2f"),
            "fee_variable_net_total": format(variable_net, ".2f"), "fee_fixed_net_total": format(fixed_net, ".2f"),
            "rate_percent": format((variable_net * 100 / principal).quantize(Decimal(".00000001"), rounding=ROUND_HALF_UP), ".8f"),
            "fixed_amount_per_sale": format((fixed_net / len(sales)).quantize(Decimal(".00000001"), rounding=ROUND_HALF_UP), ".8f"),
            "fixed_basis": "statement_sale_mean",
            "vat_rate": format(vat_rate, "f") if vat_rate is not None else None,
            "vat_rate_percent": format(vat_rate * 100, ".8f") if vat_rate is not None else None,
            "vat_rate_status": "source_conflict" if conflicting_vat_rate else "mixed" if len(vat_rates) > 1 else "missing" if missing_vat_rate else "explicit_uniform",
            "calculation": AVERAGE_CALCULATION, "status": "active"}
        profile["scope_key"] = _profile_key(profile)
        profile["profile_id"] = _profile_id(profile)
        return profile if len(json.dumps(profile, ensure_ascii=False).encode()) <= 8192 else None
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
        return None


def _valid_average_profile(profile, owner):
    try:
        principal = _positive(profile.get("principal_total"))
        net, tax, fee = (_cash(profile.get(key)) for key in ("fee_net_total", "fee_vat_total", "fee_total"))
        variable, fixed = (_cash(profile.get(key)) for key in ("fee_variable_net_total", "fee_fixed_net_total"))
        count = profile.get("eligible_sale_rows")
        if type(count) is not int or not 0 < count <= 10000:
            return False
        rate = format((variable * 100 / principal).quantize(Decimal(".00000001"), rounding=ROUND_HALF_UP), ".8f")
        fixed_per_sale = format((fixed / count).quantize(Decimal(".00000001"), rounding=ROUND_HALF_UP), ".8f")
        vat_rate = profile.get("vat_rate")
        if vat_rate is not None:
            vat_rate = Decimal(str(vat_rate))
            if not vat_rate.is_finite() or not ZERO <= vat_rate <= 1:
                return False
        return (profile.get("schema_version") == AVERAGE_SCHEMA and profile.get("owner_id") == owner
            and profile.get("provider") in PROVIDERS and profile.get("status") == "active"
            and profile.get("calculation") == AVERAGE_CALCULATION
            and min(net, tax, fee, variable, fixed) >= ZERO and variable + fixed == net and net + tax == fee
            and rate == profile.get("rate_percent") and fixed_per_sale == profile.get("fixed_amount_per_sale")
            and profile.get("fixed_basis") == "statement_sale_mean"
            and profile.get("vat_rate_status") in {"source_conflict", "mixed", "missing", "explicit_uniform"}
            and (vat_rate is not None) == (profile.get("vat_rate_status") == "explicit_uniform")
            and (profile.get("vat_rate_percent") == (format(vat_rate * 100, ".8f") if vat_rate is not None else None))
            and profile.get("scope_key") == _profile_key(profile) and profile.get("profile_id") == _profile_id(profile)
            and bool(_profile_order(profile)) and len(json.dumps(profile, ensure_ascii=False).encode()) <= 8192)
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
        return False


def store_statement_average_profile(state, profile):
    """One active source per provider/currency/account; no growing file history."""
    if profile is None:
        return False
    if not _valid_average_profile(profile, state.get("owner_id")):
        raise ValueError("provider_statement_average_invalid")
    profiles = state.setdefault("provider_statement_reconciliation", {}).setdefault("average_profiles", {})
    key = profile["scope_key"]
    previous = profiles.get(key)
    if previous is not None:
        if previous.get("source_fingerprint") == profile["source_fingerprint"]:
            return False
        if _profile_order(profile) < _profile_order(previous):
            return False
        if _profile_order(profile) == _profile_order(previous):
            profiles[key] = {**deepcopy(previous), "status": "ambiguous",
                "conflicting_sources": sorted({previous["source_fingerprint"], profile["source_fingerprint"]})}
            return True
    elif len(profiles) >= MAX_AVERAGE_PROFILES:
        raise ValueError("provider_statement_average_scope_limit")
    profiles[key] = deepcopy(profile)
    return True


def select_statement_average_profile(state, provider, native_currency, *, payment=None):
    profiles = state.get("engine", {}).get("provider_fee_inputs", {}).get("average_profiles", [])
    candidates = [profile for profile in profiles if isinstance(profile, dict)
                  and profile.get("provider") == provider and profile.get("native_currency") == native_currency]
    account = (payment or {}).get("merchant_account")
    if account:
        candidates = [profile for profile in candidates if profile.get("merchant_account") == account]
    if (len(candidates) != 1 or not _valid_average_profile(candidates[0], state.get("owner_id"))):
        return None
    return candidates[0]


def statement_average_summary(profile):
    if not isinstance(profile, dict) or not _valid_average_profile(profile, profile.get("owner_id")):
        return {"available": False, "reason_code": "source_average_unavailable" if profile is None else
                "source_profile_ambiguous" if isinstance(profile, dict) and profile.get("status") == "ambiguous"
                else "source_profile_invalid"}
    return {"available": True, **{key: profile.get(key) for key in (
        "profile_id", "provider", "native_currency", "merchant_account", "rate_percent", "principal_total",
        "fee_variable_net_total", "fee_fixed_net_total", "fixed_amount_per_sale", "fixed_basis",
        "fee_net_total", "fee_vat_total", "fee_total", "eligible_sale_rows", "statement_id", "statement_reference",
        "source_as_of", "source_date_basis", "date_precision", "accepted_at", "vat_rate", "vat_rate_percent",
        "vat_rate_status", "calculation")}, "full_estimate_requires_vat": profile.get("vat_rate") is None}


def statement_average_projection(profile, principal, *, operations=None, configured_vat_rate=None,
                                 configured_fixed_fee=None, vat_conflict=False, operations_basis=None):
    """Variable commission, estimated fixed charge and VAT stay separate."""
    if operations is not None and (type(operations) is not int or operations < 1):
        raise ValueError("provider_statement_average_operations_invalid")
    variable = (_cash(principal) * _cash(profile["fee_variable_net_total"])
                / _positive(profile["principal_total"])).quantize(CENT, rounding=ROUND_HALF_UP)
    fixed_rate = (_cash(configured_fixed_fee) if configured_fixed_fee is not None else
                  _cash(profile["fee_fixed_net_total"]) / profile["eligible_sale_rows"])
    fixed = ((fixed_rate * operations).quantize(CENT, rounding=ROUND_HALF_UP) if operations is not None
             else ZERO if fixed_rate == ZERO else None)
    rate = None if vat_conflict else configured_vat_rate if configured_vat_rate is not None else profile.get("vat_rate")
    rate = Decimal(str(rate)) if rate is not None else None
    if rate is not None and (not rate.is_finite() or not ZERO <= rate <= 1):
        raise ValueError("provider_statement_average_vat_invalid")
    tax = ((variable + fixed) * rate).quantize(CENT, rounding=ROUND_HALF_UP) if rate is not None and fixed is not None else None
    return {"variable": variable, "fixed": fixed, "vat": tax,
            "fee": variable + fixed + tax if tax is not None and fixed is not None else None,
            "vat_rate": format(rate, "f") if rate is not None else None,
            "vat_basis": "conflicting_policy" if vat_conflict else "configured" if configured_vat_rate is not None else "statement" if rate is not None else "unavailable",
            "fixed_basis": "configured" if configured_fixed_fee is not None else "statement_sale_mean",
            "configured_fixed_fee": configured_fixed_fee,
            "complete": tax is not None, "operations": operations,
            "operations_basis": operations_basis if operations is not None else "unavailable"}


def freeze_statement_fee_events(state, *, accepted_at=None):
    """Freeze accepted evidence in the existing fee inputs in the caller's CAS.

    The source snapshot's as_of, principal, movements and refund state are not
    changed. The review service remains the sole writer of accepted events.
    """
    canonical = state.get("provider_statement_reconciliation", {}).get("events", {})
    if not isinstance(canonical, dict):
        raise ValueError(REVIEW)
    events = [deepcopy(canonical[key]) for key in sorted(canonical)]
    inputs = state.setdefault("engine", {}).setdefault("provider_fee_inputs", {})
    inputs["statement_events"] = events
    stamps = [event.get("accepted_at") for event in events if isinstance(event, dict)]
    if stamps:
        inputs["statement_evidence_as_of"] = max(stamps, key=_stamp)
    else:
        inputs.pop("statement_evidence_as_of", None)
    profiles = state.get("provider_statement_reconciliation", {}).get("average_profiles", {})
    if not isinstance(profiles, dict) or len(profiles) > MAX_AVERAGE_PROFILES:
        raise ValueError("provider_statement_average_scope_limit")
    inputs["average_profiles"] = [deepcopy(profiles[key]) for key in sorted(profiles)]


def prepare_statement_fee_context(state):
    """Index once for a whole upload/report, not once per statement row."""
    engine = state.get("engine", {})
    inputs = engine.get("provider_fee_inputs", {})
    context = {"orders": inputs.get("orders", {}), "facts": engine.get("facts", {}),
               "movements": {str(row.get("id")): row for row in state.get("movements", [])},
               "returns": {str(row.get("id")): row for row in state.get("customer_returns", [])},
               "captures": defaultdict(dict), "refunds": defaultdict(dict), "pages": {},
               "events": defaultdict(list), "latest": {},
               "allocations": defaultdict(lambda: {"gross": ZERO, "fees": ZERO}),
               "unallocated_fee_scopes": set()}
    for key, fact in context["facts"].items():
        if not isinstance(fact, dict):
            continue
        oid = str(fact.get("order_id") or "")
        try:
            if key.startswith("capture:") and oid and fact.get("provider") in PROVIDERS:
                context["captures"][oid, fact["provider"]][key] = _positive(fact.get("amount"))
            elif key.startswith("refund:") and oid and key.split(":", 2)[1] in PROVIDERS:
                context["refunds"][oid, key.split(":", 2)[1]][key] = _positive(fact.get("amount"))
            elif key == "provider_order_balance:" + oid and fact.get("provider") in PROVIDERS:
                context["pages"][oid, fact["provider"]] = _positive(fact.get("paid"))
        except ValueError:
            continue
    for event in inputs.get("statement_events", []):
        if isinstance(event, dict):
            pair = str(event.get("order_id") or ""), event.get("provider")
            context["events"][pair].append(event)
            try:
                stamp = _stamp(event.get("accepted_at"))
                if pair not in context["latest"] or stamp > context["latest"][pair][0]:
                    context["latest"][pair] = stamp, event["accepted_at"]
            except ValueError:
                pass
    for movement in context["movements"].values():
        if (movement.get("party_type") != "provider" or movement.get("party_id") not in PROVIDERS
                or movement.get("kind") == "correction" or movement.get("direction") != "incoming"):
            continue
        scope = movement.get("currency"), movement["party_id"]
        try:
            received, actual = _cash(movement.get("amount")), _cash(movement.get("actual_fee_amount", "0"))
            total = received + actual
            if received < ZERO or actual < ZERO or total <= ZERO:
                raise ValueError(REVIEW)
            cumulative = previous = assigned = ZERO
            for line in movement.get("allocations", []):
                value = _positive(line.get("amount"))
                cumulative += value
                boundary = (received * cumulative / total).quantize(CENT, rounding=ROUND_HALF_UP)
                fee = value - (boundary - previous)
                previous = boundary
                assigned += fee
                allocation = context["allocations"][line["obligation_id"], *scope]
                allocation["gross"] += value
                allocation["fees"] += fee
            if cumulative > total or actual != assigned:
                context["unallocated_fee_scopes"].add(scope)
        except (ValueError, TypeError, KeyError, InvalidOperation):
            context["unallocated_fee_scopes"].add(scope)
    return context


def _native_totals(context, oid, provider):
    captured = sum(context["captures"][oid, provider].values(), ZERO)
    return (max(captured, context["pages"].get((oid, provider), ZERO)),
            sum(context["refunds"][oid, provider].values(), ZERO))


def _movement_fee(movement, identity, principal):
    """Use the same cumulative-cent allocation as the existing settlement."""
    received = _cash(movement.get("amount"))
    actual = _cash(movement.get("actual_fee_amount", "0"))
    gross = received + actual
    if received < ZERO or actual < ZERO or gross <= ZERO:
        raise ValueError(REVIEW)
    cumulative = previous = assigned = fee = ZERO
    for line in movement.get("allocations", []):
        value = _positive(line.get("amount"))
        cumulative += value
        boundary = (received * cumulative / gross).quantize(CENT, rounding=ROUND_HALF_UP)
        if line.get("obligation_id") == identity:
            assigned += value
            fee += value - (boundary - previous)
        previous = boundary
    if cumulative > gross or assigned != principal:
        raise ValueError(REVIEW)
    return fee


def _check_event(state, event, context):
    if not isinstance(event, dict):
        raise ValueError(REVIEW)
    provider, oid = event.get("provider"), str(event.get("order_id") or "")
    kind, currency = event.get("kind"), event.get("native_currency")
    if (provider not in PROVIDERS or not oid or kind not in {"capture", "refund"}
            or not isinstance(event.get("event_key"), str) or not event["event_key"].strip()
            or event.get("confirmation") not in {"exact_match", "owner_approved"}
            or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)):
        raise ValueError(REVIEW)
    principal = _positive(event.get("principal_amount"))
    net, vat, fee = (_cash(event.get(key)) for key in ("fee_net", "fee_vat", "fee_total"))
    if net + vat != fee or (kind == "capture" and min(net, vat, fee) < ZERO) or (kind == "refund" and max(net, vat, fee) > ZERO):
        raise ValueError(REVIEW)
    source = event.get("source") or {}
    if (not isinstance(source, dict) or not source.get("statement_ref")
            or any(not isinstance(source.get(key), str) or not re.fullmatch(r"[a-f0-9]{64}", source[key])
                   for key in ("file_hash", "row_fingerprint"))):
        raise ValueError(REVIEW)
    accepted_day = _stamp(event.get("accepted_at")).astimezone(RIYADH).date()
    as_of_day = _stamp(state.get("engine", {}).get("as_of")).astimezone(RIYADH).date()
    start_day = _stamp(state.get("started_at")).astimezone(RIYADH).date()
    interval = event.get("date_precision") == "snapshot_interval" and event.get("event_date") is None
    event_day = None if interval else date.fromisoformat(str(event.get("event_date") or "")[:10])
    if not interval and (event.get("date_precision") not in {"day", "instant"}
                         or not start_day <= event_day <= min(accepted_day, as_of_day)):
        raise ValueError(REVIEW)
    order = context["orders"].get(oid)
    if not isinstance(order, dict) or order.get("currency") != currency:
        raise ValueError("provider_statement_fee_currency_conflict")
    paid, refunded = _native_totals(context, oid, provider)
    payment = order.get("payment") or {}
    suffix = ":" + provider if payment.get("split") else ""
    claim = state.get("engine", {}).get("obligations", {}).get("provider:" + oid + suffix, {})
    native_claim = claim.get("native_balance") or claim
    if (not claim or claim.get("incomplete") or claim.get("party_id") != provider
            or native_claim.get("currency") != currency
            or paid > _cash(native_claim.get("confirmed")) + refunded):
        raise ValueError("provider_statement_fee_principal_unconfirmed")
    if principal > (paid if kind == "capture" else refunded):
        raise ValueError("provider_statement_fee_principal_unconfirmed")
    allocation = context["allocations"]["provider:" + oid + suffix, currency, provider]
    if ((currency, provider) in context["unallocated_fee_scopes"] or allocation["gross"] and (
            kind != "capture" or refunded or principal != paid or allocation["gross"] != paid
            or allocation["fees"] != fee)):
        raise ValueError("provider_statement_fee_settlement_conflict")
    cover = event.get("covered_event") or {}
    if (not isinstance(cover, dict) or cover.get("financial_effect") not in {"fee_only", "operational_fee"}
            or _positive(cover.get("amount")) != principal):
        raise ValueError(REVIEW)
    cover_kind, identity = cover.get("kind"), str(cover.get("id") or "")
    if interval and (kind != "capture" or cover_kind != "fact"
                     or identity != "provider_order_balance:" + oid
                     or context["captures"][oid, provider] or principal != paid):
        raise ValueError(REVIEW)
    canonical = cover_kind, identity
    if cover_kind == "fact":
        saved = context["facts"].get(identity)
        if not isinstance(saved, dict) or str(saved.get("order_id") or "") != oid:
            raise ValueError(REVIEW)
        if kind == "capture":
            if saved.get("provider") != provider:
                raise ValueError(REVIEW)
            if identity.startswith("capture:"):
                value = _positive(saved.get("amount"))
            elif identity == "provider_order_balance:" + oid and saved.get("currency") == currency:
                value = _positive(saved.get("paid"))
            else:
                raise ValueError(REVIEW)
        elif identity.startswith("refund:" + provider + ":"):
            value = _positive(saved.get("amount"))
        else:
            raise ValueError(REVIEW)
        if value != principal or saved.get("currency", currency) != currency or cover["financial_effect"] != "fee_only":
            raise ValueError(REVIEW)
    elif cover_kind == "customer_return":
        saved = context["returns"].get(identity)
        if (kind != "refund" or not saved or saved.get("status") not in {"refunded", "salla_refund_confirmed"}
                or str(saved.get("order_id")) != oid or saved.get("currency") != currency
                or saved.get("refund_source_type") != "provider" or saved.get("refund_source_id") != provider
                or _positive(saved.get("amount")) != principal or cover["financial_effect"] != "fee_only"):
            raise ValueError(REVIEW)
        refund_key = "refund:" + provider + ":" + str(saved.get("refund_reference") or "")
        if context["refunds"][oid, provider].get(refund_key) != principal:
            raise ValueError("provider_statement_fee_principal_unconfirmed")
        canonical = "fact", refund_key
    elif cover_kind == "movement":
        saved = context["movements"].get(identity)
        if (not saved or kind != "capture" or saved.get("kind") == "correction"
                or saved.get("party_type") != "provider" or saved.get("party_id") != provider
                or saved.get("currency") != currency or saved.get("direction") != "incoming"
                or cover["financial_effect"] != "operational_fee"):
            raise ValueError(REVIEW)
        if _movement_fee(saved, "provider:" + oid + suffix, principal) != fee:
            raise ValueError("provider_statement_fee_settlement_conflict")
    else:
        raise ValueError(REVIEW)
    if statement_evidence_fingerprint(saved) != cover.get("fingerprint"):
        raise ValueError("provider_statement_fee_evidence_changed")
    return {"event": event, "principal": principal, "fee": fee, "cover": canonical,
            "operational": cover["financial_effect"] == "operational_fee"}


def statement_event_issue(state, event, *, context=None):
    try:
        _check_event(state, event, context if context is not None else prepare_statement_fee_context(state))
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError) as exc:
        code = str(exc)
        return code if code.startswith("provider_statement_fee_") else REVIEW
    return None


def statement_event_issues(state, events):
    context = prepare_statement_fee_context(state)
    return [statement_event_issue(state, event, context=context) for event in events]


def resolve_statement_fees(state, identity, record, *, context, applied, allocated_fee,
                           estimate, estimate_basis, average_profile=None, average_components=None):
    """Return remaining fee parts, or a review result for unsafe overlap.

    A partial known-plan estimate has per-order fixed-fee ambiguity. A source
    average prices only uncovered, individually proved capture events, so its
    estimated fixed component is counted once per uncovered event.
    """
    events = context["events"].get((record["order_id"], record["provider"]), [])
    if not events:
        return None
    result = {"actual": ZERO, "estimated": ZERO, "average": ZERO, "complete": False,
              "issue": REVIEW, "evidenced": False, "average_used": False, "components": None}
    checked, by_key, by_cover = [], {}, {}
    try:
        for event in events:
            item = _check_event(state, event, context)
            if event["native_currency"] != record["native_currency"]:
                raise ValueError("provider_statement_fee_currency_conflict")
            # A re-export with the same economic event never contributes twice.
            economic = (event["kind"], item["principal"], _cash(event["fee_net"]), _cash(event["fee_vat"]),
                        item["fee"], item["cover"], event.get("event_date"), event.get("date_precision"))
            if event["event_key"] in by_key and by_key[event["event_key"]] != economic:
                raise ValueError(REVIEW)
            if item["cover"] in by_cover:
                if by_cover[item["cover"]] != economic:
                    raise ValueError(REVIEW)
                continue
            by_key[event["event_key"]] = by_cover[item["cover"]] = economic
            checked.append(item)
        captures = [item for item in checked if item["event"]["kind"] == "capture"]
        refunds = [item for item in checked if item["event"]["kind"] == "refund"]
        capture_amount = sum((item["principal"] for item in captures), ZERO)
        refund_amount = sum((item["principal"] for item in refunds), ZERO)
        if capture_amount > record["native_gross"] or refund_amount > record["native_refunded"]:
            raise ValueError(REVIEW)
        if any(item["cover"][1].startswith("provider_order_balance:") for item in captures) and len(captures) != 1:
            raise ValueError(REVIEW)
        actual = sum((item["fee"] for item in checked), ZERO)
        # Convert only after native-currency identity and native coverage pass.
        def display(value):
            if record["currency"] == record["native_currency"]:
                return value
            if record.get("fee_fx_basis") is None:
                raise ValueError("provider_statement_fee_currency_conflict")
            from operational_provider_balance_sar import sar_amount
            return sar_amount(value, record["fee_fx_basis"])
        full = capture_amount == record["native_gross"] and refund_amount == record["native_refunded"]
        if applied:
            # A payout allocation has no capture IDs. Do not infer which
            # partial capture it covered, or add its fee a second time.
            if not full or applied < record["gross"] or display(actual) != allocated_fee:
                raise ValueError("provider_statement_fee_settlement_conflict")
            return {**result, "complete": True, "issue": None, "evidenced": True}
        if any(item["operational"] for item in checked):
            raise ValueError("provider_statement_fee_settlement_conflict")
        result.update(actual=display(actual), evidenced=True)
        if full:
            return {**result, "complete": True, "issue": None}
        all_captures = context["captures"][record["order_id"], record["provider"]]
        disjoint = (sum(all_captures.values(), ZERO) == record["native_gross"]
                    and all(item["cover"][0] == "fact" and item["cover"][1] in all_captures for item in captures))
        if (not record["native_refunded"] and not refunds and disjoint and capture_amount > ZERO
                and estimate_basis == "average_estimate" and average_profile is not None and average_components is not None):
            uncovered = set(all_captures) - {item["cover"][1] for item in captures}
            components = statement_average_projection(average_profile, record["native_gross"] - capture_amount,
                operations=len(uncovered), operations_basis="capture_facts",
                configured_vat_rate=average_components["vat_rate"] if average_components["vat_basis"] == "configured" else None,
                configured_fixed_fee=average_components.get("configured_fixed_fee"),
                vat_conflict=average_components["vat_basis"] == "conflicting_policy")
            previous = cumulative = ZERO
            for key in ("variable", "fixed", "vat"):
                if components[key] is not None:
                    cumulative += components[key]
                    boundary = display(cumulative)
                    components[key] = boundary - previous
                    previous = boundary
            remaining = display(components["fee"]) if components["fee"] is not None else None
            components["fee"] = remaining
            return {**result, "estimated": remaining or ZERO, "average": remaining or ZERO,
                    "average_used": True, "complete": remaining is not None, "components": components,
                    "issue": None if remaining is not None else "provider_fee_estimate_unavailable"}
        return result
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError) as exc:
        code = str(exc)
        return {**result, "actual": ZERO, "evidenced": False,
                "issue": code if code.startswith("provider_statement_fee_") else REVIEW}
