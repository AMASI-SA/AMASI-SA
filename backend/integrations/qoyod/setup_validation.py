"""Qoyod Settings — final-setup validation surface.

Purpose
───────
The Qoyod Settings page is meant to be a *one-time setup* page. Before
flipping Go-Live the operator must complete every required field. This
module powers two helper endpoints used exclusively by that page:

  • `GET /api/integrations/qoyod/payment-methods/used`
      Collects the DISTINCT payment-method keys that have actually
      appeared on this tenant's orders (both `unified_orders` and the
      `integration_inbox` canonical payloads). The UI shows these as
      mandatory rows in the Payment Method Mapping table.

  • `GET /api/integrations/qoyod/setup/validate`
      Runs every settings-page validation in one shot and returns a
      flat list of issues the operator must resolve before saving.

Both functions are STRICTLY read-only and never mutate Qoyod state.
"""
from __future__ import annotations

from typing import Any, Iterable

from integrations.qoyod.normalizer import _canonical_payment_method


# ─────────────────────────────────────────────────────────────────────
# Canonical payment-method catalogue surfaced in the UI dropdown.
# Order matches the user spec (2026-06-27): mada, Apple Pay, Visa/MC,
# STC Pay, Bank Transfer, Tamara, Tabby, Emkan, COD.
# Each row pairs the canonical key (matched by `invoice_builder` at
# receipt time) with an Arabic label for the operator.
# ─────────────────────────────────────────────────────────────────────
CANONICAL_PAYMENT_METHODS: list[dict[str, str]] = [
    {"key": "mada",          "label_ar": "مدى"},
    {"key": "apple_pay",     "label_ar": "Apple Pay"},
    {"key": "visa",          "label_ar": "Visa"},
    {"key": "mastercard",    "label_ar": "Mastercard"},
    {"key": "credit_card",   "label_ar": "بطاقة ائتمان (موحّد)"},
    {"key": "stc_pay",       "label_ar": "STC Pay"},
    {"key": "bank_transfer", "label_ar": "تحويل بنكي"},
    {"key": "tamara",        "label_ar": "تمارا"},
    {"key": "tabby",         "label_ar": "تابي"},
    {"key": "emkan",         "label_ar": "إمكان"},
    {"key": "cod",           "label_ar": "الدفع عند الاستلام"},
]


def _norm(v: Any) -> str:
    """Apply the same canonical transform used by the normalizer so the
    UI-side keys always match the keys the receipt builder compares
    against."""
    if not v:
        return ""
    out = _canonical_payment_method(str(v))
    return (out or "").strip().lower()


async def collect_used_payment_methods(db, *, user_id: str) -> list[dict]:
    """Returns one row per DISTINCT canonical payment method observed
    on this tenant's data. Each row: `{key, label_ar, count, sources}`.

    Sources scanned:
      1. `unified_orders.payment_method`
      2. `unified_orders.raw.payment_method`
      3. `integration_inbox.canonical_payload.payment_method`
    """
    by_key: dict[str, dict] = {}

    def _bump(key: str, native: str | None, source: str) -> None:
        if not key:
            return
        row = by_key.setdefault(key, {
            "key":      key,
            "label_ar": _label_for(key),
            "native_examples": set(),
            "count":    0,
            "sources":  set(),
        })
        row["count"] += 1
        row["sources"].add(source)
        if native:
            row["native_examples"].add(native[:60])

    # Source 1: unified_orders.payment_method
    async for o in db.unified_orders.find(
        {"user_id": user_id},
        {"payment_method": 1, "raw.payment_method": 1, "_id": 0},
    ):
        pm  = (o.get("payment_method") or "").strip()
        if pm:
            _bump(_norm(pm), pm, "unified_orders")
        raw = (o.get("raw") or {})
        pm2 = (raw.get("payment_method") or "").strip()
        if pm2 and pm2 != pm:
            _bump(_norm(pm2), pm2, "unified_orders.raw")

    # Source 2: integration_inbox canonical payloads
    async for r in db.integration_inbox.find(
        {"user_id": user_id,
         "canonical_payload.payment_method": {"$nin": [None, ""]}},
        {"canonical_payload.payment_method": 1,
         "canonical_payload.payment_method_native": 1, "_id": 0},
    ):
        cp = r.get("canonical_payload") or {}
        key = (cp.get("payment_method") or "").strip().lower()
        if key:
            _bump(key, cp.get("payment_method_native") or key, "inbox")

    rows = list(by_key.values())
    # Stable order: highest count first.
    rows.sort(key=lambda r: (-r["count"], r["key"]))
    # Serialise sets for JSON
    for r in rows:
        r["sources"] = sorted(r["sources"])
        r["native_examples"] = sorted(r["native_examples"])
    return rows


