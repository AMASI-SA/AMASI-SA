"""Review exact native closed-day restatements; apply only in the refresh CAS.

No source writer, approval queue, manual movement, or independently committed
financial effect. Preview is a bounded owner-scoped source/state snapshot.
"""
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal

from pymongo.read_concern import ReadConcern
from pymongo.errors import PyMongoError
import pymongo

from operational_balance_engine import (
    advertising_financial_vector, advertising_projection, advertising_sar_amount, advertising_fx_basis, amount, money,
)
from operational_balance_store import STATES, audit, digest, fail, now, remember, replay, request_key
from operational_demand_refresh import calculation_checkpoint, run_calculation
from operational_financial_activity_runtime import prepare_in_thread


SCHEMA = "operational_ad_correction_preview_v1"
ACTION = "ad_spend_correction:"
MAX_DAYS = 31
MAX_BINDINGS = 200
READ_SECONDS = 12
SOURCE_FIELDS = (
    "_id", "user_id", "provider", "ad_account_id", "date", "report_date",
    "base_spend_native", "spend_native", "currency", "currency_native",
    "source_latest_updated_at", "observed_at", "projection_timezone", "account_timezone",
    "source_close_proof", "amount_complete", "data_state",
)
BINDING_FIELDS = (
    "_id", "user_id", "platform", "integration_account_id", "platform_account_id",
    "funding_mode", "funding_policy", "currency", "timezone", "confirmed_by", "confirmed_at",
    "status", "active", "is_active", "archived", "deleted", "is_archived", "is_deleted",
    "version", "hybrid_policy", "mode", "evidence",
)
ACCOUNT_FIELDS = (
    "_id", "user_id", "provider", "mezan_integration_account_id", "external_account_id",
    "connection_status", "currency", "timezone", "display_name", "name",
)
CONTRACT_FIELDS = (
    "id", "kind", "party_id", "party_type", "currency", "direction", "business_date",
    "funding_type", "funding_mode", "wallet_fraction", "funding_policy", "ad_day_id",
    "original_currency", "timezone", "baseline_amount",
)


def validate_selection(selection):
    if not isinstance(selection, dict) or set(selection) != {"account_id", "date_from", "date_to"}:
        raise ValueError("ad_correction_selection_invalid")
    identity = selection["account_id"]
    if not isinstance(identity, str) or not 1 <= len(identity) <= 200 or identity != identity.strip():
        raise ValueError("ad_correction_selection_invalid")
    first, last = (date.fromisoformat(selection[key]) for key in ("date_from", "date_to"))
    if (first.isoformat() != selection["date_from"] or last.isoformat() != selection["date_to"]
            or not 0 <= (last - first).days < MAX_DAYS):
        raise ValueError("ad_correction_period_invalid")
    return [(first + timedelta(days=offset)).isoformat() for offset in range((last - first).days + 1)]


async def _rows(scoped, owner, collection, query, fields, limit):
    from operational_read_runtime import read_source_cursor
    cursor = scoped[collection].find({"user_id": owner, **query}, dict.fromkeys(fields, 1)).limit(limit + 1).max_time_ms(3000)
    rows = await read_source_cursor(cursor, limit + 1)
    if len(rows) > limit or any(row.get("user_id") != owner for row in rows):
        fail("ad_correction_source_scope_invalid", "تعذر إثبات نطاق مصدر الإعلان؛ لم تتغير الأرصدة")
    return rows


