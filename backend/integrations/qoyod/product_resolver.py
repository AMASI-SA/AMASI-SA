"""Qoyod Product Resolution (Step 4b) — `CUSTOMER_RESOLVED → PRODUCT_RESOLVED`.

SSOT (Single Source Of Truth) for products at runtime
─────────────────────────────────────────────────────
The runtime pipeline uses **Mezan + Salla** as the SSOT for products.
It does NOT read from the migration snapshot collections
(`qoyod_external_products`, `qoyod_migration_products`) — those are
review-only artefacts populated by the «مرحلة الانتقال» page.

For each line item:
    1. Hit `qoyod_products_mapping` by `sku` (the runtime mapping table).
    2. On miss → SSOT trust gate (see below) → POST /products to Qoyod.
    3. Persist the new mapping for next time.

Failures route to FAILED_PRODUCT → DEAD_LETTER (no PARTIAL_FAILURE
at this stage — nothing has been written to Qoyod yet).

SSOT Trust Gate (2026-02-27)
────────────────────────────
Historical Qoyod tenants frequently contain dozens of legacy products
from old Salla syncs, manual data entry, or other connectors. The
resolver MUST NOT silently bind a new order to those historical rows.

Before creating a product, the gate queries Qoyod for the SKU:
  • Mezan mapping HIT                → use it (happy path).
  • Mezan mapping MISS + Qoyod NONE  → create fresh.
  • Mezan mapping MISS + Qoyod HIT
      AND settings.block_untrusted_existing_products is True (default)
                                     → fail with `qoyod_existing_untrusted`.
      AND settings.block_untrusted_existing_products is False
                                     → adopt + log audit trail.

To onboard a historical product into Mezan, the operator must call
`POST /api/integrations/qoyod/products/adopt` (manual review), which
inserts the row into `qoyod_products_mapping` with `adopted=True` so
the gate stops blocking it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.api_client import QoyodAPIError
from integrations.qoyod.write_lock import QoyodWriteLockedError


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class ProductResolutionItem:
    sku: str
    qoyod_product_id: Optional[str] = None
    created_new: bool = False
    error: Optional[dict] = None
    # SSOT gate metadata — surfaces "yes this came from Mezan" vs
    # "we adopted a legacy Qoyod row by operator action".
    trust_source: Optional[str] = None    # "mezan" | "adopted" | "created"


@dataclass
class ProductsResolutionResult:
    success: bool
    items: list[ProductResolutionItem] = field(default_factory=list)
    error: Optional[dict] = None     # first failure that flipped success=False

    def to_log_dict(self) -> dict:
        return {
            "success": self.success,
            "items": [{"sku": i.sku,
                       "qoyod_product_id": i.qoyod_product_id,
                       "created_new": i.created_new,
                       "trust_source": i.trust_source,
                       "error": i.error}
                      for i in self.items],
            "error": self.error,
        }


def _build_product_payload(item: dict, settings: dict) -> dict:
    """Map a DTO LineItem (as dict) → Qoyod /products POST body.

    Iter-287 — required Qoyod product fields
    ────────────────────────────────────────
    Qoyod's `/products` REQUIRES (post-`sale_item:1` activation):
      • `category_id`          — product category in the merchant's Qoyod
      • `tax_id`               — the tax record applied at sale time
      • `product_unit_type_id` — e.g. "piece" / "service hour"
      • `sales_account_id`     — GL account credited on each sale
    All four must come from Mezan settings:
      settings.default_product_category_id
      settings.default_product_tax_id
      settings.default_product_unit_type_id
      settings.default_sales_account_id
    `validate_product_defaults(settings)` enforces presence BEFORE any
    POST so we never hit the 422.

    Iter-286 — corrected activation flags
    ─────────────────────────────────────
    Qoyod's `/products` endpoint uses the snake_case integer-flag
    convention (`sale_item: 1`, `purchase_item: 0`), NOT the boolean
    Rails-style `is_sold`/`is_bought` shape.

    A self-healing 422 retry path lives in `resolve_products`
    (Iter-286): if Qoyod still complains about prices we fall back to
    the smallest viable payload — `name, sku, sale_item=1,
    selling_price` PLUS the four required Iter-287 ids — exactly once.
    """
    # Coerce to float so we never accidentally send a string-typed
    # price. Qoyod requires `selling_price` whenever `sale_item: 1`.
    raw_price = item.get("unit_price")
    try:
        selling_price = float(raw_price) if raw_price is not None else 0.0
    except (TypeError, ValueError):
        selling_price = 0.0

    ptype = (settings.get("default_product_type") or "service")
    payload = {
        "name":              item.get("name") or item.get("sku") or "منتج",
        "sku":               item.get("sku"),
        "type":              ptype,
        "is_non_stock":      ptype == "service",
        # Iter-286 — integer-flag activation per Qoyod live API.
        "sale_item":         1,
        "purchase_item":     0,
        "selling_price":     selling_price,
    }
    # Iter-287 — Qoyod-required ids (validated upstream by
    # validate_product_defaults). Stamp every key explicitly so audit
    # readers know which settings drove the create.
    _stamp_required_ids(payload, settings)
    return {"product": payload}


def _build_product_payload_fallback(item: dict, settings: dict) -> dict:
    """Minimal-fields product payload used by the 422 self-healing
    retry (Iter-286). Strips everything except the four fields Qoyod's
    validator absolutely needs: name, sku, sale_item, selling_price.
    Plus Iter-287's required tenant ids (without which Qoyod always
    rejects). No type/is_non_stock/purchase_item — let Qoyod default.

    Iter-290g — Bump zero-price products to 1.0 SAR for *product
    creation only*. Some Qoyod tenants refuse `selling_price=0` with
    "enter at least a sales price". The invoice line still uses the
    real Salla price (0 or the discounted figure under
    match_salla_total) — this fallback affects ONLY the catalog row.
    """
    raw_price = item.get("unit_price")
    try:
        selling_price = float(raw_price) if raw_price is not None else 0.0
    except (TypeError, ValueError):
        selling_price = 0.0
    if selling_price <= 0:
        selling_price = 1.0
    payload = {
        "name":          item.get("name") or item.get("sku") or "منتج",
        "sku":           item.get("sku"),
        "sale_item":     1,
        "selling_price": selling_price,
    }
    _stamp_required_ids(payload, settings)
    return {"product": payload}


# ─────────────────────────────────────────────────────────────────────
# Iter-287 — Qoyod-side required product defaults
# ─────────────────────────────────────────────────────────────────────
REQUIRED_PRODUCT_DEFAULT_KEYS = (
    "default_product_category_id",
    "default_product_tax_id",
    "default_product_unit_type_id",
    "default_sales_account_id",
)

REQUIRED_PRODUCT_DEFAULT_LABELS_AR = {
    "default_product_category_id":  "التصنيف (category_id)",
    "default_product_tax_id":       "الضريبة (tax_id)",
    "default_product_unit_type_id": "وحدة القياس (product_unit_type_id)",
    "default_sales_account_id":     "حساب المبيعات (sales_account_id)",
}


def item_unit_price(item: dict) -> float:
    """Iter-290g — Defensively extract a numeric unit_price from a DTO
    line item. Returns 0.0 for missing/garbage input so the diagnostic
    message can still attribute a price to the failing SKU even when
    the upstream data is broken."""
    raw = item.get("unit_price")
    try:
        return float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _coerce_id_to_int(v: Any) -> Optional[int]:
    """Iter-290g — Coerce a Qoyod id setting to a clean integer when
    possible. Returns `None` ONLY when the value is empty / unusable.

    Handles three realistic UI shapes:
      • Scalar string  ("1" / " 1 ")        → 1
      • Scalar number  (1, 1.0)             → 1
      • List/tuple     (["1"], ["1","2"])   → 1   (first non-empty)
      • Empty/None                          → None

    NOTE on non-numeric strings (e.g. "CAT-99"): some test fixtures and
    legacy ports use string identifiers. We DO NOT refuse them here —
    `_unwrap_id_for_payload` falls back to passing them through as the
    stripped string. Qoyod will surface its own validator response if
    such an id is invalid in the live API. This matches the user's
    Iter-290g brief which only mandated the SHAPE fix (array → scalar)
    and integer coercion as a *best effort*.
    """
    # Unwrap list/tuple (multiselect or accidental array shape).
    if isinstance(v, (list, tuple)):
        for el in v:
            res = _coerce_id_to_int(el)
            if res is not None:
                return res
        return None
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        f = float(s)
        return int(f)
    except (TypeError, ValueError):
        return None


def _unwrap_id_for_payload(v: Any) -> Any:
    """Iter-290g — Resolve a Qoyod id setting to the scalar value the
    POST body should carry. Always returns a scalar (never a list).

    Priority:
      1. Multi-element array  → take the first non-empty element.
      2. If it parses cleanly to an integer        → return that int.
      3. Else                                       → return the
         stripped non-empty string (legacy compatibility).
      4. Empty / unusable                           → return None
         (caller drops the key from the payload).
    """
    if isinstance(v, (list, tuple)):
        # Pick the first non-empty element. Recurse if it's nested.
        for el in v:
            r = _unwrap_id_for_payload(el)
            if r not in (None, ""):
                return r
        return None
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return s


def _is_array_shape(v: Any) -> bool:
    """True when `v` is a list/tuple containing more than one element
    that resolves to a different id. Used by the preflight to surface
    `product_payload_invalid_id_shape` BEFORE the POST — so a multi-
    valued multiselect doesn't silently get its first element used."""
    if not isinstance(v, (list, tuple)):
        return False
    seen: set[int] = set()
    for el in v:
        i = _coerce_id_to_int(el)
        if i is not None:
            seen.add(i)
    return len(seen) > 1


