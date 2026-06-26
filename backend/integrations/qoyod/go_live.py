"""QYD-GO — Qoyod Production Readiness Layer.

A read-only verification mode. Adds NO business logic; it only inspects
the existing state and proves whether the system is safe to flip into
live-Qoyod mode.

Two public outputs:
    • `go_live_checklist(db, user_id, *, api_client)` — 11 boolean checks.
    • `go_live_report(db, user_id, *, api_client)`    — quantitative
      pre-flight snapshot (eligible orders, products needing creation,
      customers needing creation, unmapped payment methods, would-fail
      count).

Activation is gated by `activate_production_mode(db, user_id)` which
re-runs the checklist server-side and refuses to flip
`dry_run_mode=false` + `enabled=true` unless every item passes.

Per user directive: this layer is INDEPENDENT — no new business rules,
no writes to invoices/customers/products. The activate endpoint is the
only mutating call, and it only touches `qoyod_settings`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError
from integrations.qoyod.credentials import get_api_key, get_fingerprint
from integrations.qoyod.compliance import COMPLETED_TRIGGER_STATUSES
from integrations.qoyod.preflight import run as preflight_run


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Checklist items — one per row in the UI
# ─────────────────────────────────────────────────────────────────────
async def _load_settings(db, user_id: str) -> dict:
    doc = await db.qoyod_settings.find_one({"user_id": user_id}, {"_id": 0})
    return doc or {}


async def _check_api_key(db, user_id: str) -> dict:
    fp = await get_fingerprint(db, user_id)
    if fp:
        return {"ok": True,
                "detail": f"مفتاح API محفوظ ومُعرَّف ({fp})"}
    return {"ok": False,
            "detail": "لم يُحفظ مفتاح API بعد — انتقل إلى إعدادات قيود."}


def _check_branch(settings: dict) -> dict:
    """Branch is OPTIONAL per user spec 2026-06-27 — many Qoyod accounts
    are single-branch and don't even expose a branches page. When blank,
    we explicitly tell Qoyod nothing and it uses its default branch."""
    if settings.get("default_branch_id"):
        return {"ok": True, "detail": f"الفرع: {settings['default_branch_id']}"}
    return {"ok": True,
            "detail": "اختياري — الحساب أحادي الفرع، سيستخدم قيود الفرع الافتراضي تلقائياً."}


def _check_tax(settings: dict) -> dict:
    if settings.get("default_tax_id"):
        return {"ok": True, "detail": f"الضريبة: {settings['default_tax_id']}"}
    return {"ok": False, "detail": "لم تُحدَّد ضريبة افتراضية في الإعدادات."}


def _check_payment_mapping(settings: dict, unmapped_methods: list[str]) -> dict:
    mapping = settings.get("payment_method_mapping") or []
    if not mapping:
        return {"ok": False, "detail": "لا توجد طرق دفع مربوطة بقيود."}
    if unmapped_methods:
        return {"ok": False,
                "detail": (f"{len(unmapped_methods)} طريقة دفع غير مربوطة: "
                           f"{', '.join(unmapped_methods[:5])}"),
                "extra": {"unmapped": unmapped_methods}}
    return {"ok": True,
            "detail": f"{len(mapping)} طريقة دفع مربوطة بحسابات قيود."}


async def _check_product_mapping(db, user_id: str, eligible_skus: set[str]) -> dict:
    mapped = await db.qoyod_products_mapping.count_documents(
        {"user_id": user_id, "qoyod_product_id": {"$ne": None}})
    if not eligible_skus:
        return {"ok": True,
                "detail": f"{mapped} SKU مربوط مسبقاً — لا توجد طلبات مؤهلة الآن."}
    if mapped == 0:
        return {"ok": False,
                "detail": "لا يوجد أي ربط منتجات بعد — Dry Run سيُنشئ المنتجات."}
    # Count overlap.
    seen = await db.qoyod_products_mapping.distinct(
        "sku", {"user_id": user_id, "sku": {"$in": list(eligible_skus)}})
    return {"ok": True,
            "detail": (f"{len(seen)} من {len(eligible_skus)} SKU مربوطة مسبقاً، "
                       f"الباقي سيُنشأ تلقائياً.")}


async def _check_customer_mapping(db, user_id: str) -> dict:
    mapped = await db.qoyod_customers_mapping.count_documents(
        {"user_id": user_id, "qoyod_customer_id": {"$ne": None}})
    if mapped == 0:
        return {"ok": False,
                "detail": "لا يوجد عملاء مربوطين — Dry Run سيُنشئ العملاء تلقائياً."}
    return {"ok": True, "detail": f"{mapped} عميل مربوط محلياً."}


async def _check_dry_run_proven(db, user_id: str, settings: dict) -> dict:
    """We require BOTH:
       1. Dry Run mode is currently ENABLED (= we haven't flipped to live yet).
       2. At least one row has completed under dry-run (= testing happened).

    Source of truth: `integration_inbox.pipeline_stage == COMPLETED` with
    `dry_run == True`. (The legacy `qoyod_invoices` collection is no
    longer populated by the new pipeline.)"""
    if not settings.get("dry_run_mode"):
        return {"ok": False,
                "detail": ("وضع التشغيل الجاف غير مُفعّل حالياً — يجب أن يكون "
                           "مُفعّلاً قبل التحقق وإلا قد نُرسل فواتير حقيقية بالخطأ.")}
    completed_dry = await db.integration_inbox.count_documents(
        {"user_id": user_id, "dry_run": True,
         "pipeline_stage": "COMPLETED"})
    if completed_dry == 0:
        return {"ok": False,
                "detail": ("لم تكتمل أي تجربة Dry Run بعد — شغّل خط الأنابيب "
                           "على بضعة طلبات أولاً.")}
    return {"ok": True,
            "detail": f"{completed_dry} طلب أكمل التجربة الجافة بنجاح."}


async def _check_outstanding_failures(db, user_id: str, settings: dict | None = None) -> dict:
    """Counts ONLY production failures CREATED AFTER Go-Live activation.

    Pre-Go-Live behaviour (Iter 2026-02-26, refined)
    ─────────────────────────────────────────────────
    Before the operator activates Go-Live (`settings.go_live_activated_at`
    is not set), this check ALWAYS passes. Pre-activation rows are by
    definition test data — even if their `dry_run` flag is missing/wrong
    on legacy rows, they cannot represent "real production failures"
    because production wasn't on yet.

    Post-Go-Live behaviour
    ──────────────────────
    Counts only rows where ALL of:
        • `pipeline_stage ∈ {DEAD_LETTER, PARTIAL_FAILURE}`
        • `dry_run != True` (real production attempts)
        • `received_at >= go_live_activated_at` (the activation watermark)

    This guarantees old test failures (no matter how broken their
    `dry_run` flag) never block re-activation or stay red after a fix.
    """
    if settings is None:
        settings = await _load_settings(db, user_id)

    activated_at = settings.get("go_live_activated_at") \
                   or settings.get("activated_at")   # legacy field
    # Normalise legacy ISO-string activations to datetime for $gte.
    if isinstance(activated_at, str):
        try:
            activated_at = datetime.fromisoformat(
                activated_at.replace("Z", "+00:00"))
        except ValueError:
            activated_at = None

    if not activated_at:
        # No activation yet → no production failures are possible.
        return {"ok": True,
                "detail": ("لم يتم تفعيل الإنتاج بعد. السجلات القديمة "
                           "(ما قبل التفعيل) لا تُحسب كفشل إنتاجي.")}

    stuck = await db.integration_inbox.count_documents({
        "user_id": user_id,
        "pipeline_stage": {"$in": ["DEAD_LETTER", "PARTIAL_FAILURE"]},
        "dry_run": {"$ne": True},
        "received_at": {"$gte": activated_at},
    })
    if stuck:
        return {"ok": False,
                "detail": (f"{stuck} فاتورة إنتاجية فشلت بعد التفعيل "
                           f"({activated_at.isoformat()[:19]}) — راجعها."),
                "extra": {"stuck_count": stuck,
                          "since": activated_at.isoformat()}}
    return {"ok": True,
            "detail": ("لا توجد فواصل إنتاجية عالقة منذ التفعيل. "
                       "فشل Dry Run (إن وُجد) معروض في صفحة المراقبة فقط.")}


async def _check_eligible_orders(
    db, user_id: str, eligible_count: int, settings: dict,
) -> dict:
    """Counts as "eligible":
       a) Rows currently in NORMALIZED / CUSTOMER_RESOLVED matching a
          trigger status (`eligible_count` from the walker).
       b) OR — at least one COMPLETED dry-run row in the last 24h (proof
          the pipeline received & processed a real Salla webhook).

    The (b) clause is critical after the worker fix: rows now drain
    quickly out of NORMALIZED so (a) often shows 0 even though the
    pipeline is healthy. (b) catches "first new completed order"."""
    if eligible_count > 0:
        return {"ok": True,
                "detail": f"{eligible_count} طلب مؤهل في خط الأنابيب جاهز للإرسال."}
    from datetime import timedelta
    recent_cutoff = _now() - timedelta(hours=24)
    triggers = settings.get("invoice_trigger_statuses") or ["completed"]
    recent_completed = await db.integration_inbox.count_documents({
        "user_id": user_id,
        "pipeline_stage": "COMPLETED",
        "dry_run": True,
        "canonical_payload.order_status": {"$in": triggers},
        "received_at": {"$gte": recent_cutoff},
    })
    if recent_completed > 0:
        return {"ok": True,
                "detail": (f"{recent_completed} طلب اكتمل في Dry Run خلال 24 ساعة — "
                           "الـ pipeline نشط وجاهز لاستقبال الطلبات الجديدة.")}
    return {"ok": False,
            "detail": ("لا توجد طلبات مؤهلة في خط الأنابيب ولا اكتمال حديث — "
                       "أرسل Test Payload من Make.com أو تأكّد من حالات تفعيل الفاتورة.")}


async def _check_lookup(api_client, label: str, fn) -> dict:
    if api_client is None:
        return {"ok": False, "detail": f"{label}: مفتاح API غير متاح."}
    try:
        resp = await fn()
        n = 0
        if isinstance(resp, dict):
            # Common Qoyod v2 list shapes
            for k in ("products", "contacts", "data", "items"):
                v = resp.get(k)
                if isinstance(v, list):
                    n = len(v); break
            if n == 0 and isinstance(resp.get("meta"), dict):
                n = resp["meta"].get("total") or 0
        elif isinstance(resp, list):
            n = len(resp)
        return {"ok": True, "detail": f"{label}: {n} موجود في قيود حالياً."}
    except QoyodAPIError as exc:
        return {"ok": False,
                "detail": f"{label}: تعذّر الاستعلام — {exc.code}",
                "extra": exc.to_log_dict()}
    except Exception as exc:   # pragma: no cover — defensive
        return {"ok": False,
                "detail": f"{label}: استثناء {exc.__class__.__name__}"}


# ─────────────────────────────────────────────────────────────────────
# Public — Checklist & Report
# ─────────────────────────────────────────────────────────────────────
async def _collect_eligible_skus_and_methods(
    db, user_id: str, settings: dict,
) -> tuple[set[str], list[str], int, int]:
    """Walk eligible orders once. Returns:
        (eligible_skus, unmapped_payment_methods, eligible_orders_count,
         would_fail_count).
    """
    triggers = settings.get("invoice_trigger_statuses") or ["completed"]
    # Alias-aware mapping lookup (Iter 2026-02-26): a method counts as
    # "mapped" if it has a direct mapping OR its base provider does.
    from integrations.qoyod.payment_methods import resolve_payment_account

    cursor = db.integration_inbox.find(
        {"user_id": user_id,
         "pipeline_stage": {"$in": ["NORMALIZED", "CUSTOMER_RESOLVED"]},
         "canonical_payload.order_status": {"$in": triggers}},
        {"_id": 0, "canonical_payload": 1, "qoyod_customer_id": 1},
    )

    eligible_skus: set[str] = set()
    unmapped: set[str] = set()
    count = 0
    would_fail = 0
    async for row in cursor:
        count += 1
        canonical = row.get("canonical_payload") or {}
        # Collect SKUs.
        for it in canonical.get("items") or []:
            sku = (it.get("sku") or "").strip()
            if sku:
                eligible_skus.add(sku)
        # Track unmapped payment methods (alias-aware).
        pm = (canonical.get("payment_method") or "").lower()
        if pm and not resolve_payment_account(settings, pm):
            unmapped.add(pm)
        # Estimate "would_fail": run preflight with the row's *current*
        # resolution state. Products not resolved yet → that's expected
        # before live; we count it as "needs work" only if the row
        # already passed Customer step AND would fail at invoice today.
        skus = [it.get("sku") for it in canonical.get("items") or []]
        prod_mappings = []
        if skus:
            async for m in db.qoyod_products_mapping.find(
                {"user_id": user_id, "sku": {"$in": skus}},
                {"_id": 0, "sku": 1, "qoyod_product_id": 1},
            ):
                prod_mappings.append(m)
        pf = preflight_run(
            dto_dict=canonical, settings=settings,
            qoyod_customer_id=row.get("qoyod_customer_id"),
            product_resolutions=prod_mappings,
        )
        if not pf.passed:
            would_fail += 1
    return eligible_skus, sorted(unmapped), count, would_fail


async def go_live_checklist(
    db, user_id: str, *, api_client: Optional[QoyodAPIClient] = None,
) -> dict:
    """Run all 11 checks. Returns a structure the UI renders verbatim."""
    settings = await _load_settings(db, user_id)
    eligible_skus, unmapped_methods, eligible_count, would_fail = \
        await _collect_eligible_skus_and_methods(db, user_id, settings)

    # Build/share an api_client only once for the two lookup checks.
    created_client = False
    if api_client is None:
        key = await get_api_key(db, user_id)
        if key:
            api_client = QoyodAPIClient(key)
            created_client = True

    items = [
        {"key": "api_key",           "label": "مفتاح Qoyod API",
         **(await _check_api_key(db, user_id))},
        {"key": "branch",            "label": "الفرع الافتراضي",
         **_check_branch(settings)},
        {"key": "tax",               "label": "الضريبة الافتراضية",
         **_check_tax(settings)},
        {"key": "payment_mapping",   "label": "ربط طرق الدفع",
         **_check_payment_mapping(settings, unmapped_methods)},
        {"key": "product_mapping",   "label": "ربط المنتجات (Local)",
         **(await _check_product_mapping(db, user_id, eligible_skus))},
        {"key": "customer_mapping",  "label": "ربط العملاء (Local)",
         **(await _check_customer_mapping(db, user_id))},
        {"key": "dry_run",           "label": "تجربة Dry Run موثّقة",
         **(await _check_dry_run_proven(db, user_id, settings))},
        {"key": "outstanding_failures", "label": "لا فواصل إنتاجية عالقة",
         **(await _check_outstanding_failures(db, user_id, settings))},
        {"key": "eligible_orders",   "label": "وجود طلبات مؤهلة",
         **(await _check_eligible_orders(db, user_id, eligible_count, settings))},
        {"key": "products_lookup",   "label": "استعلام منتجات قيود",
         **(await _check_lookup(
             api_client, "منتجات قيود",
             (lambda: api_client.list_products(limit=1)) if api_client else (lambda: None)))},
        {"key": "customers_lookup",  "label": "استعلام عملاء قيود",
         **(await _check_lookup(
             api_client, "عملاء قيود",
             (lambda: api_client.list_contacts(limit=1)) if api_client else (lambda: None)))},
    ]
    all_passed = all(i["ok"] for i in items)
    return {
        "schema_version": 1,
        "generated_at":   _now(),
        "all_passed":     all_passed,
        "items":          items,
        "totals": {
            "passed": sum(1 for i in items if i["ok"]),
            "failed": sum(1 for i in items if not i["ok"]),
            "checks": len(items),
        },
        "context": {
            "dry_run_mode_currently_on": bool(settings.get("dry_run_mode")),
            "enabled_currently":         bool(settings.get("enabled")),
            "would_fail_count":          would_fail,
            "unmapped_payment_methods":  unmapped_methods,
        },
    }


async def go_live_report(
    db, user_id: str, *, api_client: Optional[QoyodAPIClient] = None,
) -> dict:
    """Quantitative pre-flight report. The numbers the operator stares at
    before clicking ACTIVATE."""
    settings = await _load_settings(db, user_id)
    eligible_skus, unmapped_methods, eligible_count, would_fail = \
        await _collect_eligible_skus_and_methods(db, user_id, settings)

    # Products: mapped vs needing creation in qoyod_products_mapping.
    products_already_local = await db.qoyod_products_mapping.count_documents(
        {"user_id": user_id, "qoyod_product_id": {"$ne": None}})
    if eligible_skus:
        mapped_skus = set(await db.qoyod_products_mapping.distinct(
            "sku", {"user_id": user_id,
                    "sku": {"$in": list(eligible_skus)}}))
        products_needing_creation = len(eligible_skus - mapped_skus)
    else:
        products_needing_creation = 0

    # Customers: count distinct lookup keys across eligible rows that
    # are NOT already in qoyod_customers_mapping.
    customers_already_local = await db.qoyod_customers_mapping.count_documents(
        {"user_id": user_id, "qoyod_customer_id": {"$ne": None}})

    # Get distinct customer lookup keys from eligible rows.
    triggers = settings.get("invoice_trigger_statuses") or ["completed"]
    pipe = [
        {"$match": {"user_id": user_id,
                    "pipeline_stage": {"$in": ["NORMALIZED", "CUSTOMER_RESOLVED"]},
                    "canonical_payload.order_status": {"$in": triggers}}},
        {"$project": {
            "phone": "$canonical_payload.customer.phone",
            "email": "$canonical_payload.customer.email",
        }},
    ]
    eligible_lookups: set[str] = set()
    async for d in db.integration_inbox.aggregate(pipe):
        k = d.get("phone") or d.get("email")
        if k:
            eligible_lookups.add(k)
    if eligible_lookups:
        mapped_lookups = set(await db.qoyod_customers_mapping.distinct(
            "lookup_key", {"user_id": user_id,
                           "lookup_key": {"$in": list(eligible_lookups)}}))
        customers_needing_creation = len(eligible_lookups - mapped_lookups)
    else:
        customers_needing_creation = 0

    # Optional: query Qoyod for global counts.
    qoyod_products_count: Optional[int] = None
    qoyod_contacts_count: Optional[int] = None
    if api_client is None:
        key = await get_api_key(db, user_id)
        if key:
            api_client = QoyodAPIClient(key)
    if api_client is not None:
        try:
            resp = await api_client.list_products(limit=1)
            if isinstance(resp, dict) and isinstance(resp.get("meta"), dict):
                qoyod_products_count = resp["meta"].get("total")
        except QoyodAPIError:
            pass
        try:
            resp = await api_client.list_contacts(limit=1)
            if isinstance(resp, dict) and isinstance(resp.get("meta"), dict):
                qoyod_contacts_count = resp["meta"].get("total")
        except QoyodAPIError:
            pass

    return {
        "schema_version":               1,
        "generated_at":                 _now(),
        "eligible_orders_count":        eligible_count,
        "products_needing_creation":    products_needing_creation,
        "products_already_in_qoyod":    products_already_local,
        "qoyod_products_total":         qoyod_products_count,
        "customers_needing_creation":   customers_needing_creation,
        "customers_already_local":      customers_already_local,
        "qoyod_contacts_total":         qoyod_contacts_count,
        "unmapped_payment_methods":     unmapped_methods,
        "unmapped_payment_methods_count": len(unmapped_methods),
        "would_fail_if_live_now":       would_fail,
        "dry_run_mode_currently_on":    bool(settings.get("dry_run_mode")),
    }


# ─────────────────────────────────────────────────────────────────────
# Activation — re-verifies the checklist BEFORE flipping settings
# ─────────────────────────────────────────────────────────────────────
class ActivationBlocked(RuntimeError):
    def __init__(self, reasons: list[str], items: list[dict]):
        super().__init__("activation_blocked")
        self.reasons = reasons
        self.items = items


async def activate_production_mode(
    db, user_id: str, *, api_client: Optional[QoyodAPIClient] = None,
) -> dict:
    """Atomically flip `dry_run_mode=false` + `enabled=true` IFF every
    checklist item passes. Raises `ActivationBlocked` otherwise — the
    operator must fix the failing items first.
    """
    checklist = await go_live_checklist(db, user_id, api_client=api_client)
    if not checklist["all_passed"]:
        failing = [i["label"] for i in checklist["items"] if not i["ok"]]
        raise ActivationBlocked(reasons=failing, items=checklist["items"])

    activated_at = _now()
    await db.qoyod_settings.update_one(
        {"user_id": user_id},
        # Write BOTH the new canonical field and the legacy one so any
        # older code/UI reading either continues to work.
        {"$set": {"enabled": True, "dry_run_mode": False,
                  "go_live_activated_at": activated_at,
                  "activated_at": activated_at}},
        upsert=True,
    )
    return {
        "ok": True,
        "activated_at": activated_at,
        "go_live_activated_at": activated_at,
        "checklist_snapshot": checklist,
    }
