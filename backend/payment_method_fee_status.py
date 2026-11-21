"""Read-only current fee setup, using the same tariff authority as balances.

This answers whether a method has a current tariff. It does not certify a
historical order's payment, plan, refund or fee and never writes financial data.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from fastapi import HTTPException
from pymongo.errors import PyMongoError

from accounting_onboarding_ssot import get_fee_source_mode, list_fee_policies
from operational_configured_provider_fees import has_capture_policies
from operational_provider_fees import day, provider_projection
from payment_method_setup import load_payment_method_setup as load_saved_method_setup
from payment_methods import normalize_payment_method

RIYADH = timezone(timedelta(hours=3))
POLICY_PROVIDERS = frozenset({"salla", "tabby", "tamara", "emkan"})


async def load_fee_setup_context(db, owner):
    """Use the bounded, owner-scoped policy reader and retain read failures."""
    try:
        policies, source_mode = await asyncio.gather(
            list_fee_policies(db, owner), get_fee_source_mode(db, owner),
        )
        if any(not isinstance(row, dict) or row.get("user_id", owner) != owner for row in policies):
            raise ValueError("provider_fee_policy_owner_mismatch")
        return {"policies": policies, "source_mode": source_mode, "complete": True}
    except (PyMongoError, TimeoutError, HTTPException, ValueError, TypeError):
        return {"policies": [], "source_mode": None, "complete": False}


def fee_setup_revision(context):
    return sha256(json.dumps(context, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), default=str).encode()).hexdigest()


def current_fee_setup(directory, context, current_day):
    result = deepcopy(directory)
    policies = context["policies"]
    for item in result["items"]:
        scope = item.get("fee_scope") or {}
        provider = scope.get("provider")
        if provider not in POLICY_PROVIDERS:
            continue  # PayPal, COD and bank keep their existing saved-fee policy.
        item["label"] = normalize_payment_method(item["name"])[1] or item["name"]
        if not context["complete"]:
            item.update(status="unverified", editable=False)
            for field in ("commission_percent", "fixed_fee", "vat_percent"):
                item[field] = None
            continue
        governed = context["source_mode"] == "mz2_policies" or has_capture_policies(policies, provider)
        if not governed:
            continue
        item.update(fee_source="mz2_policies", editable=False)
        item.pop("inherits_from", None)
        for field in ("commission_percent", "fixed_fee", "vat_percent"):
            item[field] = None
        payment = {"provider": provider, "currency": "SAR", "gross": "100.00",
                   "payment_method": scope.get("payment_method")}
        try:
            projection = provider_projection(payment, policies, current_day)
        except (ValueError, TypeError, KeyError, ArithmeticError) as error:
            # An overlapping or invalid tariff needs review, not another
            # duplicate commission. Missing current scope needs a tariff.
            try:
                applicable = [row for row in policies if row.get("provider") == provider
                    and row.get("currency") == "SAR" and row.get("status") == "active"
                    and row.get("event_type", "capture") == "capture"
                    and day(row["effective_from"]) <= current_day
                    and (not row.get("effective_to") or current_day <= day(row["effective_to"]))
                    and (not scope.get("payment_method") or row.get("payment_method") in (
                        None, "", scope["payment_method"]))]
            except (ValueError, TypeError, KeyError):
                applicable = [None]  # Malformed policy data must stay visibly unresolved.
            if str(error) == "provider_payment_plan_required":
                plans = {str(row["plan_id"]) for row in applicable
                         if isinstance(row, dict) and row.get("plan_id") not in (None, "")}
                try:
                    priced = [provider_projection({**payment, "plan_id": plan}, policies, current_day)
                              for plan in sorted(plans)]
                except (ValueError, TypeError, KeyError, ArithmeticError) as plan_error:
                    error = plan_error
                else:
                    if priced:
                        # This directory has no individual order/plan. Valid
                        # configured plans are not missing setup; each real
                        # order still has to prove its own plan to be priced.
                        item.update(status="configured", configuration_kind="plan_specific",
                                    plan_count=len(plans), policy_ids=sorted({row["policy_id"] for row in priced}))
                        continue
            item.update(status="needs_review" if applicable else "needs_commission",
                        reason_code=str(error))
        else:
            item.update(status="configured", policy_id=projection["policy_id"])
    result["pending_count"] = sum(item["status"] == "needs_commission" for item in result["items"])
    result["review_count"] = sum(item["status"] == "needs_review" for item in result["items"])
    result["complete"] = result["complete"] and context["complete"]
    result["fee_revision"] = fee_setup_revision(context)
    result["as_of_date"] = current_day.isoformat()
    return result


async def load_payment_method_setup(db, owner, *, settings=None, fee_context=None, current_day=None):
    directory = await load_saved_method_setup(db, owner, settings=settings)
    context = fee_context if fee_context is not None else await load_fee_setup_context(db, owner)
    return current_fee_setup(directory, context, current_day or datetime.now(RIYADH).date())