def _stamp_required_ids(product: dict, settings: dict) -> None:
    """Mutates `product` in place — adds the four Qoyod-required ids
    drawn from settings as SCALAR values (int when possible, str
    otherwise) per Iter-290g.

    Empty/missing values are dropped (the preflight in
    `validate_product_defaults` will refuse the row upstream so we
    never actually POST with a missing id).
    """
    cat   = _unwrap_id_for_payload(settings.get("default_product_category_id"))
    tax   = _unwrap_id_for_payload(settings.get("default_product_tax_id"))
    unit  = _unwrap_id_for_payload(settings.get("default_product_unit_type_id"))
    acct  = _unwrap_id_for_payload(settings.get("default_sales_account_id"))
    if cat is not None:
        product["category_id"] = cat
    if tax is not None:
        # Iter-290g — Qoyod's live `/products` validator returns
        # `{'tax_id': ['Please select taxes']}` when we send an array
        # (Iter-289 was incorrect — confirmed against production
        # 2026-02-28 with order 268784455 SKU=AMS11542). The correct
        # shape is a SCALAR (int when numeric, else string).
        product["tax_id"] = tax
    if unit is not None:
        product["product_unit_type_id"] = unit
    if acct is not None:
        product["sales_account_id"] = acct


def validate_product_defaults(settings: dict) -> tuple[bool, list[str]]:
    """Iter-287 preflight — verifies the four Qoyod-required product
    settings are configured. Iter-290g — accepts any non-empty unwrapped
    value (int, numeric string, single-element multiselect), refusing
    only empty / None / empty-array / unusable shapes.
    Returns `(ok, missing_label_keys)`.
    """
    missing: list[str] = []
    for k in REQUIRED_PRODUCT_DEFAULT_KEYS:
        v = _unwrap_id_for_payload(settings.get(k))
        if v is None or (isinstance(v, str) and not v.strip()):
            missing.append(k)
    return (not missing), missing


