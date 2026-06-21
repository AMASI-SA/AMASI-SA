"""Iter-250b · P1.5.t — Movements ↔ GL Drift Analyzer (Read-Only).

Background
==========
The P1.5.s supplier-ledger work surfaced a structural issue: dozens
of supplier invoices live in `financial_movements` with no matching
entry in `general_ledger`. Until we understand WHY, no migration or
write is permitted.

This endpoint enumerates drifted movements across ALL suppliers for
the merchant, categorises each one by its probable cause, and
returns a comprehensive Arabic-friendly diagnostic report.

Probable causes the analyser tries to classify each row into
---------------------------------------------------------------
A) `legacy_pre_gl`
   The movement was created BEFORE the `general_ledger` table was
   introduced into this codebase (heuristic: created earlier than
   the earliest GL entry for this user, OR has a metadata flag
   `legacy`, `migrated`, `import_batch`).

B) `gl_creation_failed`
   The movement carries a `ledger_txn_group_id` but no GL row with
   that group_id actually exists. Indicates a partial-failure when
   the original write was attempted (e.g. mid-transaction crash).

C) `no_group_id_at_all`
   The movement has `ledger_txn_group_id = null/missing`. Could be
   legacy or a code path that simply forgot to wire the GL write.

D) `voided_or_draft`
   The movement is marked `voided / draft / cancelled / status != active`.
   These are correctly EXCLUDED from GL by design — informational
   only.

E) `import_batch`
   `metadata.import_batch_id` is set. Excel imports that bypass the
   ledger writer.

F) `manual_legacy_data`
   Catch-all for movements created via a manual data-entry path
   (legacy admin tool, direct DB inserts).

Endpoint
========
    GET /api/audit/movements-gl-drift
        ?from=YYYY-MM-DD          (optional)
        &to=YYYY-MM-DD            (optional)
        &movement_type=supplier_invoice   (default; pass "all" for everything)

STRICT READ-ONLY — no writes, no migrations.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query


def _r(n) -> float:
    return round(float(n or 0), 2)


def _strip(d):
    if not d:
        return d
    d = dict(d)
    d.pop("_id", None)
    return d


def make_movements_gl_drift_router(db, current_user):
    router = APIRouter(tags=["diagnostics", "movements-gl-drift"])

    @router.get("/audit/movements-gl-drift")
    async def movements_gl_drift(
        from_: Optional[str] = Query(None, alias="from"),
        to:    Optional[str] = Query(None),
        movement_type: str = Query("supplier_invoice"),
        user: dict = Depends(current_user),
    ):
        uid = user["id"]

        # ── 1) Earliest GL row for this user (heuristic boundary) ──
        earliest_gl = await db.general_ledger.find_one(
            {"user_id": uid},
            {"_id": 0, "created_at": 1},
            sort=[("created_at", 1)],
        )
        gl_origin = (earliest_gl or {}).get("created_at")

        # ── 2) Build movement filter ───────────────────────────────
        mv_query: Dict[str, Any] = {"user_id": uid}
        if movement_type and movement_type != "all":
            mv_query["movement_type"] = movement_type
        date_filter: Dict[str, Any] = {}
        if from_:
            date_filter["$gte"] = from_
        if to:
            date_filter["$lte"] = to
        if date_filter:
            mv_query["doc_date"] = date_filter

        # ── 3) Walk all movements, partition into has-gl / no-gl ──
        all_movements: List[Dict[str, Any]] = []
        async for m in db.financial_movements.find(mv_query, {"_id": 0}):
            all_movements.append(m)

        total_movements = len(all_movements)

        # Collect all txn_group_ids referenced by movements.
        group_ids = list({
            m.get("ledger_txn_group_id")
            for m in all_movements
            if m.get("ledger_txn_group_id")
        })

        # Which of those groups ACTUALLY exist in GL?
        gl_groups_present = set()
        if group_ids:
            async for g in db.general_ledger.find(
                {"user_id": uid, "txn_group_id": {"$in": group_ids},
                 "status": "posted"},
                {"_id": 0, "txn_group_id": 1},
            ):
                gl_groups_present.add(g["txn_group_id"])

        # ── 4) Categorise each movement ────────────────────────────
        with_gl: List[Dict[str, Any]] = []
        drifted: List[Dict[str, Any]] = []
        for m in all_movements:
            tg = m.get("ledger_txn_group_id")
            status = (m.get("status") or "active").lower()
            meta = m.get("metadata") or {}

            if tg and tg in gl_groups_present:
                with_gl.append(m); continue

            # Drifted — figure out why.
            cause = None
            if status in ("voided", "cancelled", "draft", "deleted"):
                cause = "voided_or_draft"
            elif meta.get("import_batch_id") or meta.get("import_source"):
                cause = "import_batch"
            elif (gl_origin and m.get("created_at")
                    and str(m["created_at"]) < str(gl_origin)):
                cause = "legacy_pre_gl"
            elif tg and tg not in gl_groups_present:
                cause = "gl_creation_failed"
            elif not tg:
                cause = "no_group_id_at_all"
            else:
                cause = "manual_legacy_data"

            drifted.append({
                "movement_id":   m.get("id"),
                "doc_number":    m.get("doc_number"),
                "doc_date":      m.get("doc_date"),
                "created_at":    m.get("created_at"),
                "supplier_id":   m.get("supplier_id"),
                "supplier_snapshot": m.get("supplier_snapshot"),
                "total_amount":  _r(m.get("total_amount")),
                "paid_amount":   _r(m.get("paid_amount")),
                "payment_terms": m.get("payment_terms"),
                "status":        status,
                "movement_type": m.get("movement_type"),
                "ledger_txn_group_id": tg,
                "category_snapshot": m.get("category_snapshot"),
                "metadata_keys": sorted(list(meta.keys())),
                "cause":         cause,
                "notes":         m.get("notes"),
            })

        # ── 5) Roll-ups for the report ────────────────────────────
        by_cause: Dict[str, Dict[str, Any]] = {}
        for d in drifted:
            c = d["cause"]
            slot = by_cause.setdefault(c, {
                "count": 0, "total_amount": 0.0, "samples": [],
            })
            slot["count"] += 1
            slot["total_amount"] = _r(slot["total_amount"] + d["total_amount"])
            if len(slot["samples"]) < 5:
                slot["samples"].append({
                    "movement_id": d["movement_id"],
                    "doc_number":  d["doc_number"],
                    "doc_date":    d["doc_date"],
                    "supplier":    (d["supplier_snapshot"] or {}).get("name"),
                    "total":       d["total_amount"],
                })

        by_supplier: Dict[str, Dict[str, Any]] = {}
        for d in drifted:
            sid = d["supplier_id"] or "(no_supplier)"
            slot = by_supplier.setdefault(sid, {
                "supplier_name": (d["supplier_snapshot"] or {}).get("name"),
                "count": 0, "total_amount": 0.0, "paid_amount": 0.0,
                "causes": set(),
            })
            slot["count"] += 1
            slot["total_amount"] = _r(slot["total_amount"] + d["total_amount"])
            slot["paid_amount"]  = _r(slot["paid_amount"]  + d["paid_amount"])
            slot["causes"].add(d["cause"])
        # JSON-friendly causes
        for v in by_supplier.values():
            v["causes"] = sorted(list(v["causes"]))

        by_year: Dict[str, Dict[str, Any]] = {}
        for d in drifted:
            y = (d.get("doc_date") or d.get("created_at") or "")[:4] or "?"
            slot = by_year.setdefault(y, {"count": 0, "total_amount": 0.0})
            slot["count"] += 1
            slot["total_amount"] = _r(slot["total_amount"] + d["total_amount"])

        # ── 6) Final response ─────────────────────────────────────
        return {
            "ok": True,
            "iter": "250b.P1.5.t",
            "filters": {
                "from": from_, "to": to,
                "movement_type": movement_type,
            },
            "gl_origin_first_seen": gl_origin,
            "summary": {
                "total_movements_in_scope": total_movements,
                "with_gl":  len(with_gl),
                "drifted":  len(drifted),
                "drift_ratio": (
                    round(len(drifted) / total_movements, 4)
                    if total_movements else 0
                ),
                "total_drifted_amount":
                    _r(sum(d["total_amount"] for d in drifted)),
                "total_drifted_paid_amount":
                    _r(sum(d["paid_amount"] for d in drifted)),
            },
            "rollups": {
                "by_cause":    by_cause,
                "by_supplier": by_supplier,
                "by_year":     by_year,
            },
            "drifted_movements": sorted(
                drifted,
                key=lambda r: (r.get("doc_date") or "",
                                r.get("created_at") or ""),
            ),
            "interpretation_hints": {
                "legacy_pre_gl":
                    "فواتير أُنشئت قبل تفعيل دفتر general_ledger "
                    "في هذا الـ tenant. لا تعتبر bug — تحتاج backfill "
                    "اختياري إن أردت إدراجها في الدفتر الموحد.",
                "gl_creation_failed":
                    "فاتورة حملت `ledger_txn_group_id` لكن لا يوجد "
                    "GL row بهذا المعرّف — يدل على فشل كتابة جزئي "
                    "(crash / rollback). تحتاج فحص case-by-case.",
                "no_group_id_at_all":
                    "فاتورة بدون `ledger_txn_group_id` نهائياً — "
                    "إما legacy، أو كود قديم لا يكتب GL.",
                "voided_or_draft":
                    "حالة الفاتورة `voided/draft/cancelled` — صحيح "
                    "ألا يكون لها قيد. ليست drift فعلية، إعلامية.",
                "import_batch":
                    "أُدخلت عبر استيراد Excel أو batch import — لم "
                    "تمر عبر كاتب GL.",
                "manual_legacy_data":
                    "إدخال يدوي قديم لا يتطابق مع أي من الأنماط "
                    "أعلاه — يحتاج تدقيق فردي.",
            },
            "next_steps_read_only": [
                "راجع `rollups.by_cause` لمعرفة الأسباب الرئيسية.",
                "افحص `drifted_movements` (مرتبة زمنياً) قبل اتخاذ "
                "أي قرار.",
                "لا migrations / لا writes / لا cleanup حتى تقرر "
                "السياسة المناسبة لكل سبب.",
            ],
        }

    return router


__all__ = ["make_movements_gl_drift_router"]