async def _load_native(scoped, owner, state, selection):
    from operational_ad_funding import bindings, same_account
    from operational_balance_sources import AD_SOURCES, usable
    native = await _rows(scoped, owner, "mz2_ad_account_bindings_v2", {}, BINDING_FIELDS, MAX_BINDINGS)
    projected, conflicts = bindings(state, owner, native)
    identity = selection["account_id"]
    chosen = [row for row in projected if str(row.get("_id")) == identity]
    if identity in conflicts or len(chosen) != 1:
        fail("ad_correction_binding_ambiguous", "ربط الحساب الإعلاني غير مكتمل أو متعارض")
    binding = chosen[0]
    if (not usable(binding) or not binding.get("confirmed_by") or not binding.get("confirmed_at")
            or binding.get("platform") not in AD_SOURCES
            or sum(usable(other) and bool(other.get("confirmed_by")) and bool(other.get("confirmed_at"))
                   and same_account(binding, other) for other in projected) != 1):
        fail("ad_correction_binding_ambiguous", "ربط الحساب الإعلاني غير مكتمل أو متعارض")
    provider, collection = AD_SOURCES[binding["platform"]]
    accounts = await _rows(scoped, owner, "mezan_integration_accounts_v2", {
        "mezan_integration_account_id": binding["integration_account_id"]}, ACCOUNT_FIELDS, 2)
    if (len(accounts) != 1 or accounts[0].get("provider") != provider
            or accounts[0].get("external_account_id") != binding.get("platform_account_id")
            or accounts[0].get("connection_status") != "connected"):
        fail("ad_correction_account_unavailable", "تعذر إثبات الحساب المتصل بالمصدر الأصلي")
    account = accounts[0]
    date_field = "report_date" if binding["platform"] == "snapchat" else "date"
    native_rows = await _rows(scoped, owner, collection, {
        "provider": provider, "ad_account_id": account["external_account_id"],
        date_field: {"$gte": selection["date_from"], "$lte": selection["date_to"]},
    }, SOURCE_FIELDS, MAX_DAYS * 2)
    policies = await _rows(scoped, owner, "mz2_ad_automation_policies_v2", {
        "platform": binding["platform"], "integration_account_id": binding["integration_account_id"],
    }, ("user_id", "id", "platform", "integration_account_id", "version", "binding_version", "status",
        "confirmed_by", "confirmed_at", "start_date", "wallet_fraction", "evidence"), MAX_BINDINGS)
    fx = await _rows(scoped, owner, "mz2_ad_fx_snapshots_v2", {
        "currency": account["currency"], "status": "active",
        "business_date": {"$gte": selection["date_from"], "$lte": selection["date_to"]},
    }, ("user_id", "id", "currency", "business_date", "status", "fx_rate_to_sar", "fx_source", "fx_at",
        "confirmed_by", "confirmed_at", "evidence"), MAX_DAYS * 2)
    return {"binding": binding, "account": account, "collection": collection, "date_field": date_field,
        "data": {"mz2_ad_account_bindings_v2": [binding], "mezan_integration_accounts_v2": accounts,
            "mz2_ad_automation_policies_v2": policies, "mz2_ad_fx_snapshots_v2": fx,
            "mezan_ad_account_cost_settings_v2": [], collection: native_rows}}


def _same_header(left, right):
    return (all(left.get(key) == right.get(key) for key in ("_id", "owner_id", "revision", "status", "started_at"))
        and left.get("engine", {}).get("as_of") == right.get("engine", {}).get("as_of"))


def _require_fx(row, stamp):
    if row.get("currency") == "SAR":
        return
    from operational_balance_sources import instant
    basis = advertising_fx_basis(row)
    if (not all(basis.values()) or money(basis["fx_rate"]) <= 0
            or basis["fx_date"] != row.get("business_date")
            or instant(basis["fx_at"]) > instant(stamp) or advertising_sar_amount(row) is None):
        raise ValueError("ad_correction_fx_evidence_incomplete")


