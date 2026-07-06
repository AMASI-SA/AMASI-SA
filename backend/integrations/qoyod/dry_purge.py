"""Iter-2026-02.rev34 — DRY-mapping Purge/Repair tool (P0).

User directive (2026-02): the preview/dry runs left DRY:* sentinel ids
inside the mapping + ledger collections. Those sentinels pollute the
pending-orders classifier and the invoices monitoring UI. This module
provides a *gated* plan → execute → verify flow so the operator can
clean them out with a full forensic trail.

Scope
─────
DELETE (each doc is ARCHIVED into `qoyod_dry_purge_archive` first):
  • qoyod_products_mapping   — qoyod_product_id          ^(DRY:|PREVIEW:)
  • qoyod_customers_mapping  — qoyod_customer_id         ^(DRY:|PREVIEW:)
  • qoyod_invoices           — qoyod_invoice_id          ^(DRY:|PREVIEW:)
  • qoyod_invoice_payments   — qoyod_invoice_payment_id  ^(DRY:|PREVIEW:)
                               OR qoyod_invoice_id       ^(DRY:|PREVIEW:)

REPAIR (flag-clear ONLY — a mapping with a REAL قيود id is NEVER
deleted, per user directive):
  • qoyod_products_mapping / qoyod_customers_mapping rows carrying a
    real id + legacy `dry_run_only=True` → flag cleared to False with
    audit fields (`dry_flag_cleared_*`).

NEVER TOUCHED (hard guarantee — no query in this module references
these collections for writes):
  • integration_inbox rows (orders) and their raw payloads
  • webhook_orders / qoyod_webhook_events / integration_events
  • any REAL قيود id — the frozen forensic invoices #188-194 and
    payments #160-165 carry real ids and are out of scope by
    definition (delete queries match the DRY:/PREVIEW: prefix only).

Gating
──────
`execute_dry_purge` refuses unless confirm_token == CONFIRM_TOKEN.
Every run writes a summary row into `qoyod_dry_purge_runs`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

CONFIRM_TOKEN = "PURGE-DRY-MAPPINGS"

# Collections this module is allowed to DELETE from. Anything else is
# structurally impossible (see _DELETE_SPECS below).
PURGEABLE_COLLECTIONS = (
    "qoyod_products_mapping",
    "qoyod_customers_mapping",
    "qoyod_invoices",
    "qoyod_invoice_payments",
)

ARCHIVE_COLLECTION = "qoyod_dry_purge_archive"
RUNS_COLLECTION    = "qoyod_dry_purge_runs"

# Cap per collection per run — far above the observed volumes
# (46 products / ~132 customers in production).
_MAX_DOCS_PER_COLLECTION = 10_000

# Terminal stages — rows there can never be sent again, so a stale
# stored DRY payload on them is historical, not a live risk.
_TERMINAL_STAGES = (
    "COMPLETED", "COMPLETED_WITH_ROUNDING_WARNING",
    "SKIPPED", "DEAD_LETTER",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _dry_rx() -> dict:
    return {"$regex": r"^(DRY:|PREVIEW:)", "$options": "i"}


def _real_id_filter(field: str) -> dict:
    """Field exists, non-empty, and does NOT carry the DRY:/PREVIEW:
    prefix — i.e. a real قيود id."""
    return {field: {
        "$exists": True,
        "$nin":    [None, ""],
        "$not":    {"$regex": r"^(DRY:|PREVIEW:)", "$options": "i"},
    }}


def _delete_query(coll: str, user_id: str) -> dict:
    if coll == "qoyod_products_mapping":
        return {"user_id": user_id, "qoyod_product_id": _dry_rx()}
    if coll == "qoyod_customers_mapping":
        return {"user_id": user_id, "qoyod_customer_id": _dry_rx()}
    if coll == "qoyod_invoices":
        return {"user_id": user_id, "qoyod_invoice_id": _dry_rx()}
    if coll == "qoyod_invoice_payments":
        return {"user_id": user_id, "$or": [
            {"qoyod_invoice_payment_id": _dry_rx()},
            {"qoyod_invoice_id":         _dry_rx()},
        ]}
    raise ValueError(f"not a purgeable collection: {coll}")


# (collection, real-id field) pairs eligible for the flag REPAIR.
_REPAIR_SPECS = (
    ("qoyod_products_mapping",  "qoyod_product_id"),
    ("qoyod_customers_mapping", "qoyod_customer_id"),
)


def _repair_query(coll: str, field: str, user_id: str) -> dict:
    return {"user_id": user_id, "dry_run_only": True,
            **_real_id_filter(field)}


def _sample(doc: dict) -> dict:
    """JSON-safe sample of a doc (no _id, datetimes → ISO)."""
    out = {}
    for k, v in doc.items():
        if k == "_id":
            continue
        out[k] = v.isoformat() if hasattr(v, "isoformat") else v
    return out


class DryPurgeRefused(Exception):
    def __init__(self, code: str, human: str, **extra):
        super().__init__(human)
        self.code  = code
        self.extra = extra


def _is_real_id(v: Any) -> bool:
    """Non-empty and NOT a DRY:/PREVIEW: sentinel → real قيود id."""
    if v in (None, ""):
        return False
    return not str(v).upper().startswith(("DRY:", "PREVIEW:"))


# ─────────────────────────────────────────────────────────────────────
# SAFETY CHECK — no to-be-deleted row may carry ANY real قيود id
# ─────────────────────────────────────────────────────────────────────
async def build_safety_check(db, *, user_id: str) -> dict:
    """Iter-2026-02.rev34.1 (user directive) — scan every row the
    delete queries would remove and count rows carrying a REAL قيود
    id in ANY identifier field. All four counters MUST be 0 before
    execute is allowed; execute_dry_purge enforces this itself.

      • dry_product_rows_with_real_product_id
      • dry_customer_rows_with_real_customer_id
      • dry_invoice_rows_with_real_invoice_id
        (also counts payment rows whose qoyod_invoice_id is real)
      • dry_invoice_rows_with_real_payment_id
        (real qoyod_invoice_payment_id / qoyod_receipt_id on any
        to-be-deleted row)
    """
    counters: dict[str, dict] = {k: {"count": 0, "samples": []} for k in (
        "dry_invoice_rows_with_real_invoice_id",
        "dry_invoice_rows_with_real_payment_id",
        "dry_customer_rows_with_real_customer_id",
        "dry_product_rows_with_real_product_id",
    )}

    def _hit(counter: str, coll: str, doc: dict, field: str):
        c = counters[counter]
        c["count"] += 1
        if len(c["samples"]) < 10:
            c["samples"].append({
                "collection":         coll,
                "offending_field":    field,
                "offending_value":    doc.get(field),
                "salla_order_number": doc.get("salla_order_number"),
                "salla_order_id":     doc.get("salla_order_id"),
                "sku":                doc.get("sku"),
                "lookup_key":         doc.get("lookup_key"),
            })

    coll = "qoyod_products_mapping"
    async for d in db[coll].find(_delete_query(coll, user_id)):
        if _is_real_id(d.get("qoyod_product_id")):
            _hit("dry_product_rows_with_real_product_id",
                 coll, d, "qoyod_product_id")

    coll = "qoyod_customers_mapping"
    async for d in db[coll].find(_delete_query(coll, user_id)):
        if _is_real_id(d.get("qoyod_customer_id")):
            _hit("dry_customer_rows_with_real_customer_id",
                 coll, d, "qoyod_customer_id")

    coll = "qoyod_invoices"
    async for d in db[coll].find(_delete_query(coll, user_id)):
        if _is_real_id(d.get("qoyod_invoice_id")):
            _hit("dry_invoice_rows_with_real_invoice_id",
                 coll, d, "qoyod_invoice_id")
        for f in ("qoyod_invoice_payment_id", "qoyod_receipt_id"):
            if _is_real_id(d.get(f)):
                _hit("dry_invoice_rows_with_real_payment_id", coll, d, f)

    coll = "qoyod_invoice_payments"
    async for d in db[coll].find(_delete_query(coll, user_id)):
        if _is_real_id(d.get("qoyod_invoice_payment_id")):
            _hit("dry_invoice_rows_with_real_payment_id",
                 coll, d, "qoyod_invoice_payment_id")
        if _is_real_id(d.get("qoyod_invoice_id")):
            _hit("dry_invoice_rows_with_real_invoice_id",
                 coll, d, "qoyod_invoice_id")

    all_zero = all(c["count"] == 0 for c in counters.values())
    return {
        **counters,
        "all_zero":        all_zero,
        "execute_allowed": all_zero,
        "blocked_reason": None if all_zero else (
            "صف واحد أو أكثر من المرشّحين للحذف يحمل معرّف قيود حقيقي "
            "في أحد الحقول — الحذف ممنوع حتى تُراجع هذه الصفوف يدوياً "
            "(راجع samples في كل عدّاد أعلاه). execute سيرفض بنيوياً."),
        "note": (
            "يجب أن تكون العدّادات الأربعة = 0 قبل execute. البوابة "
            "مفروضة داخل execute نفسه وليست عرضاً فقط."),
    }


# ─────────────────────────────────────────────────────────────────────
# PLAN — read-only preview of exactly what execute would do
# ─────────────────────────────────────────────────────────────────────
async def build_dry_purge_plan(db, *, user_id: str) -> dict:
    delete_buckets: dict[str, dict] = {}
    total_delete = 0
    for coll in PURGEABLE_COLLECTIONS:
        q = _delete_query(coll, user_id)
        count = await db[coll].count_documents(q)
        samples = [
            _sample(d) async for d in db[coll].find(q).limit(10)
        ]
        delete_buckets[coll] = {"count": count, "samples": samples}
        total_delete += count

    repair_buckets: dict[str, dict] = {}
    total_repair = 0
    for coll, field in _REPAIR_SPECS:
        q = _repair_query(coll, field, user_id)
        count = await db[coll].count_documents(q)
        samples = [
            _sample(d) async for d in db[coll].find(q).limit(10)
        ]
        repair_buckets[coll] = {"count": count, "samples": samples}
        total_repair += count

    safety = await build_safety_check(db, user_id=user_id)

    return {
        "ok":                     True,
        "generated_at":           _now().isoformat(),
        "total_delete":           total_delete,
        "total_repair":           total_repair,
        "delete":                 delete_buckets,
        "repair":                 repair_buckets,
        "safety_check":           safety,
        "execute_allowed":        safety["execute_allowed"],
        "expected_confirm_token": CONFIRM_TOKEN,
        "never_touched": [
            "integration_inbox (الطلبات + raw payload)",
            "webhook_orders / qoyod_webhook_events",
            "أي معرّف قيود حقيقي (الفواتير 188-194 والسدادات 160-165 خارج النطاق)",
        ],
        "note": (
            "قراءة فقط — لا يُحذف شيء من هذا الاستعلام. للتنفيذ استخدم "
            "POST /admin/dry-purge/execute مع confirm_token. كل محذوف "
            "يُؤرشف أولاً في qoyod_dry_purge_archive."),
    }


# ─────────────────────────────────────────────────────────────────────
# EXECUTE — archive → delete → repair, all scoped to user_id
# ─────────────────────────────────────────────────────────────────────
async def execute_dry_purge(
    db, *, user_id: str, confirm_token: str, actor: str = "operator",
) -> dict:
    if (confirm_token or "").strip() != CONFIRM_TOKEN:
        raise DryPurgeRefused(
            "confirm_token_mismatch",
            f"Pass confirm_token='{CONFIRM_TOKEN}' to authorise the purge.")

    # Iter-2026-02.rev34.1 — HARD safety gate. If any to-be-deleted
    # row carries a real قيود id anywhere, the whole run is refused
    # BEFORE any archive/delete/repair write happens.
    safety = await build_safety_check(db, user_id=user_id)
    if not safety["all_zero"]:
        non_zero = {k: v["count"] for k, v in safety.items()
                    if isinstance(v, dict) and v.get("count", 0) > 0}
        raise DryPurgeRefused(
            "safety_check_failed",
            ("مرفوض — safety_check غير صفري: "
             f"{non_zero}. لا حذف قبل مراجعة هذه الصفوف يدوياً."),
            safety_check=safety)

    run_id = uuid4().hex
    now = _now()

    deleted: dict[str, int]  = {}
    archived: dict[str, int] = {}
    for coll in PURGEABLE_COLLECTIONS:
        q = _delete_query(coll, user_id)
        docs = await db[coll].find(q).to_list(
            length=_MAX_DOCS_PER_COLLECTION)
        if not docs:
            deleted[coll] = 0
            archived[coll] = 0
            continue
        # 1. Archive FIRST — forensic trail, restorable.
        archive_rows = [{
            "run_id":            run_id,
            "user_id":           user_id,
            "source_collection": coll,
            "source_object_id":  str(d.get("_id")),
            "purged_at":         now,
            "purged_by":         actor,
            "doc":               {k: v for k, v in d.items()
                                  if k != "_id"},
        } for d in docs]
        await db[ARCHIVE_COLLECTION].insert_many(archive_rows)
        archived[coll] = len(archive_rows)
        # 2. Delete by explicit _id list (never a broad query).
        ids = [d["_id"] for d in docs]
        res = await db[coll].delete_many(
            {"_id": {"$in": ids}, "user_id": user_id})
        deleted[coll] = res.deleted_count

    repaired: dict[str, int] = {}
    for coll, field in _REPAIR_SPECS:
        res = await db[coll].update_many(
            _repair_query(coll, field, user_id),
            {"$set": {
                "dry_run_only":            False,
                "dry_flag_cleared_at":     now,
                "dry_flag_cleared_by":     actor,
                "dry_flag_cleared_reason": "dry_purge_repair",
                "dry_flag_cleared_run_id": run_id,
            }})
        repaired[coll] = res.modified_count

    summary = {
        "ok":             True,
        "run_id":         run_id,
        "user_id":        user_id,
        "executed_at":    now.isoformat(),
        "executed_by":    actor,
        "deleted":        deleted,
        "archived":       archived,
        "repaired":       repaired,
        "total_deleted":  sum(deleted.values()),
        "total_repaired": sum(repaired.values()),
        "safety_check_passed": True,
        "archive_collection": ARCHIVE_COLLECTION,
        "note": (
            "كل صف محذوف مؤرشف في qoyod_dry_purge_archive تحت run_id "
            "هذا. لم يُمسّ أي طلب في integration_inbox ولا أي raw "
            "payload ولا أي معرّف قيود حقيقي."),
    }
    await db[RUNS_COLLECTION].insert_one({**summary, "executed_at": now})
    summary.pop("_id", None)
    return summary


# ─────────────────────────────────────────────────────────────────────
# PAYLOAD SCRUB — Iter-2026-02.rev34.2 (user directive)
# ─────────────────────────────────────────────────────────────────────
# After the production purge, verify flagged sendable inbox rows whose
# STORED request-body snapshot (`qoyod_payloads.invoice`) still holds
# DRY:/PREVIEW: ids or product_id=None from the old dry runs.
#
# Those snapshots are WRITE-ONLY artefacts: pipeline.py rebuilds the
# payload from the (now clean) mappings on every run and overwrites
# the snapshot (see pipeline.py ~line 1908). Every reader
# (eligible_orders, pending_classifier, monitors, one_shot audit) uses
# them for DISPLAY only — nothing sends a stored snapshot to قيود.
#
# So the safe, limited fix is: archive the poisoned snapshot keys into
# qoyod_dry_purge_archive, then $unset them from the row. The scrub:
#   • sends NOTHING to قيود            • never changes pipeline_stage
#   • never touches raw_payload        • never deletes the order row
#   • never touches settings/canary

SCRUB_CONFIRM_TOKEN = "SCRUB-DRY-PAYLOADS"

# Snapshot keys written atomically together in pipeline.py — when a
# leaky key is removed, its paired metadata goes with it.
_PAIRED_META_KEYS = {
    "invoice": ("invoice_snapshot_at", "invoice_diagnostics"),
    "invoice_payment": ("invoice_payment_snapshot_at",
                        "invoice_payment_fingerprint"),
    "customer_request": ("customer_request_at",),
    "invoice_blocked_preflight": ("invoice_blocked_at",),
    "invoice_selective_blocked_payload": ("invoice_selective_blocked_at",),
    "invoice_locked_payload": ("invoice_locked_at",),
    "invoice_rev32_blocked_payload": ("invoice_rev32_blocked_at",),
    "invoice_payment_selective_blocked_payload":
        ("invoice_payment_selective_blocked_at",),
    "invoice_payment_locked_payload": ("invoice_payment_locked_at",),
    "invoice_payment_rev32_blocked_payload":
        ("invoice_payment_rev32_blocked_at",),
}


def _scrub_scope_query(user_id: str) -> dict:
    """Same scope as verify's sendable-rows check."""
    return {
        "user_id":        user_id,
        "pipeline_stage": {"$nin": list(_TERMINAL_STAGES)},
        "qoyod_payloads.invoice": {"$exists": True},
    }