def validate_product_id_shapes(settings: dict) -> tuple[bool, list[dict]]:
    """Iter-290g — Refuse multi-element arrays in any of the four
    product-create id settings. A multiselect widget delivering
    `["1", "2"]` would silently pick the first id, which is the kind
    of footgun that destroys accounting. We surface a structured
    error instead so the operator fixes the Setting before any POST.

    Returns `(ok, offenders)` where `offenders` is a list of dicts
    `{"field": key, "value": raw_value, "issue": "multi_element_array"}`.
    """
    offenders: list[dict] = []
    for k in REQUIRED_PRODUCT_DEFAULT_KEYS:
        raw = settings.get(k)
        if _is_array_shape(raw):
            offenders.append({
                "field": k,
                "value": raw,
                "issue": "multi_element_array",
            })
    return (not offenders), offenders


def build_invalid_id_shape_error(offenders: list[dict]) -> dict:
    """Structured error used when `validate_product_id_shapes` fails.
    Mirrors the Arabic wording of `build_missing_product_defaults_error`
    so the operator-facing UX is consistent."""
    fields_ar = [REQUIRED_PRODUCT_DEFAULT_LABELS_AR.get(o["field"], o["field"])
                 for o in offenders]
    return {
        "code":             "product_payload_invalid_id_shape",
        "failed_at_stage":  "PREFLIGHT_PRODUCT_DEFAULTS",
        "offenders":        offenders,
        "message": (
            "إعدادات إنشاء المنتجات تحتوي قيماً غير مفردة "
            "(مصفوفات متعددة العناصر): " + "، ".join(fields_ar)
            + ". قيود يتطلّب رقماً واحداً لكل من هذه الحقول. "
            "افتح صفحة الإعدادات وحدّد قيمة واحدة فقط."
        ),
    }