async def read_context(db, owner, selection, *, state=None):
    """Source reads are serial within one snapshot; no authority/source writes."""
    from operational_balance_service import active_gate
    from operational_financial_position_state import read_financial_position_state
    from operational_supplier_invoice_preview import SnapshotReads
    validate_selection(selection)
    try:
        with pymongo.timeout(READ_SECONDS):
            async with await db.client.start_session() as session:
                async with session.start_transaction(read_concern=ReadConcern("snapshot")):
                    scoped = SnapshotReads(db, owner, session)
                    if state is None:
                        state = await read_financial_position_state(scoped, owner)
                    else:
                        header = await scoped[STATES].find_one({"_id": owner, "owner_id": owner}, {
                            "_id": 1, "owner_id": 1, "revision": 1, "status": 1, "started_at": 1, "engine.as_of": 1})
                        if header is None or not _same_header(header, state):
                            fail("ad_correction_preview_stale", "تغيرت الأرصدة؛ أعد معاينة التصحيح")
                    await active_gate(scoped, owner, state)
                    context = await _load_native(scoped, owner, state, selection)
                    return state, context
    except PyMongoError:
        fail("ad_correction_source_unavailable", "تعذرت قراءة دليل الإعلان من لقطة متسقة؛ لم تتغير الأرصدة", 503)