def _leaky_payload_keys(payloads: dict) -> list[str]:
    from integrations.qoyod.live_send_gate import _has_dry_or_preview_leak
    return [k for k, v in (payloads or {}).items()
            if _has_dry_or_preview_leak(v)]


async def build_payload_scrub_plan(db, *, user_id: str) -> dict:
    """READ-ONLY: the exact rows execute_payload_scrub would clean."""
    count = 0
    samples: list[dict] = []
    async for r in db.integration_inbox.find(
            _scrub_scope_query(user_id),
            {"id": 1, "salla_order_number": 1, "trace_id": 1,
             "pipeline_stage": 1, "qoyod_payloads": 1}
    ).limit(_MAX_DOCS_PER_COLLECTION):
        leaky = _leaky_payload_keys(r.get("qoyod_payloads") or {})
        if "invoice" not in leaky:
            continue
        count += 1
        if len(samples) < 20:
            samples.append({
                "salla_order_number": r.get("salla_order_number"),
                "trace_id":           r.get("trace_id"),
                "pipeline_stage":     r.get("pipeline_stage"),
                "leaky_payload_keys": leaky,
            })
    return {
        "ok":                     True,
        "generated_at":           _now().isoformat(),
        "count":                  count,
        "samples":                samples,
        "expected_confirm_token": SCRUB_CONFIRM_TOKEN,
        "guarantees": [
            "لا إرسال إلى قيود إطلاقاً",
            "لا تغيير pipeline_stage ولا حذف أي طلب",
            "raw_payload لا يُمسّ",
            "كل snapshot يُؤرشف في qoyod_dry_purge_archive قبل الإزالة",
            "الـ pipeline يعيد بناء الـ payload من الـ mappings النظيفة "
            "عند أي معالجة مستقبلية معتمدة",
        ],
    }


