"""Snapshot preview and owner-only authorization; reconciliation books the base fee."""
from pymongo.errors import PyMongoError
from pymongo.read_concern import ReadConcern

from operational_balance_service import active_gate
from operational_balance_sources import collect_sources
from operational_balance_store import audit, check_authorization, digest, fail, mutate, now, read, remember, replay
from operational_historical_shipping_fee import APPROVALS, review, saved_for
from operational_supplier_invoice_preview import SnapshotReads


async def _owner(db, owner, actor):
    user = await db.users.find_one({"id": actor}, {"_id": 0, "role": 1, "disabled": 1, "is_active": 1, "deleted_at": 1})
    if actor != owner or not user or user.get("role") != "owner" or user.get("disabled") or user.get("deleted_at") or user.get("is_active") is False:
        fail("historical_shipping_owner_required", "مطابقة الأجور التاريخية متاحة للمالك فقط", 403)
    await check_authorization()


async def _preview(db, owner, actor, number, stamp):
    try:
        async with await db.client.start_session() as session:
            async with session.start_transaction(read_concern=ReadConcern("snapshot")):
                scoped = SnapshotReads(db, owner, session)
                state = await read(scoped, owner)
                await active_gate(scoped, owner, state)
                sources = await collect_sources(scoped, owner, state["started_at"], stamp, operational_state=state,
                                                historical_fee_order=number)
                hits = [p for p in sources.get("historical_fee_candidates", []) if p["order_number"] == number]
                p = hits[0] if len(hits) == 1 else None
                saved = saved_for(state, number)
                openings, payouts, issues = review(state, p)
                native_conflict = sources.get("historical_fee_native_conflicts", {}).get(number)
                if native_conflict:
                    issues.append(native_conflict)
                if not p:
                    for entry in sources["issues"]:
                        details = entry.get("driver_fee_review") or entry.get("courier_fee_review") or {}
                        action = entry.get("required_action")
                        if details.get("order_number") == number and action and action not in issues:
                            issues.append(action)
                applied = saved and state.get("engine", {}).get("facts", {}).get(
                    "historical_base_fee:" + saved["candidate"]["order_id"])
                same_approval = saved and saved["candidate"] == p and (
                    saved["opening_payout_hash"] == digest([openings, payouts]))
                if applied or same_approval:
                    issues.append("سبق اعتماد أجرة هذا الطلب.")
                result = {"order_number": number, "candidate": p, "openings": openings, "payouts": payouts,
                          "issues": issues, "can_approve": bool(p and not issues), "already_approved": bool(applied or same_approval),
                          "session_scope": digest([owner, actor])}
                result["preview_hash"] = digest([state["revision"], result])
                return state, result
    except PyMongoError:
        fail("historical_shipping_snapshot_unavailable", "تعذرت قراءة مصادر الأجرة من لقطة متسقة", 503)
    except (TypeError, KeyError, ValueError, ArithmeticError):
        fail("historical_shipping_source_invalid", "تحتاج بيانات التسليم أو السداد إلى مطابقة")


async def preview(db, owner, actor, number, *, clock=None):
    await _owner(db, owner, actor)
    state, result = await _preview(db, owner, actor, number, clock or now())
    await active_gate(db, owner, state)
    await _owner(db, owner, actor)
    return result


async def approve(db, owner, actor, payload, *, clock=None):
    await _owner(db, owner, actor)
    if payload.get("expected_session_scope") != digest([owner, actor]):
        fail("operational_actor_scope_changed", "تغير الحساب؛ أعد المعاينة")
    if payload.get("confirm_opening_and_payouts") is not True or len(str(payload.get("evidence_ref") or "").strip()) < 3:
        fail("historical_shipping_confirmation_required", "يلزم مرجع وتأكيد مراجعة الافتتاح والسداد السابق", 422)
    number, stamp = payload["order_number"], clock or now()
    action = "approve_historical_shipping_fee:" + actor

    async def apply(state):
        await _owner(db, owner, actor)
        old = replay(state, action, payload)
        if old is not None:
            return old
        await active_gate(db, owner, state)
        snapshot, check = await _preview(db, owner, actor, number, stamp)
        if (not check["can_approve"] or digest(state) != digest(snapshot)
                or payload["expected_preview_hash"] != check["preview_hash"]):
            fail("historical_shipping_preview_stale", "تغيرت المطابقة أو تعذر إثباتها؛ أعد المعاينة")
        record = {"candidate": check["candidate"], "approved_by": actor, "approved_at": stamp,
                  "evidence_ref": payload["evidence_ref"], "opening_payout_hash": digest([check["openings"], check["payouts"]])}
        await _owner(db, owner, actor)
        await active_gate(db, owner, state)
        previous = saved_for(state, number)
        state.setdefault(APPROVALS, {})[digest(number)] = record
        audit(state, actor, "approve_historical_shipping_fee", previous, record, payload["evidence_ref"],
              "historical_shipping_fee_review", stamp)
        result = {"ok": True, "order_number": number, "obligation_id": record["candidate"]["obligation_id"],
                  "pending_reconciliation": True}
        remember(state, action, payload, result)
        return result

    result = await mutate(db, owner, apply)
    await _owner(db, owner, actor)
    return result