def build_preview(state, context, selection, stamp):
    """Pure bounded comparison, including the exact original row identities."""
    from operational_balance_sources import _advertising, instant
    days = validate_selection(selection)
    owner = state.get("owner_id")
    if not owner or state.get("_id") != owner or state.get("status") != "active":
        raise ValueError("ad_correction_state_invalid")
    out = {"ad_snapshots": [], "ad_balance_sar_basis": {}, "issues": []}
    _advertising(context["data"], out, instant(state["started_at"]), instant(stamp), state.get("source_baselines"))
    snapshots, raw_rows = {}, {}
    for snapshot in out["ad_snapshots"]:
        calculation_checkpoint()
        snapshots.setdefault(snapshot["date"], []).append(snapshot)
    for row in context["data"][context["collection"]]:
        raw_rows.setdefault(row.get(context["date_field"]), []).append(row)
    indexed = {}
    for identity, row in state.get("engine", {}).get("obligations", {}).items():
        calculation_checkpoint()
        if (row.get("kind") == "advertising" or identity.startswith("advertising:" + selection["account_id"] + ":")) and row.get("party_type") == "ad_account" and row.get("party_id") == selection["account_id"] and row.get("business_date") in days:
            indexed.setdefault(row["business_date"], {})[identity] = row
    items, private = [], {}
    for business_day in days:
        calculation_checkpoint()
        key = "advertising:" + selection["account_id"] + ":" + business_day
        prior = state.get("engine", {}).get("ad_days", {}).get(key)
        item = {"business_date": business_day, "ad_day_id": key, "status": "blocked", "can_approve": False,
                "currency": context["account"].get("currency"), "issues": [], "parts": []}
        material = {"prior_day": prior, "old_rows": indexed.get(business_day, {}),
                    "native_rows": raw_rows.get(business_day, []), "snapshots": snapshots.get(business_day, [])}
        private[business_day] = material
        try:
            if prior is None or prior.get("closed") is not True:
                raise ValueError("ad_correction_saved_closed_day_required")
            if len(snapshots.get(business_day, [])) != 1 or len(raw_rows.get(business_day, [])) != 1:
                raise ValueError("ad_correction_native_day_missing_or_duplicated")
            snapshot = snapshots[business_day][0]
            raw = raw_rows[business_day][0]
            if not raw.get("_id") or not all(snapshot.get(field) is True for field in ("complete", "closed", "day_ended")):
                raise ValueError("ad_correction_close_proof_incomplete")
            if instant(snapshot["observed_at"]) < instant(prior["observed_at"]):
                raise ValueError("ad_correction_native_observation_stale")
            if instant(context["binding"]["confirmed_at"]) > instant(stamp):
                raise ValueError("ad_correction_binding_not_effective")
            projected = advertising_projection(snapshot, state)
            old = indexed.get(business_day, {})
            active = {identity: row for identity, row in old.items()
                if not (row.get("superseded") is True and money(row.get("expected")) == 0 and money(row.get("confirmed")) == 0)}
            if set(active) != set(projected):
                raise ValueError("ad_correction_saved_parts_incomplete")
            for identity, row in active.items():
                if (any(row.get(field) != projected[identity].get(field) for field in CONTRACT_FIELDS)
                        or money(row.get("expected")) != 0 or row.get("ad_total_amount") != prior["amount"]):
                    raise ValueError("ad_correction_saved_contract_changed")
                # A foreign-currency conversion change needs its own review.
                _require_fx(row, stamp)
                _require_fx(projected[identity], stamp)
                if advertising_fx_basis(row) != advertising_fx_basis(projected[identity]):
                    raise ValueError("ad_correction_fx_basis_changed")
            first = next(iter(projected.values()))
            if (prior.get("funding_type") != first["funding_mode"]
                    or Decimal(prior.get("wallet_fraction", "-1")) != Decimal(first["wallet_fraction"])
                    or prior.get("timezone") != first["timezone"] or prior.get("funding_policy") != first.get("funding_policy")
                    or sum((money(row["confirmed"]) for row in active.values()), Decimal(0)) != money(prior["amount"])):
                raise ValueError("ad_correction_saved_contract_changed")
            changed = advertising_financial_vector(active) != advertising_financial_vector(projected)
            item.update(status="correction_required" if changed else "unchanged", can_approve=changed,
                before_amount=prior["amount"], after_amount=first["ad_total_amount"],
                difference=amount(money(first["ad_total_amount"]) - money(prior["amount"])),
                timezone=first["timezone"], native_observed_at=snapshot["observed_at"],
                native_amount=snapshot["source_cumulative_amount"], close_proof_fingerprint=raw["source_close_proof"]["fingerprint"],
                fx_basis=advertising_fx_basis(first),
                parts=[{"id": identity, "funding_type": row["funding_type"], "before": active[identity]["confirmed"],
                    "after": row["confirmed"], "difference": amount(money(row["confirmed"]) - money(active[identity]["confirmed"])),
                    "before_sar": advertising_sar_amount(active[identity]), "after_sar": advertising_sar_amount(row),
                    "sar_difference": (amount(money(advertising_sar_amount(row)) - money(advertising_sar_amount(active[identity])))
                        if advertising_sar_amount(row) is not None and advertising_sar_amount(active[identity]) is not None else None)}
                    for identity, row in sorted(projected.items())])
            material["projected"] = projected
            material["snapshot"] = snapshot
        except (ValueError, TypeError, KeyError, ArithmeticError) as error:
            reason = str(error)
            item["issues"] = [reason if reason.startswith("ad_correction_") else "ad_correction_evidence_invalid"]
        items.append(item)
    header = {key: state.get(key) for key in ("_id", "owner_id", "revision", "status", "started_at")}
    header["as_of"] = state.get("engine", {}).get("as_of")
    material = {"schema": SCHEMA, "state": header, "selection": selection,
        "binding": context["binding"], "account": context["account"], "days": private}
    result = {"schema": SCHEMA, "read_only": True, "posting_allowed": False, "selection": deepcopy(selection),
        "revision": state["revision"], "as_of": header["as_of"], "started_at": state["started_at"],
        "previewed_at": stamp, "preview_hash": digest(material), "items": items,
        "can_approve": any(item["can_approve"] for item in items), "source_completeness_verified": False}
    return result, private


async def preview(db, owner, selection, *, clock=None):
    from operational_financial_position_state import require_current_snapshot
    state, context = await read_context(db, owner, selection)
    result, _ = await prepare_in_thread(build_preview, state, context, selection, clock or now())
    await require_current_snapshot(db, owner, state)
    return result


def approval_receipt(state, approval):
    return replay(state, ACTION + approval["actor_id"], approval["payload"])