def _label_for(key: str) -> str:
    for row in CANONICAL_PAYMENT_METHODS:
        if row["key"] == key:
            return row["label_ar"]
    return key


# ─────────────────────────────────────────────────────────────────────
# Settings validation
# ─────────────────────────────────────────────────────────────────────
def _ensure_iter(v: Any) -> list:
    if isinstance(v, list):
        return v
    return []


async def validate_settings_for_setup(db, *, user_id: str) -> dict:
    """Run every Settings-page check. Returns:

        {
          "ok":     bool,           # True = ready to save & go to Dry Run
          "issues": [               # ordered by severity
            {"code": str, "field": str,
             "severity": "blocker"|"warning",
             "message": str}
          ],
          "context": {
            "used_payment_methods": [...keys...],
            "mapped_payment_methods": [...keys...],
            "missing_payment_methods": [...keys...],
            "product_type": "service"|"inventory"|"per_product",
          }
        }
    """
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}

    issues: list[dict] = []

    # 1) Branch ID — optional (some Qoyod accounts are single-branch
    #    and don't expose a branch picker). Treated as a WARNING so the
    #    operator can save & proceed; the invoice builder omits the
    #    field when None.
    if not (settings.get("default_branch_id") or "").strip():
        issues.append({
            "code": "missing_branch_id",
            "field": "default_branch_id",
            "severity": "warning",
            "message": ("لم يُحدَّد Branch ID. اختياري إذا كان حسابك "
                        "بفرع واحد فقط — في هذه الحالة سيستخدم قيود "
                        "الفرع الافتراضي تلقائياً."),
        })

    # 2) Tax ID
    if not (settings.get("default_tax_id") or "").strip():
        issues.append({
            "code": "missing_tax_id",
            "field": "default_tax_id",
            "severity": "blocker",
            "message": ("لم يُحدَّد Tax ID. ادخل قيود → الإعدادات → "
                        "الضرائب → انسخ رقم معرّف ضريبة VAT 15%."),
        })

    # 3) Payment-method mapping must cover every USED method.
    used_rows = await collect_used_payment_methods(db, user_id=user_id)
    used_keys = {r["key"] for r in used_rows if r.get("key")}
    mapping = _ensure_iter(settings.get("payment_method_mapping"))
    mapped_keys = {
        (m.get("salla_method") or "").strip().lower()
        for m in mapping
        if (m.get("salla_method") or "").strip()
        and (m.get("qoyod_account_id") or "").strip()
    }
    missing = sorted(used_keys - mapped_keys)
    if missing:
        labels = ", ".join(_label_for(k) for k in missing[:5])
        more = f" (+{len(missing)-5} غيرها)" if len(missing) > 5 else ""
        issues.append({
            "code": "unmapped_payment_methods",
            "field": "payment_method_mapping",
            "severity": "blocker",
            "message": (f"{len(missing)} طريقة دفع مُستخدمة في طلباتك "
                        f"غير مربوطة بحساب قيود: {labels}{more}."),
            "extra": {"missing": missing},
        })

    # 4) Inventory-mode required accounts
    ptype = settings.get("default_product_type") or "service"
    if ptype == "inventory":
        if not (settings.get("inventory_account_id") or "").strip():
            issues.append({
                "code": "missing_inventory_account",
                "field": "inventory_account_id",
                "severity": "blocker",
                "message": ("وضع المنتجات = Inventory يتطلب حساب المخزون "
                            "(Inventory Account ID) من دليل حسابات قيود."),
            })
        if not (settings.get("cost_account_id") or "").strip():
            issues.append({
                "code": "missing_cost_account",
                "field": "cost_account_id",
                "severity": "blocker",
                "message": ("وضع المنتجات = Inventory يتطلب حساب التكلفة "
                            "(Cost of Goods Sold Account ID)."),
            })

    # 5) Soft warning: no default customer for guests
    if not (settings.get("default_customer_id") or "").strip():
        issues.append({
            "code": "missing_default_customer",
            "field": "default_customer_id",
            "severity": "warning",
            "message": ("لم يُحدَّد عميل افتراضي للضيوف. سيُنشأ عميل جديد "
                        "في قيود لكل طلب ضيف بدون هاتف/إيميل. اختياري."),
        })

    blockers = [i for i in issues if i["severity"] == "blocker"]
    return {
        "schema_version": 1,
        "ok":     len(blockers) == 0,
        "issues": issues,
        "context": {
            "product_type": ptype,
            "used_payment_methods":    sorted(used_keys),
            "mapped_payment_methods":  sorted(mapped_keys),
            "missing_payment_methods": missing,
            "blocker_count": len(blockers),
            "warning_count": sum(
                1 for i in issues if i["severity"] == "warning"),
        },
    }
