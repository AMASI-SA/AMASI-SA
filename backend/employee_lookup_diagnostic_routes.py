"""Iter-250b · P1.5.n — Employee Lookup Forensic (STRICT READ-ONLY).

Diagnostic endpoint to investigate the error:
    "لا يمكن إنشاء قيد على موظف غير موجود (entity_id=...)"
emitted by `ledger_core.create_entry()` guard (Iter-226).

Background
----------
The codebase stores employees in TWO collections:

    * `operating_salaries`   ← used by expenses, liabilities, custody,
                                corrections, financial_position …
                                (modern primary storage)
    * `employees`            ← legacy. Used by `audit_routes` and the
                                guard in `ledger_core.py`.

If the merchant creates a custody (عهدة) on an employee that lives only
in `operating_salaries`, the guard's `db.employees.find_one()` returns
None ⇒ HTTPException("لا يمكن إنشاء قيد على موظف غير موجود …").

This endpoint surfaces, side-by-side, where exactly the entity_id is
(or isn't) and which collections reference it — so we can prescribe
the correct fix WITHOUT touching the data.

Endpoint
--------
    GET /api/audit/employee-lookup
        ?entity_id=<uuid>           REQUIRED
        &name_hint=<arabic-name>    OPTIONAL · default "عزوز"

STRICT READ-ONLY · NO writes · NO migration · NO cleanup.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query


# Collections we care about. We probe each defensively — if it doesn't
# exist on this deployment we silently skip and report the failure
# under `probe_errors`.
EMPLOYEE_LIKE_COLLECTIONS = [
    "operating_salaries",
    "employees",
    "employees_archive",
    "employees_legacy",
]

# Ledger-style collections that may reference the entity_id.
LEDGER_REFERENCING_COLLECTIONS = [
    "general_ledger",
    "liabilities",
    "account_transactions",
    "expenses",                    # custody/expense rows
    "operating_salary_payments",   # legacy salary payouts
    "salary_payments",             # alt naming
]

# Common ID fields an employee record may be addressed by.
ID_FIELDS = ["id", "employee_id", "external_id", "legacy_id", "uuid", "_id"]


def _r(n) -> float:
    return round(float(n or 0), 2)


def _strip_id(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Strip Mongo `_id` (ObjectId) so the result is JSON-serialisable."""
    if not doc:
        return None
    out = dict(doc)
    out.pop("_id", None)
    return out


async def _safe_count(db, coll_name: str, query: Dict[str, Any]) -> int:
    try:
        return await db[coll_name].count_documents(query)
    except Exception:
        return -1   # signals "collection missing / probe failed"


async def _safe_find(
    db,
    coll_name: str,
    query: Dict[str, Any],
    limit: int = 50,
    projection: Optional[Dict[str, int]] = None,
) -> List[Dict[str, Any]]:
    try:
        cursor = db[coll_name].find(query, projection).limit(limit)
        out = []
        async for r in cursor:
            out.append(_strip_id(r))
        return out
    except Exception:
        return []