async def saved_approval(db, owner, actor, request_id, expected_preview_hash, *, payload=None):
    """Read one stored receipt after an uncertain response, without calculation."""
    if actor != owner:
        fail("ad_correction_owner_required", "إيصال التصحيح متاح للمالك الحالي فقط", 403)
    key = request_key(ACTION + actor, request_id)
    stored = await db[STATES].find_one({"_id": owner, "owner_id": owner}, {
        "_id": 1, "owner_id": 1, "revision": 1, "engine.as_of": 1, "requests." + key: 1})
    old = (stored or {}).get("requests", {}).get(key)
    result = {"schema": "operational_ad_correction_status_v1", "request_id": request_id,
        "status": "not_recorded", "receipt": None, "revision": (stored or {}).get("revision"),
        "as_of": (stored or {}).get("engine", {}).get("as_of")}
    if old is not None:
        receipt = old.get("result", {})
        if (payload is not None and old.get("hash") != digest(payload)
                or receipt.get("request_id") != request_id or receipt.get("preview_hash") != expected_preview_hash):
            fail("operational_request_conflict", "رقم الاعتماد مستخدم بمحتوى آخر؛ راجع إيصال العملية")
        if receipt.get("schema") != "operational_ad_correction_receipt_v1":
            fail("ad_correction_receipt_invalid", "تعذر التحقق من إيصال التصحيح", 503)
        result.update(status="applied", receipt=receipt)
    return result


def _check_approval(state, approval, reviewed):
    payload = approval["payload"]
    if (approval["actor_id"] != state["owner_id"]
            or payload.get("expected_session_scope") != digest([state["owner_id"], approval["actor_id"]])):
        fail("ad_correction_owner_required", "اعتماد تصحيح الإعلان متاح للمالك الحالي فقط", 403)
    if (payload.get("expected_revision") != state["revision"]
            or payload.get("expected_preview_hash") != reviewed["preview_hash"]):
        fail("ad_correction_preview_stale", "تغير المصدر أو الأرصدة؛ أعد المعاينة قبل اعتماد التصحيح")
    selected = payload.get("days")
    eligible = {item["business_date"] for item in reviewed["items"] if item["can_approve"]}
    if (payload.get("confirm_reviewed") is not True or not isinstance(selected, list) or not selected
            or len(selected) != len(set(selected)) or not set(selected) <= eligible):
        fail("ad_correction_review_required", "اختر الأيام ذات الدليل المكتمل وأكد مراجعة الفروق", 422)


async def prepare_approval(db, owner, state, sources, approval, stamp):
    """Inject only server-verified flags into the already collected exact rows."""
    payload = approval["payload"]
    _, native = await read_context(db, owner, payload["selection"], state=state)
    reviewed, private = await run_calculation(build_preview, state, native, payload["selection"], stamp)
    _check_approval(state, approval, reviewed)
    chosen = {}
    for business_day in payload["days"]:
        material = private[business_day]
        snapshot = material["snapshot"]
        identity = (snapshot["account_id"], snapshot["date"])
        collected = [row for row in sources.get("ad_snapshots", [])
                     if (row.get("account_id"), row.get("date")) == identity]
        if len(collected) != 1 or digest(collected[0]) != digest(snapshot):
            fail("ad_correction_source_changed", "تغير مصدر اليوم أثناء جمع البيانات؛ أعد المعاينة")
        correction_id = digest([owner, approval["actor_id"], payload["request_id"], reviewed["preview_hash"], business_day])
        chosen[identity] = {**snapshot, "correction_approved": True, "correction_id": correction_id}
        material["correction_id"] = correction_id
    updated = {**sources, "ad_snapshots": [chosen.get((row.get("account_id"), row.get("date")), row)
                                          for row in sources.get("ad_snapshots", [])]}
    return updated, {"reviewed": reviewed, "private": private}


