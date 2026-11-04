"""Owner-verified Salla closing balances, projected as customer liabilities.

The closing already includes wallet redemptions. Order tender observations do
not change it, and each newer source statement replaces the preceding closing.
"""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import re

from operational_balance_store import audit, digest, fail, mutate, now, read, remember, replay


SNAPSHOTS = "customer_wallet_snapshots"
ACTION = "customer_wallet_snapshot"
NAME = "محافظ العملاء"


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.utcoffset() is None:
            raise ValueError("timezone_required")
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("customer_wallet_timestamp_invalid") from None


def _amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,18}(?:\.\d{1,2})?", value):
        raise ValueError("customer_wallet_amount_invalid")
    return format(Decimal(value), ".2f")


def _evidence(payload):
    if not isinstance(payload, dict):
        raise ValueError("customer_wallet_source_invalid")
    currency = payload.get("currency")
    merchant = payload.get("merchant_id")
    reference = payload.get("source_reference")
    source_hash = payload.get("source_hash")
    if (not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency)
            or not isinstance(merchant, str) or not merchant.strip() or len(merchant) > 200
            or not isinstance(reference, str) or not 3 <= len(reference.strip()) <= 500
            or (source_hash is not None and not re.fullmatch(r"[a-f0-9]{64}", str(source_hash)))):
        raise ValueError("customer_wallet_source_invalid")
    evidence = {"merchant_id": merchant.strip(), "currency": currency,
                "as_of": _instant(payload.get("as_of")).isoformat(),
                "remaining_balance": _amount(payload.get("remaining_balance")),
                "source_reference": reference.strip(), "source_hash": source_hash, "flow": None}
    flow = payload.get("flow")
    if flow is not None:
        if not isinstance(flow, dict) or flow.get("opening_verified") is not True:
            raise ValueError("customer_wallet_opening_unverified")
        normalized = {key: _amount(flow.get(key)) for key in ("opening_balance", "added", "used", "other_deductions")}
        normalized.update(period_start=_instant(flow.get("period_start")).isoformat(), opening_verified=True)
        if _instant(normalized["period_start"]) > _instant(evidence["as_of"]):
            raise ValueError("customer_wallet_flow_period_invalid")
        closing = (Decimal(normalized["opening_balance"]) + Decimal(normalized["added"])
                   - Decimal(normalized["used"]) - Decimal(normalized["other_deductions"]))
        if closing != Decimal(evidence["remaining_balance"]):
            raise ValueError("customer_wallet_flow_conflict")
        evidence["flow"] = normalized
    return evidence


async def _binding(db, owner):
    rows = await db["salla_integrations"].find({"user_id": owner}, {
        "_id": 0, "user_id": 1, "store_id": 1, "currency": 1, "store_currency": 1}).to_list(2)
    if len(rows) != 1 or rows[0].get("user_id") != owner or not rows[0].get("store_id"):
        return None
    row = rows[0]
    return {"merchant_id": str(row["store_id"]), "currency": row.get("currency") or row.get("store_currency")}


def project(state, *, cutoff=None, require_cutoff=False):
    """Read dated evidence without posting cash, claims, or wallet redemptions."""
    balances, issues = {}, []
    for currency, saved in (state.get(SNAPSHOTS) or {}).items():
        row = {"name": NAME, "currency": currency, "remaining": None, "as_of": None,
               "source_reference": None, "merchant_id": None, "flow": None, "complete": False}
        try:
            evidence = _evidence(saved)
            if (currency != evidence["currency"] or saved.get("confirmed_by") != state["owner_id"]
                    or saved.get("evidence_hash") != digest(evidence)
                    or _instant(evidence["as_of"]) > _instant(saved.get("confirmed_at"))):
                raise ValueError("customer_wallet_snapshot_invalid")
            row.update({key: evidence[key] for key in ("as_of", "source_reference", "merchant_id", "source_hash", "flow")})
            if require_cutoff and cutoff is None:
                raise ValueError("customer_wallet_report_cutoff_missing")
            if cutoff is not None and _instant(evidence["as_of"]) > _instant(cutoff):
                raise ValueError("customer_wallet_snapshot_after_cutoff")
            row.update(remaining=evidence["remaining_balance"], complete=True, id=saved["id"],
                       authority="owner_verified_salla_report", read_only=True)
        except (KeyError, TypeError, ValueError):
            issues.append({"code": "customer_wallet_balance_unavailable", "currency": currency,
                           "source_id": "customer_wallet", "message": "تعذر اعتماد رصيد محافظ العملاء لهذا التاريخ؛ راجع كشف سلة الموثق."})
        balances[currency] = row
    coverage = state.get("engine", {}).get("customer_tender_coverage", {})
    wallet_used = any(row.get("method") == "wallet" and row.get("orders", 0)
                      for rows in coverage.get("by_currency", {}).values() for row in rows)
    if not balances and wallet_used:
        issues.append({"code": "customer_wallet_balance_missing", "source_id": "customer_wallet",
                       "message": "ظهر استخدام لمحافظ العملاء؛ الرصيد المتبقي المستحق لهم يحتاج إلى كشف سلة موثق."})
    return balances, issues