def build_missing_product_defaults_error(missing_keys: list[str]) -> dict:
    """Structured error returned to the orchestrator when the
    preflight fails. The Arabic message lists the missing settings
    so the operator knows exactly which fields to fill in.
    """
    labels = [REQUIRED_PRODUCT_DEFAULT_LABELS_AR.get(k, k)
              for k in missing_keys]
    return {
        "code":             "missing_qoyod_product_defaults",
        "failed_at_stage":  "PREFLIGHT_PRODUCT_DEFAULTS",
        "missing":          missing_keys,
        "message": ("إعدادات إنشاء المنتجات في قيود ناقصة: "
                    + "، ".join(labels)),
    }


def _extract_product_id(resp: Any) -> Optional[str]:
    if not isinstance(resp, dict):
        return None
    if "product" in resp and isinstance(resp["product"], dict):
        pid = resp["product"].get("id")
        if pid is not None:
            return str(pid)
    pid = resp.get("id") or resp.get("product_id")
    return str(pid) if pid is not None else None


def _untrusted_error(sku: str, qoyod_product: dict) -> dict:
    """Build the `qoyod_existing_untrusted` error payload. Includes
    enough detail for the operator to decide: adopt or archive."""
    return {
        "code":    "qoyod_existing_untrusted",
        "message": (f"SKU '{sku}' موجود في قيود (product_id="
                    f"{qoyod_product.get('id')}) لكنه غير مربوط محلياً "
                    "في ميزان. لمنع ربط فاتورة جديدة بمنتج تاريخي "
                    "مجهول المصدر، تم إيقاف المعالجة. "
                    "اعتمد المنتج عبر "
                    "POST /api/integrations/qoyod/products/adopt أو "
                    "أرشفه في قيود."),
        "qoyod_product_id":   str(qoyod_product.get("id")),
        "qoyod_product_name": (qoyod_product.get("name")
                               or qoyod_product.get("name_ar")
                               or qoyod_product.get("name_en")),
        "qoyod_product_sku":  qoyod_product.get("sku")
                              or qoyod_product.get("reference"),
        "remediation":        "adopt_or_archive",
    }