def record_approval(before, result, approval, plan, stamp):
    """Postconditions, owner audit and replay receipt share the financial CAS."""
    payload = approval["payload"]
    corrected = []
    for business_day in payload["days"]:
        calculation_checkpoint()
        material = plan["private"][business_day]
        snapshot, projected = material["snapshot"], material["projected"]
        key = "advertising:" + snapshot["account_id"] + ":" + business_day
        saved_day = result["engine"]["ad_days"].get(key, {})
        if (saved_day.get("closed") is not True or saved_day.get("amount") != amount(money(snapshot["amount"]))
                or saved_day.get("observed_at") != snapshot["observed_at"]):
            fail("ad_correction_result_mismatch", "لم يطابق التصحيح المعاينة؛ لم تتغير الأرصدة")
        for identity, expected in projected.items():
            actual = result["engine"]["obligations"].get(identity, {})
            # The refresh may update an account's display name independently;
            # every financial/source field must still match the reviewed part.
            if {field: value for field, value in actual.items() if field != "name"} != expected:
                fail("ad_correction_result_mismatch", "لم يطابق التصحيح المعاينة؛ لم تتغير الأرصدة")
        fact = result["engine"]["facts"].get("ad_correction:" + material["correction_id"])
        if fact != {"day": key, "amount": amount(money(snapshot["amount"])), "parts": advertising_financial_vector(projected)}:
            fail("ad_correction_result_mismatch", "تعذر إثبات واقعة التصحيح؛ لم تتغير الأرصدة")
        corrected.append({"business_date": business_day, "ad_day_id": key, "correction_id": material["correction_id"],
            "currency": snapshot["currency"], "native_observed_at": snapshot["observed_at"],
            "source_digest": digest(material["native_rows"]), "before": deepcopy(material["old_rows"]), "after": deepcopy(projected)})
    if (before.get("openings", {}) != result.get("openings", {})
            or result.get("movements", [])[:len(before.get("movements", []))] != before.get("movements", [])
            or any(result.get("requests", {}).get(key) != value for key, value in before.get("requests", {}).items())):
        fail("ad_correction_history_changed", "تعذر التحقق من حفظ الحركات السابقة؛ لم تتغير الأرصدة")
    receipt = {"schema": "operational_ad_correction_receipt_v1", "request_id": payload["request_id"],
        "preview_hash": payload["expected_preview_hash"], "account_id": payload["selection"]["account_id"],
        "previous_revision": before["revision"], "calculation_revision": before["revision"] + 1,
        "applied_at": stamp, "days": corrected,
        "reviewed_changes": [deepcopy(item) for item in plan["reviewed"]["items"] if item["business_date"] in payload["days"]],
        "manual_history_preserved": True, "native_sources_changed": False}
    audit(result, approval["actor_id"], "ad_spend_correction_approved", None, receipt,
          payload["reason"], "native_ad_review", stamp)
    remember(result, ACTION + approval["actor_id"], payload, receipt)
    return receipt


async def verify_before_commit(db, owner, before, after, approval):
    """Packing cannot silently widen an old approval to a newer native value."""
    from operational_demand_refresh import demand_context
    import time
    context = demand_context()
    def require_current_request():
        # No aggregate CAS has started at this hook. A departure/deadline during
        # its source validation can still reject safely; a started CAS retains
        # the unchanged joined/unknown-outcome behavior of run_commit.
        if context is not None and context.cancelled.is_set():
            fail("operational_refresh_cancelled", "أُلغي اعتماد التصحيح قبل حفظ الأرصدة", 499)
        if context is not None and context.read_deadline is not None and time.monotonic() >= context.read_deadline:
            fail("operational_refresh_timeout", "انتهت مهلة اعتماد التصحيح قبل حفظ الأرصدة", 408)
    require_current_request()
    _, native = await read_context(db, owner, approval["payload"]["selection"], state=before)
    # The protected commit already owns its task. Preparation retains the same
    # cooperative checks; errors propagate without a CAS or an invented result.
    reviewed, _ = await run_calculation(build_preview, before, native,
        approval["payload"]["selection"], now())
    _check_approval(before, approval, reviewed)
    receipt = approval_receipt(after, approval)
    if receipt is None or receipt.get("previous_revision") != before["revision"]:
        fail("ad_correction_result_mismatch", "تعذر إثبات اعتماد التصحيح قبل الحفظ")
    require_current_request()
    return require_current_request