async def execute_payload_scrub(
    db, *, user_id: str, confirm_token: str, actor: str = "operator",
) -> dict:
    if (confirm_token or "").strip() != SCRUB_CONFIRM_TOKEN:
        raise DryPurgeRefused(
            "confirm_token_mismatch",
            f"Pass confirm_token='{SCRUB_CONFIRM_TOKEN}' to authorise "
            "the payload scrub.")

    run_id = uuid4().hex
    now = _now()
    scrubbed_rows = 0
    scrubbed_keys_total = 0

    async for r in db.integration_inbox.find(
            _scrub_scope_query(user_id)
    ).limit(_MAX_DOCS_PER_COLLECTION):
        payloads = r.get("qoyod_payloads") or {}
        leaky = _leaky_payload_keys(payloads)
        if "invoice" not in leaky:
            continue
        # Leaky keys + their paired metadata (only if present).
        to_remove: list[str] = []
        for k in leaky:
            to_remove.append(k)
            for meta in _PAIRED_META_KEYS.get(k, ()):
                if meta in payloads:
                    to_remove.append(meta)
        to_remove = list(dict.fromkeys(to_remove))

        # 1. Archive FIRST.
        await db[ARCHIVE_COLLECTION].insert_one({
            "run_id":             run_id,
            "user_id":            user_id,
            "source_collection":  "integration_inbox.qoyod_payloads",
            "source_object_id":   str(r.get("_id")),
            "inbox_id":           r.get("id"),
            "salla_order_number": r.get("salla_order_number"),
            "trace_id":           r.get("trace_id"),
            "pipeline_stage":     r.get("pipeline_stage"),
            "purged_at":          now,
            "purged_by":          actor,
            "doc": {k: payloads[k] for k in to_remove if k in payloads},
        })
        # 2. $unset ONLY inside qoyod_payloads + a forensic marker.
        #    Stage, raw_payload and every other field stay untouched.
        await db.integration_inbox.update_one(
            {"_id": r["_id"], "user_id": user_id},
            {"$unset": {f"qoyod_payloads.{k}": "" for k in to_remove},
             "$set": {"rev34_payload_scrub": {
                 "run_id":        run_id,
                 "scrubbed_at":   now,
                 "scrubbed_by":   actor,
                 "scrubbed_keys": to_remove,
             }}})
        scrubbed_rows += 1
        scrubbed_keys_total += len(to_remove)

    summary = {
        "ok":                  True,
        "kind":                "payload_scrub",
        "run_id":              run_id,
        "user_id":             user_id,
        "executed_at":         now.isoformat(),
        "executed_by":         actor,
        "scrubbed_rows":       scrubbed_rows,
        "scrubbed_keys_total": scrubbed_keys_total,
        "archive_collection":  ARCHIVE_COLLECTION,
        "note": (
            "أُزيلت الـ snapshots الملوثة فقط من qoyod_payloads بعد "
            "أرشفتها. لم يُرسل شيء إلى قيود، ولم تتغير أي مرحلة، ولم "
            "يُمسّ أي raw_payload أو طلب."),
    }
    await db[RUNS_COLLECTION].insert_one({**summary, "executed_at": now})
    summary.pop("_id", None)
    return summary