# ─────────────────────────────────────────────────────────────────────
# System-product SKU blocklist (Iter-267, user directive 2026-02-27)
# ─────────────────────────────────────────────────────────────────────
# These SKUs belong to Qoyod's internal accounting plumbing (or to
# other connectors that pre-populated the tenant). They MUST NOT be
# bound to real order line items — not even via the adoption flow.
# A Salla order arriving with one of these SKUs is anomalous and is
# refused outright (FAILED_PRODUCT → DEAD_LETTER).
#
# Must stay in sync with `identity_diagnostics._SYSTEM_SKU_EXACT/PREFIXES`
# — the diagnostic UI uses the same set to badge rows as "نظامي".
_SYSTEM_SKU_EXACT = frozenset({
    "cod_item", "custom_product", "shipping_fee", "delivery_fee",
    "discount_item", "tax_item", "rounding_item", "fees_item",
})
_SYSTEM_SKU_PREFIXES = ("shipping_", "delivery_", "fees_", "tax_", "system_")


def _is_system_sku(sku: str) -> bool:
    s = (sku or "").strip().lower()
    if not s:
        return False
    if s in _SYSTEM_SKU_EXACT:
        return True
    return any(s.startswith(p) for p in _SYSTEM_SKU_PREFIXES)


def _system_sku_error(sku: str) -> dict:
    return {
        "code":    "system_product_sku_refused",
        "message": (f"SKU '{sku}' محجوز للنظام (مثل cod_item / "
                    "custom_product / shipping_fee). لا يُسمح بربط "
                    "طلب جديد بمنتج نظامي — هذا منع نهائي ولا "
                    "يقبل adoption. راجع بيانات الطلب في سلة "
                    "وصحّح الـSKU."),
        "sku":            sku,
        "remediation":    "fix_source_sku",
    }


