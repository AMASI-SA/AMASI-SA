"""Owner review metadata only; the engine revalidates the canonical order on refresh."""
from operational_balance_service import active_gate
from operational_balance_store import RECEIPTS, audit, digest, fail, mutate, now, read, remember, replay
from operational_provider_history import HISTORICAL, SETTLED, REVIEWS, preview_row


def _candidates(state):
    result, repeated = {}, set()
    for issue in state.get("engine", {}).get("issues", []):
        page = issue.get("order_page_balance_review")
        if issue.get("code") not in (HISTORICAL, SETTLED) or not page or not issue.get("history_proof_hash"):
            continue
        row = {**page, "owner_id": state["owner_id"], "created_at": issue["history_created_at"],
               "proof_hash": issue["history_proof_hash"]}
        oid = row["order_id"]
        if issue.get("source_id") != "provider:" + oid or oid in result:
            repeated.add(oid)
        result[oid] = (row, issue["code"])
    return {k:v for k,v in result.items() if k not in repeated}


async def candidates(db, owner, actor):
    if actor != owner:
        fail("provider_history_owner_required", "المطابقة متاحة للمالك فقط", 403)
    state = await read(db, owner)
    await active_gate(db, owner, state)
    rows = []
    for oid, (row, code) in _candidates(state).items():
        item = preview_row(state, row, code)
        old = state.get(REVIEWS, {}).get(digest(oid))
        item["awaiting_refresh"] = bool(old and old.get("preview_hash") == item["preview_hash"])
        rows.append(item)
    return {"items": rows, "session_scope": digest([owner, actor]), "as_of":state.get("engine",{}).get("as_of")}


async def approve(db, owner, actor, payload):
    if actor != owner or payload["expected_session_scope"] != digest([owner, actor]):
        fail("provider_history_owner_required", "تغير الحساب. أعد فتح المطابقة بصلاحية المالك.", 403)
    if payload["confirmed_outstanding"] is not True:
        fail("provider_history_evidence_required", "أكّد مطابقة المبلغ المستحق وكشف المنصة والافتتاحي.", 422)
    async def apply(state):
        await active_gate(db, owner, state)
        previous = replay(state, "provider_history_review", payload)
        if previous:
            return previous
        current = _candidates(state).get(payload["order_id"])
        if not current:
            fail("provider_history_review_stale", "تغيرت حالة الطلب. أعد قراءة القائمة.")
        row, code = current
        item = preview_row(state, row, code)
        if item["blocks"] or item["preview_hash"] != payload["expected_preview_hash"]:
            fail("provider_history_review_stale", "تغير الدليل أو يحتاج السداد إلى مطابقة. أعد قراءة القائمة.")
        proof = await db[RECEIPTS].find_one({"_id":payload["evidence_id"], "owner_id":owner}, {"_id":1, "sha256":1})
        if not proof or not proof.get("sha256") or digest([owner,proof["sha256"]]) != payload["evidence_id"]:
            fail("provider_history_evidence_required", "أرفق كشف المنصة المثبت لاستحقاق هذا الطلب.", 422)
        ident = digest(row["order_id"])
        old = state.setdefault(REVIEWS, {}).get(ident)
        if old and old.get("preview_hash") == item["preview_hash"]:
            fail("provider_history_already_reviewed", "هذا الإثبات معتمد وينتظر إعادة الاحتساب.")
        saved = {"owner_id":owner, "order_id":row["order_id"], "confirmed_by":actor, "confirmed_at":now(),
                 "preview_hash":item["preview_hash"], "proof_hash":row["proof_hash"], "evidence_id":payload["evidence_id"],
                 "statement_reference":payload["statement_reference"]}
        state[REVIEWS][ident] = saved
        audit(state, actor, "provider_history_review", old, saved, "مطابقة مستحق الطلب مع كشف المنصة والافتتاحي", row["order_number"])
        response = {"order_id":row["order_id"], "awaiting_refresh":True, "session_scope":digest([owner,actor])}
        remember(state,"provider_history_review",payload,response)
        return response
    return await mutate(db, owner, apply)