async def context(db, owner, actor):
    if actor != owner:
        fail("customer_wallet_owner_required", "توثيق رصيد محافظ العملاء متاح للمالك فقط", 403)
    state = await read(db, owner)
    binding = await _binding(db, owner)
    balances, issues = project(state)
    return {"status": state["status"], "session_scope": digest([owner, actor]),
            "merchant_id": binding["merchant_id"] if binding else None,
            "currency": binding["currency"] if binding else None,
            "can_manage": state["status"] == "active" and binding is not None,
            "balances": balances, "issues": issues}


async def save(db, owner, actor, payload, *, clock=None):
    if actor != owner:
        fail("customer_wallet_owner_required", "توثيق رصيد محافظ العملاء متاح للمالك فقط", 403)
    if payload.get("expected_session_scope") != digest([owner, actor]):
        fail("operational_actor_scope_changed", "تغير ارتباط الحساب؛ حدّث الصفحة قبل الحفظ", 403)
    stamp = clock or now()
    try:
        evidence = _evidence(payload)
        if _instant(evidence["as_of"]) > _instant(stamp):
            raise ValueError("customer_wallet_future_snapshot")
    except ValueError as exc:
        fail(str(exc), "تحقق من مبلغ الرصيد وتاريخ كشف سلة ومطابقة حركة المحفظة", 422)

    async def change(state):
        if state.get("owner_id") != owner:
            fail("customer_wallet_owner_mismatch", "مصدر المحفظة لا يطابق المالك", 403)
        binding = await _binding(db, owner)
        if (not binding or binding["merchant_id"] != evidence["merchant_id"]
                or (binding["currency"] and binding["currency"] != evidence["currency"])):
            fail("customer_wallet_merchant_mismatch", "الكشف لا يطابق متجر سلة المرتبط وعملته")
        cached = replay(state, ACTION, payload)
        if cached is not None:
            return cached
        from operational_balance_service import active_gate
        await active_gate(db, owner, state)
        if _instant(evidence["as_of"]) < _instant(state["started_at"]):
            fail("customer_wallet_before_start", "تاريخ كشف المحفظة يسبق بدء التشغيل")
        old = state.get(SNAPSHOTS, {}).get(evidence["currency"])
        proof_hash = digest(evidence)
        if old:
            if old["merchant_id"] != evidence["merchant_id"] or _instant(old["as_of"]) > _instant(evidence["as_of"]):
                fail("customer_wallet_snapshot_stale", "الكشف أقدم من الرصيد المحفوظ أو يخص متجرًا آخر")
            if _instant(old["as_of"]) == _instant(evidence["as_of"]):
                if old.get("evidence_hash") != proof_hash:
                    fail("customer_wallet_snapshot_conflict", "يوجد كشف مختلف محفوظ للتاريخ نفسه")
                result = deepcopy(old)
                remember(state, ACTION, payload, result)
                return result
        saved = {**evidence, "id": digest([owner, ACTION, proof_hash]), "evidence_hash": proof_hash,
                 "confirmed_by": actor, "confirmed_at": stamp}
        state.setdefault(SNAPSHOTS, {})[evidence["currency"]] = saved
        audit(state, actor, ACTION, old, saved, evidence["source_reference"], "salla_wallet_report", stamp)
        remember(state, ACTION, payload, saved)
        return deepcopy(saved)

    return await mutate(db, owner, change)