async def resolve_products(
    db, user_id: str, dto_items: list[dict], settings: dict,
    *, trace_id: str, api_client,
) -> ProductsResolutionResult:
    result = ProductsResolutionResult(success=True)
    # Default trust gate to ON — operator must explicitly opt out via
    # settings.block_untrusted_existing_products = False. This protects
    # tenants whose Qoyod account already holds historical products
    # (cod_item, custom_product, legacy Salla SKUs, etc.).
    trust_gate_on = settings.get("block_untrusted_existing_products", True)

    for it in dto_items:
        sku = (it.get("sku") or "").strip()
        if not sku:
            result.success = False
            result.error = {"code": "missing_sku",
                            "message": "line item has no sku"}
            result.items.append(ProductResolutionItem(
                sku="", error=result.error))
            return result

        # ─── System-SKU block ──────────────────────────────────────
        # Hard refusal BEFORE the local-mapping lookup. Even if some
        # legacy import accidentally inserted a `cod_item` mapping,
        # we refuse the order — system SKUs must never bind to real
        # invoices.
        if _is_system_sku(sku):
            err = _system_sku_error(sku)
            result.success = False
            result.error = err
            result.items.append(ProductResolutionItem(sku=sku, error=err))
            return result

        existing = await db.qoyod_products_mapping.find_one(
            {"user_id": user_id, "sku": sku},
            {"_id": 0, "qoyod_product_id": 1, "adopted": 1,
             "dry_run_only": 1},
        )
        existing_pid = existing.get("qoyod_product_id") if existing else None
        # ─── DRY-Run Leak Guard (Iter-267, P0) ─────────────────────
        # Any mapping carrying a `DRY:*` id is a Dry-Run artefact and
        # MUST NOT bind to a production invoice. Production order
        # 268670571 hit this on 2026-02-27. We treat the mapping as
        # absent: the resolver falls through to the Trust Gate +
        # create-fresh path. The existing row is also marked
        # `dry_run_only=true` so the operator can audit what was
        # quarantined.
        if existing_pid and (str(existing_pid).startswith("DRY:")
                             or existing.get("dry_run_only")):
            await db.qoyod_products_mapping.update_one(
                {"user_id": user_id, "sku": sku},
                {"$set": {"dry_run_only": True,
                          "quarantined_at": _now(),
                          "quarantine_reason": "dry_run_id_in_production"}},
            )
            existing = None   # fall-through to create-fresh
        elif existing_pid:
            result.items.append(ProductResolutionItem(
                sku=sku,
                qoyod_product_id=str(existing_pid),
                created_new=False,
                trust_source="adopted" if existing.get("adopted") else "mezan",
            ))
            continue

        # ─── SSOT Trust Gate / Auto-Adopt (Iter-288) ─────────────────
        # Always look up the SKU in Qoyod BEFORE attempting to create.
        # Behaviour on a hit depends on `auto_adopt_existing_qoyod_products`:
        #   • true  (DEFAULT for trial Iter-288): treat a single-SKU
        #     match as the canonical Qoyod row, write a local mapping
        #     marked `auto_adopted_from_qoyod`, and reuse the existing
        #     `qoyod_product_id`. NO `POST /products` is fired.
        #   • false (strict Trust Gate, original behaviour): refuse with
        #     `untrusted_qoyod_product_match` — operator must adopt manually.
        # In BOTH modes, a multi-row match aborts the resolver with
        # `duplicate_qoyod_sku` so the operator can clean up Qoyod.
        if trust_gate_on:
            try:
                # Iter-288 — prefer the multi-row lookup; fall back to
                # the legacy single-row method for test stubs / older
                # api_client builds that only expose find_product_by_sku.
                if hasattr(api_client, "find_all_products_by_sku"):
                    qoyod_matches = await api_client.find_all_products_by_sku(sku)
                else:
                    single = await api_client.find_product_by_sku(sku)
                    qoyod_matches = [single] if single else []
            except QoyodAPIError as exc:
                # Lookup failed → be strict and refuse. Treat as
                # transient: caller will retry / dead-letter as usual.
                err = exc.to_log_dict()
                err.setdefault(
                    "context",
                    "ssot_trust_gate_lookup_failed (cannot create safely)")
                result.success = False
                result.error = err
                result.items.append(ProductResolutionItem(sku=sku, error=err))
                return result

            if len(qoyod_matches) >= 2:
                # Duplicate SKU in Qoyod — never auto-adopt either way.
                err = {
                    "code":            "duplicate_qoyod_sku",
                    "failed_at_stage": "PRODUCT_MATCH",
                    "message": (
                        f"تم العثور على {len(qoyod_matches)} منتجات في "
                        f"قيود بنفس الـ SKU={sku!r} — لا يمكن الربط "
                        f"تلقائياً. يرجى توحيد المنتجات في قيود."),
                    "matches": [
                        {"qoyod_product_id": str(m.get("id") or ""),
                         "name":             m.get("name"),
                         "sku":              m.get("sku"),
                         "selling_price":    m.get("selling_price"),
                         "type":             m.get("type")}
                        for m in qoyod_matches[:10]
                    ],
                }
                result.success = False
                result.error = err
                result.items.append(ProductResolutionItem(sku=sku, error=err))
                return result

            if len(qoyod_matches) == 1:
                qoyod_match = qoyod_matches[0]
                auto_adopt = settings.get(
                    "auto_adopt_existing_qoyod_products", True)
                if auto_adopt:
                    # Iter-288 — silently bind to the existing Qoyod row.
                    qpid = str(qoyod_match.get("id") or "")
                    if not qpid:
                        err = {
                            "code":            "qoyod_match_missing_id",
                            "failed_at_stage": "PRODUCT_MATCH",
                            "message": (
                                f"Qoyod returned a match for sku={sku!r} "
                                f"but without an id — cannot auto-adopt"),
                            "qoyod_match_excerpt": str(qoyod_match)[:300],
                        }
                        result.success = False
                        result.error = err
                        result.items.append(
                            ProductResolutionItem(sku=sku, error=err))
                        return result
                    await db.qoyod_products_mapping.update_one(
                        {"user_id": user_id, "sku": sku},
                        {"$set": {
                            "schema_version":     1,
                            "user_id":            user_id,
                            "sku":                sku,
                            "qoyod_product_id":   qpid,
                            "qoyod_product_name": qoyod_match.get("name"),
                            "auto_created":       False,
                            "adopted":            True,
                            "adopted_at":         _now(),
                            "adopted_by":         "system",
                            "source":             "auto_adopted_from_qoyod",
                            "resolved_via":       "auto_adopt_sku_match",
                        },
                         "$setOnInsert": {"created_at": _now()}},
                        upsert=True,
                    )
                    result.items.append(ProductResolutionItem(
                        sku=sku, qoyod_product_id=qpid, created_new=False,
                        trust_source="auto_adopted"))
                    continue
                else:
                    # Strict mode — refuse, operator must adopt manually.
                    err = _untrusted_error(sku, qoyod_match)
                    result.success = False
                    result.error = err
                    result.items.append(
                        ProductResolutionItem(sku=sku, error=err))
                    return result
            # Otherwise (0 matches) → fall through to the create path.

        # Need to create in Qoyod. Iter-287 — preflight defaults first.
        # Iter-290g — shape validation runs FIRST so an operator who
        # configured a multi-element array sees the structured error
        # rather than a misleading "missing" message.
        ok_shape, offenders = validate_product_id_shapes(settings)
        if not ok_shape:
            err = build_invalid_id_shape_error(offenders)
            result.success = False
            result.error = err
            result.items.append(ProductResolutionItem(sku=sku, error=err))
            return result
        ok_defaults, missing_keys = validate_product_defaults(settings)
        if not ok_defaults:
            err = build_missing_product_defaults_error(missing_keys)
            result.success = False
            result.error = err
            result.items.append(ProductResolutionItem(sku=sku, error=err))
            return result
        idem = f"mzn-{trace_id}-product-{sku}"
        try:
            resp = await api_client.create_product(
                _build_product_payload(it, settings), idem=idem)
        except QoyodWriteLockedError as exc:
            # Iter-294 — Global Write Lock refused the product create.
            err = {
                "code":       "qoyod_write_locked",
                "message":    ("إنتاج قيود مقفول — لم يُنشَأ منتج جديد. "
                               "تم حفظ payload المنتج للمراجعة."),
                "attempt_id": exc.attempt_id,
                "action":     exc.action,
                "sku":        sku,
            }
            result.success = False
            result.error = err
            result.items.append(ProductResolutionItem(sku=sku, error=err))
            return result
        except QoyodAPIError as exc:
            # Iter-286 — self-healing 422 retry. Some Qoyod tenants
            # reject the canonical payload with:
            #   {"base": ["enter at least a purchase price or a sales price..."]}
            # even when `sale_item: 1` + `selling_price` are present
            # (older tenant template missing `type`/`is_non_stock`).
            # Iter-290g — also retry on `{'tax_id': ['Please select taxes']}`
            # as a last-resort defense if a tenant ever flips its
            # validator shape expectation. The fallback payload still
            # ships `tax_id` as a scalar int (correct per production)
            # but with the minimal-field shape that some tenants prefer.
            # Retry ONCE with the minimal-fields payload before
            # surfacing the failure.
            err_payload = exc.to_log_dict() if hasattr(exc, "to_log_dict") else {}
            msg = (str(err_payload.get("qoyod_response_excerpt") or "")
                   + " " + str(err_payload.get("message") or "")).lower()
            should_retry = (
                err_payload.get("status_code") == 422
                and (
                    "purchase price" in msg
                    or "sales price"  in msg
                    or "selling price" in msg
                    or "please select taxes" in msg     # Iter-290g
                    or "tax_id"        in msg            # Iter-290g
                )
            )
            if should_retry:
                try:
                    resp = await api_client.create_product(
                        _build_product_payload_fallback(it, settings),
                        idem=f"{idem}-fb")
                except QoyodAPIError as exc2:
                    err = exc2.to_log_dict() if hasattr(exc2, "to_log_dict") else {}
                    err["fallback_attempted"] = True
                    # Iter-290g — surface which SKU the diagnostic
                    # actually came from. Operators were confused when
                    # the failure of item #2 was logged with item #1's
                    # SKU/price (because the inbox row only persisted
                    # the first item's preview). Now every failure
                    # carries the EXACT sku + price it tried.
                    err["sku"] = sku
                    err["attempted_selling_price"] = float(
                        item_unit_price(it))
                    result.success = False
                    result.error = err
                    result.items.append(ProductResolutionItem(sku=sku, error=err))
                    return result
            else:
                err = err_payload or {"code": "qoyod_api_error", "message": str(exc)}
                err["sku"] = sku        # Iter-290g — SKU attribution
                err["attempted_selling_price"] = float(item_unit_price(it))
                result.success = False
                result.error = err
                result.items.append(ProductResolutionItem(sku=sku, error=err))
                return result

        pid = _extract_product_id(resp)
        if not pid:
            err = {"code": "qoyod_response_missing_id",
                   "message": f"create_product for sku={sku} returned no id",
                   "qoyod_response_excerpt": str(resp)[:200]}
            result.success = False
            result.error = err
            result.items.append(ProductResolutionItem(sku=sku, error=err))
            return result

        await db.qoyod_products_mapping.update_one(
            {"user_id": user_id, "sku": sku},
            {"$set": {
                "schema_version":     1,
                "user_id":            user_id,
                "sku":                sku,
                "qoyod_product_id":   pid,
                "qoyod_product_name": it.get("name"),
                "product_type":       settings.get("default_product_type") or "service",
                "is_non_stock":       (settings.get("default_product_type") or "service") == "service",
                "auto_created":       True,
                "adopted":            False,
                "resolved_via":       "global_setting",
                "source":             "mezan_created",
            },
             "$setOnInsert": {"created_at": _now()}},
            upsert=True,
        )
        result.items.append(ProductResolutionItem(
            sku=sku, qoyod_product_id=pid, created_new=True,
            trust_source="created"))
    return result