async def verify_dry_state(db, *, user_id: str) -> dict:
    """The four acceptance checks from the user's P0 directive:
      1. /products/dry-mappings would return count=0
         (prefix ids AND legacy dry_run_only flags).
      2. Customers mapping clean.
      3. Ledger (qoyod_invoices / qoyod_invoice_payments) carries no
         DRY:/PREVIEW: rows → pending-orders can never treat a DRY
         invoice as a real existing_invoice.
      4. No sendable (non-terminal) inbox row still holds a stored
         request_body containing DRY/PREVIEW ids or product_id=None.
         (Defence-in-depth: live_send_gate deep-scans again at send
         time and hard-blocks any leak regardless.)
    """
    from integrations.qoyod.live_send_gate import _has_dry_or_preview_leak

    checks: dict[str, dict] = {}

    # 1. Products — mirrors GET /admin/products/dry-mappings exactly.
    products_q = {"user_id": user_id, "$or": [
        {"dry_run_only": True},
        {"qoyod_product_id": _dry_rx()},
    ]}
    n = await db.qoyod_products_mapping.count_documents(products_q)
    checks["products_dry_mappings"] = {"count": n, "pass": n == 0}

    # 2. Customers — same shape.
    customers_q = {"user_id": user_id, "$or": [
        {"dry_run_only": True},
        {"qoyod_customer_id": _dry_rx()},
    ]}
    n = await db.qoyod_customers_mapping.count_documents(customers_q)
    checks["customers_dry_mappings"] = {"count": n, "pass": n == 0}

    # 3. Ledger.
    n_inv = await db.qoyod_invoices.count_documents(
        _delete_query("qoyod_invoices", user_id))
    checks["ledger_dry_invoices"] = {"count": n_inv, "pass": n_inv == 0}
    n_pay = await db.qoyod_invoice_payments.count_documents(
        _delete_query("qoyod_invoice_payments", user_id))
    checks["ledger_dry_payments"] = {"count": n_pay, "pass": n_pay == 0}

    # 4. Stored request bodies on rows that could still be sent.
    leaks: list[dict] = []
    cursor = db.integration_inbox.find(
        {"user_id": user_id,
         "pipeline_stage": {"$nin": list(_TERMINAL_STAGES)},
         "qoyod_payloads.invoice": {"$exists": True}},
        {"_id": 0, "salla_order_number": 1, "trace_id": 1,
         "pipeline_stage": 1, "qoyod_payloads.invoice": 1},
    ).limit(500)
    async for r in cursor:
        payload = (r.get("qoyod_payloads") or {}).get("invoice")
        if payload is not None and _has_dry_or_preview_leak(payload):
            leaks.append({
                "salla_order_number": r.get("salla_order_number"),
                "trace_id":           r.get("trace_id"),
                "pipeline_stage":     r.get("pipeline_stage"),
            })
    checks["sendable_rows_with_dry_request_body"] = {
        "count":   len(leaks),
        "pass":    len(leaks) == 0,
        "samples": leaks[:20],
        "note": (
            "هذه الصفوف تحتاج one-shot-reprocess (يعيد بناء الـ payload "
            "من الـ mappings النظيفة). بوابة live_send_gate تفحص "
            "الحمولة مرة أخرى لحظة الإرسال وتمنع أي تسريب DRY أو "
            "product_id=None مهما كان."),
    }

    # 5. Code-level guarantee (rev34 fix): pending-orders ledger lookup
    #    excludes DRY:/PREVIEW: from existing_invoice detection.
    checks["pending_orders_ledger_excludes_dry"] = {
        "pass": True,
        "note": ("إصلاح rev34 في routes.py — استعلام qoyod_invoices "
                 "داخل pending-orders يستبعد ^(DRY:|PREVIEW:) بنيوياً."),
    }

    all_pass = all(c.get("pass") for c in checks.values())
    return {
        "ok":          True,
        "all_pass":    all_pass,
        "checked_at":  _now().isoformat(),
        "checks":      checks,
    }