def make_employee_lookup_diagnostic_router(db, current_user):
    router = APIRouter(tags=["diagnostics", "employee-lookup-forensic"])

    @router.get("/audit/employee-lookup")
    async def employee_lookup(
        entity_id: str = Query(...),
        name_hint: str = Query("عزوز"),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]
        probe_errors: List[str] = []

        # ── Section 1 ──────────────────────────────────────────────
        # Look the entity_id up in EVERY employee-like collection,
        # against EVERY plausible ID field.
        id_hits: Dict[str, List[Dict[str, Any]]] = {}
        for coll in EMPLOYEE_LIKE_COLLECTIONS:
            try:
                or_clauses = []
                for f in ID_FIELDS:
                    if f == "_id":
                        # _id is ObjectId-typed in legacy collections;
                        # also try the raw string variant.
                        or_clauses.append({"_id": entity_id})
                    else:
                        or_clauses.append({f: entity_id})
                query = {"user_id": uid, "$or": or_clauses}
                hits = await _safe_find(db, coll, query, limit=10)
                if hits:
                    id_hits[coll] = hits
                # also try without user_id filter — some seed/legacy
                # docs may lack user_id and they'd still be "real".
                hits_no_uid = await _safe_find(
                    db, coll, {"$or": or_clauses}, limit=10,
                )
                # only report the no-uid hits that the uid-scoped
                # query missed (avoid duplicates).
                extra = [
                    h for h in hits_no_uid
                    if h not in (id_hits.get(coll) or [])
                ]
                if extra:
                    id_hits.setdefault(coll, []).extend(
                        [{**h, "_no_user_id_match": True} for h in extra],
                    )
            except Exception as e:
                probe_errors.append(f"id_lookup:{coll}:{e!r}")

        # ── Section 2 ──────────────────────────────────────────────
        # Look up by name hint ("عزوز") in every employee-like collection
        # to enumerate ALL candidate IDs the merchant could be referring
        # to. We use a case-insensitive contains-match so "عزوز",
        # "عزوز الفلاني", "Azoz" etc. all surface.
        name_hits: Dict[str, List[Dict[str, Any]]] = {}
        for coll in EMPLOYEE_LIKE_COLLECTIONS:
            try:
                query = {
                    "user_id": uid,
                    "$or": [
                        {"name":      {"$regex": name_hint,
                                       "$options": "i"}},
                        {"full_name": {"$regex": name_hint,
                                       "$options": "i"}},
                        {"display_name": {"$regex": name_hint,
                                          "$options": "i"}},
                        {"first_name":  {"$regex": name_hint,
                                         "$options": "i"}},
                    ],
                }
                hits = await _safe_find(
                    db, coll, query, limit=50,
                    projection={
                        "id": 1, "employee_id": 1, "external_id": 1,
                        "legacy_id": 1, "uuid": 1,
                        "name": 1, "full_name": 1, "display_name": 1,
                        "status": 1, "active": 1, "is_active": 1,
                        "archived": 1, "is_archived": 1,
                        "deleted": 1, "is_deleted": 1, "stopped": 1,
                        "created_at": 1, "updated_at": 1,
                        "user_id": 1, "metadata": 1,
                    },
                )
                if hits:
                    name_hits[coll] = hits
            except Exception as e:
                probe_errors.append(f"name_lookup:{coll}:{e!r}")

        # ── Section 3 ──────────────────────────────────────────────
        # Where does this entity_id appear in ledger / liability /
        # legacy-txn collections?
        ledger_refs: Dict[str, Dict[str, Any]] = {}
        for coll in LEDGER_REFERENCING_COLLECTIONS:
            try:
                query = {
                    "user_id": uid,
                    "$or": [
                        {"entity_id": entity_id},
                        {"employee_id": entity_id},
                        {"target_id": entity_id},
                    ],
                }
                cnt = await _safe_count(db, coll, query)
                sample = await _safe_find(
                    db, coll, query, limit=5,
                    projection={
                        "id": 1, "entity_id": 1, "entity_type": 1,
                        "sub_account": 1, "side": 1, "amount": 1,
                        "entry_type": 1, "status": 1, "txn_group_id": 1,
                        "metadata": 1, "created_at": 1, "notes": 1,
                        "employee_id": 1, "type": 1, "kind": 1,
                    },
                )
                ledger_refs[coll] = {
                    "count": cnt,
                    "sample": sample,
                }
            except Exception as e:
                probe_errors.append(f"ledger_ref:{coll}:{e!r}")

        # ── Section 4 ──────────────────────────────────────────────
        # Replicate THE EXACT lookup `ledger_core.py` performs so we
        # can prove whether the guard would pass or fail right now.
        # This is the question the merchant cares about most.
        guard_query = {
            "user_id": uid,
            "$or": [
                {"id": entity_id},
                {"employee_id": entity_id},
                {"external_id": entity_id},
                {"legacy_id": entity_id},
            ],
        }
        guard_employees_hit = await db.employees.find_one(
            guard_query, {"_id": 1, "id": 1, "name": 1},
        )
        # And the equivalent lookup against `operating_salaries`
        # (the modern storage).
        ops_hit = await db.operating_salaries.find_one(
            guard_query,
            {"_id": 0, "id": 1, "name": 1, "status": 1, "archived": 1,
             "active": 1, "is_active": 1, "stopped": 1, "deleted": 1,
             "user_id": 1, "metadata": 1, "created_at": 1},
        )

        # ── Section 5 ──────────────────────────────────────────────
        # Final assessment — derive a single recommendation the
        # merchant can act on.
        recommendation: List[str] = []
        if guard_employees_hit:
            recommendation.append(
                "✅ الحارس يجد الموظف في collection 'employees' — "
                "هذا الـ ID صالح. تحقق من سبب آخر للخطأ (cache "
                "frontend / مهلة الجلسة)."
            )
        elif ops_hit:
            recommendation.append(
                "🔴 جذر المشكلة: الموظف موجود في 'operating_salaries' "
                "لكن غير موجود في 'employees'. الحارس "
                "(ledger_core.py · Iter-226) يفحص فقط 'employees'. "
                "التوصية: توسيع الحارس ليفحص أيضاً "
                "'operating_salaries' (إصلاح Backend واحد، بدون "
                "migration)."
            )
        else:
            # Try harder — maybe the merchant's UI sent a stale ID and
            # the real "عزوز" lives under a different ID.
            any_name_hit = any(name_hits.values())
            if any_name_hit:
                recommendation.append(
                    "🟡 الـ entity_id المطلوب غير موجود إطلاقاً، لكن "
                    "يوجد موظف(ون) باسم 'عزوز' بـ IDs مختلفة. "
                    "التوصية: إصلاح الـ frontend ليرسل الـ ID الصحيح "
                    "(راجع 'name_hits' أدناه)."
                )
            else:
                recommendation.append(
                    "🔴 الـ entity_id غير موجود، ولا يوجد أي موظف "
                    "باسم 'عزوز' في أيٍّ من الـ collections. تأكد "
                    "أن الموظف مُسجَّل أصلاً تحت هذا الحساب."
                )
            # Bonus signal: was this entity_id ever active in the ledger?
            had_ledger = any(
                (v.get("count") or 0) > 0 for v in ledger_refs.values()
            )
            if had_ledger:
                recommendation.append(
                    "⚠️ هذا الـ entity_id له قيود تاريخية في الدفاتر، "
                    "أي أنه كان موظفاً مسجَّلاً سابقاً ثم تم حذفه/"
                    "أرشفته. إن أردت متابعة العهد قديمة عليه ⇒ "
                    "أعد تفعيله، أو اربط القيود التاريخية بموظف بديل."
                )

        return {
            "ok": True,
            "iter": "250b.P1.5.n",
            "user_id": uid,
            "queried": {
                "entity_id": entity_id,
                "name_hint": name_hint,
            },
            "guard_simulation": {
                "ledger_core_check_against_employees": _strip_id(
                    guard_employees_hit,
                ),
                "would_pass_guard_today": bool(guard_employees_hit),
                "operating_salaries_hit": ops_hit,
                "discrepancy": (
                    bool(ops_hit) and not bool(guard_employees_hit)
                ),
            },
            "section_1_id_hits_by_collection": id_hits,
            "section_2_name_hits_by_collection": name_hits,
            "section_3_ledger_references": ledger_refs,
            "recommendation": recommendation,
            "probe_errors": probe_errors,
            "notes": [
                "STRICT READ-ONLY · no writes performed.",
                "Guard source: /app/backend/ledger_core.py · "
                "lines 249-280 (Iter-226).",
                "If `guard_simulation.discrepancy = true` ⇒ employee "
                "exists in 'operating_salaries' but NOT in 'employees' "
                "⇒ guard needs to be widened.",
            ],
        }

    return router


__all__ = ["make_employee_lookup_diagnostic_router"]