# ─────────────────────────────────────────────────────────────────────
# Manual adoption — operator explicitly onboards a historical product
# ─────────────────────────────────────────────────────────────────────
async def adopt_qoyod_product(
    db, *, user_id: str, sku: str, qoyod_product_id: str,
    qoyod_product_name: Optional[str] = None,
    note: Optional[str] = None,
    actor: str = "operator",
) -> dict:
    """Insert a row in `qoyod_products_mapping` flagged `adopted=True`.

    After adoption the resolver flows normally for this SKU. The full
    audit trail (`adopted_by`, `adopted_at`, `adoption_note`) is
    persisted so the operator can answer 'why is this SKU bound to a
    legacy Qoyod product?' months later.

    Idempotent: re-adopting the same SKU updates the note / actor
    without inserting a duplicate.
    """
    if not sku or not qoyod_product_id:
        return {"ok": False, "reason": "sku_and_qoyod_product_id_required"}
    sku = sku.strip()
    qoyod_product_id = str(qoyod_product_id).strip()

    now = _now()
    await db.qoyod_products_mapping.update_one(
        {"user_id": user_id, "sku": sku},
        {"$set": {
            "schema_version":     1,
            "user_id":            user_id,
            "sku":                sku,
            "qoyod_product_id":   qoyod_product_id,
            "qoyod_product_name": qoyod_product_name,
            "adopted":            True,
            "adopted_by":         actor,
            "adopted_at":         now,
            "adoption_note":      note,
            "source":             "operator_adopted",
            "auto_created":       False,
        },
         "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {
        "ok":                 True,
        "sku":                sku,
        "qoyod_product_id":   qoyod_product_id,
        "qoyod_product_name": qoyod_product_name,
        "adopted_by":         actor,
        "adopted_at":         now.isoformat(),
    }
