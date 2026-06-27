"""Legacy-payload Adapter — Make.com → canonical Salla shape.

Purpose (user spec 2026-06-26)
─────────────────────────────
Make.com currently fires an HTTP module that sends a flat JSON body
to `/api/webhook/make/{token}` for Dashboard reporting. A second HTTP
module is about to be added that posts to `/api/integrations/qoyod/webhook`
with the SAME flat shape (so the Make scenario doesn't have to be
rebuilt). This module bridges that legacy contract to the canonical
Salla shape expected by `normalizer.validate()`.

Detection rule
──────────────
A payload is treated as "legacy" when ALL of these are true:
  • Top-level is a dict.
  • There is NO `data` envelope (`raw["data"]` is missing/empty).
  • At least one legacy marker key is present at the root:
        `customer_name`, `customer_mobile`,
        `total_amount`, `order_status_slug`,
        `subtotal`, `shipping_cost`, `received_from`.

Anything else passes through unchanged (so the canonical Salla shape
keeps working when Make eventually graduates to it).

Items resolution
────────────────
After detection, the adapter looks for line items in this priority
order — first hit wins:
  1. `items[]`    — flat array, each item `{sku, name, quantity,
                    price: {amount, currency}, options?}`
  2. `packages[].items[]` — nested per shipping package (Salla raw shape)
  3. nothing      — items_source = "missing"

The two shapes are normalised to a canonical list-of-dicts whose
sub-shape matches what `_normalize_item()` in `normalizer.py` expects:
  `{sku, name, quantity, amounts: {price_without_tax: {amount, currency},
                                    total: {amount, currency}}}`

Metadata
────────
Returns `(adapted_payload, meta_dict)` where `meta_dict` is:
  {
    "adapter_applied":          true/false,
    "items_source":             "items" | "packages" | "missing",
    "legacy_status_slug":       <str | None>,
    "legacy_extras":            { ...unknown_root_fields... },
  }

The pipeline writer is responsible for persisting `meta` onto the
`integration_inbox` row alongside the canonical payload.
"""
from __future__ import annotations

from typing import Any, Optional


# ─── Detection ──────────────────────────────────────────────────────
_LEGACY_MARKERS: tuple[str, ...] = (
    "customer_name", "customer_mobile", "total_amount",
    "order_status_slug", "subtotal", "shipping_cost",
    "received_from", "order_number",
)
# Keys we transform — anything else at root is preserved under
# `meta.legacy_extras` so nothing is silently dropped.
_KNOWN_LEGACY_KEYS: frozenset[str] = frozenset({
    "event_type", "order_number", "order_id", "created_at", "order_date",
    "total_amount", "total", "subtotal", "tax", "discount",
    "shipping_cost", "currency", "payment_method",
    "customer_name", "customer_mobile", "customer_email",
    "order_status", "order_status_slug", "payment_status", "status",
    "completed_at",
    "items", "packages", "products",
    "shipping_address", "billing_address",
    # Note: utm_*, device, received_from, shipping_company, source — all
    # intentionally fall into `meta.legacy_extras` for audit. They have
    # NO downstream representation in the Qoyod pipeline.
})


def is_legacy_shape(raw: Any) -> bool:
    """True when the payload smells like Make.com's flat contract."""
    if not isinstance(raw, dict):
        return False
    if isinstance(raw.get("data"), dict) and raw.get("data"):
        return False     # already canonical
    return any(k in raw for k in _LEGACY_MARKERS)


# ─── Money helper ───────────────────────────────────────────────────
def _money(amount: Any, currency: str = "SAR") -> Optional[dict]:
    """Wrap a flat number into Salla's `{amount, currency}` shape."""
    if amount is None or amount == "":
        return None
    try:
        n = float(amount)
    except (TypeError, ValueError):
        return None
    return {"amount": n, "currency": currency or "SAR"}


def _extract_money_value(node: Any) -> Optional[float]:
    """Extract a numeric amount from a Salla money node, handling
    BOTH flat `{amount, currency}` and double-nested
    `{amount: {amount, currency}}` shapes (Iter-275 normalizer parity).
    Returns None when the node carries no resolvable amount.
    """
    if node is None or node == "":
        return None
    if isinstance(node, dict):
        inner = node.get("amount")
        if isinstance(inner, dict):
            return _extract_money_value(inner)
        try:
            return float(inner) if inner not in (None, "") else None
        except (TypeError, ValueError):
            return None
    try:
        return float(node)
    except (TypeError, ValueError):
        return None


def _split_name(full: Any) -> tuple[str, str]:
    """Split 'first last' → ('first', 'last'). Handles None/empty/single-word."""
    if not full:
        return ("", "")
    s = str(full).strip()
    if not s:
        return ("", "")
    parts = s.split(maxsplit=1)
    return (parts[0], parts[1] if len(parts) > 1 else "")


# ─── Item normalisation ─────────────────────────────────────────────
def _adapt_item(raw_item: dict, default_currency: str) -> Optional[dict]:
    """Coerce a Make/Salla line item into the canonical Salla shape.

    Returns None if the item lacks BOTH name AND sku (can't represent
    a sellable line — caller decides whether to mark items_source
    as 'missing' if every line collapses to None).
    """
    if not isinstance(raw_item, dict):
        return None

    name = (raw_item.get("name") or "").strip()
    sku  = (raw_item.get("sku") or "").strip()
    if not sku and isinstance(raw_item.get("product"), dict):
        sku = str(raw_item["product"].get("sku") or "").strip()
    if not name and isinstance(raw_item.get("product"), dict):
        name = str(raw_item["product"].get("name") or "").strip()
    if not name and not sku:
        return None

    qty = raw_item.get("quantity")
    try:
        qty = float(qty) if qty not in (None, "") else 1.0
    except (TypeError, ValueError):
        qty = 1.0

    # ── Price / tax / discount extraction (Iter-278) ────────────────
    # Bug fixed: previous version only inspected `raw_item.price` and
    # `raw_item.amounts.tax.amount` (one level). Salla's modern
    # webhook ships items as:
    #   amounts: {
    #     price_without_tax: { amount, currency },
    #     total_discount:    { amount, currency },          ← was dropped
    #     tax:   { percent, amount: { amount, currency } }, ← double-nested
    #     total: { amount, currency }
    #   }
    # — and crucially does NOT include a top-level `price` field.
    # Without this fix, the adapter produced `price_without_tax: null`
    # which the normalizer rendered as `unit_price = 0`. See orders
    # 268632361 / 268633052 (Iter-278 forensic).
    amounts_node = raw_item.get("amounts") if isinstance(
        raw_item.get("amounts"), dict) else {}

    # Currency: prefer the explicit price_without_tax.currency, fall back
    # to a flat price.currency, finally to order-level default.
    pwt_node = amounts_node.get("price_without_tax")
    flat_price_node = raw_item.get("price")
    if isinstance(pwt_node, dict) and pwt_node.get("currency"):
        price_currency = pwt_node.get("currency")
    elif isinstance(flat_price_node, dict) and flat_price_node.get("currency"):
        price_currency = flat_price_node.get("currency")
    else:
        price_currency = default_currency

    # Unit price — priority chain: amounts.price_without_tax →
    # flat price.amount → raw.unit_price → None.
    price_amount = _extract_money_value(pwt_node)
    if price_amount is None:
        price_amount = _extract_money_value(flat_price_node)
    if price_amount is None and raw_item.get("unit_price") not in (None, ""):
        try:
            price_amount = float(raw_item.get("unit_price"))
        except (TypeError, ValueError):
            price_amount = None

    # Total per line — priority: amounts.total → raw.total → raw.total_price.
    total_amount = _extract_money_value(amounts_node.get("total")) \
        if amounts_node else None
    if total_amount is None:
        total_amount = _extract_money_value(raw_item.get("total"))
    if total_amount is None:
        total_amount = _extract_money_value(raw_item.get("total_price"))
    if total_amount is None and price_amount is not None:
        total_amount = price_amount * qty

    # Tax — handles BOTH flat (`amounts.tax = {amount, currency}`)
    # AND Salla's modern double-nested
    # (`amounts.tax = {percent, amount: {amount, currency}}`).
    tax_amount = _extract_money_value(amounts_node.get("tax")) \
        if amounts_node else None
    if tax_amount is None:
        tax_amount = _extract_money_value(raw_item.get("tax"))

    # Per-line discount — Iter-276 column. Previously dropped entirely.
    discount_amount = _extract_money_value(amounts_node.get("total_discount")) \
        if amounts_node else None
    if discount_amount is None:
        discount_amount = _extract_money_value(raw_item.get("discount_amount"))
    if discount_amount is None:
        discount_amount = _extract_money_value(raw_item.get("discount"))

    item: dict = {
        "name": name or sku,
        "sku":  sku,
        "quantity": qty,
        "amounts": {
            "price_without_tax": _money(price_amount, price_currency),
            "total":             _money(total_amount, price_currency),
        },
    }
    if tax_amount is not None:
        item["amounts"]["tax"] = _money(tax_amount, price_currency)
    if discount_amount is not None:
        item["amounts"]["total_discount"] = _money(
            discount_amount, price_currency)

    # Carry product_id when Salla shipped one.
    pid = raw_item.get("product_id") or (
        raw_item["product"].get("id")
        if isinstance(raw_item.get("product"), dict)
        else None)
    if pid is not None:
        item["product_id"] = str(pid)

    # Carry through options (variants) untouched — useful for audit.
    if raw_item.get("options"):
        item["options"] = raw_item["options"]

    return item


def _collect_items(
    raw: dict, default_currency: str,
) -> tuple[list[dict], str]:
    """Return (canonical_items, items_source).

    items_source ∈ {"items", "packages", "missing"}.
    Empty list pairs with "missing".
    """
    # Priority 1 — flat items[] at root (preferred Make output)
    items_field = raw.get("items")
    if isinstance(items_field, list) and items_field:
        out: list[dict] = []
        for it in items_field:
            adapted = _adapt_item(it, default_currency)
            if adapted is not None:
                out.append(adapted)
        if out:
            return out, "items"

    # Priority 1b — legacy `products[]` alias (older Salla webhooks)
    products_field = raw.get("products")
    if isinstance(products_field, list) and products_field:
        out = []
        for it in products_field:
            adapted = _adapt_item(it, default_currency)
            if adapted is not None:
                out.append(adapted)
        if out:
            return out, "items"

    # Priority 2 — packages[].items[] (Salla raw nested shape)
    packages = raw.get("packages")
    if isinstance(packages, list) and packages:
        out = []
        for pkg in packages:
            if not isinstance(pkg, dict):
                continue
            pkg_items = pkg.get("items") or pkg.get("products") or []
            if not isinstance(pkg_items, list):
                continue
            for it in pkg_items:
                adapted = _adapt_item(it, default_currency)
                if adapted is not None:
                    out.append(adapted)
        if out:
            return out, "packages"

    return [], "missing"


# ─── Status extraction ──────────────────────────────────────────────
def _build_status(raw: dict) -> Optional[dict]:
    """Translate Legacy status fields into Salla's status node.

    Output shape mirrors what `_extract_status_native()` understands:
      {"name": "<arabic name>", "slug": "<english slug>"}
    Returns None when neither status nor slug are present.
    """
    slug = (raw.get("order_status_slug") or raw.get("status_slug")
            or raw.get("status") or "").strip()
    name = (raw.get("order_status") or raw.get("status_name") or "").strip()
    if not slug and not name:
        return None
    out: dict = {}
    if name:
        out["name"] = name
    if slug:
        out["slug"] = slug
        out["customized"] = {"name": name or slug}
    return out


# ─── Public entrypoint ─────────────────────────────────────────────
def adapt(raw: Any) -> tuple[Any, dict]:
    """Detect legacy shape and return `(adapted_payload, meta)`.

    When the payload is NOT legacy, returns the payload unchanged with
    `meta.adapter_applied = False`. Items are not analysed in that
    branch — `validate()` does its own structural check downstream.
    """
    if not is_legacy_shape(raw):
        return raw, {
            "adapter_applied": False,
            "items_source":    "passthrough",
            "legacy_status_slug": None,
            "legacy_extras":   {},
        }

    src = raw if isinstance(raw, dict) else {}
    currency = (src.get("currency") or "SAR").strip() or "SAR"

    items, items_source = _collect_items(src, currency)

    # Build canonical data envelope ------------------------------------
    order_id      = src.get("order_id") or src.get("id") or ""
    order_number  = src.get("order_number") or order_id or ""
    first, last   = _split_name(src.get("customer_name"))
    status_node   = _build_status(src)

    # Amounts — every field is OPTIONAL because legacy MVPs may omit
    # them. `normalizer._money()` already returns None safely.
    amounts: dict = {}
    if src.get("subtotal")      not in (None, ""):
        amounts["sub_total"] = _money(src["subtotal"], currency)
    if src.get("tax")           not in (None, ""):
        amounts["tax"] = _money(src["tax"], currency)
    if src.get("shipping_cost") not in (None, ""):
        amounts["shipping"] = _money(src["shipping_cost"], currency)
    if src.get("discount")      not in (None, ""):
        amounts["discount"] = _money(src["discount"], currency)
    total = src.get("total_amount") if src.get("total_amount") not in (None, "") \
            else src.get("total")
    if total not in (None, ""):
        amounts["total"] = _money(total, currency)

    customer: dict = {}
    if first or last:
        customer["first_name"] = first
        customer["last_name"]  = last
    # Always also expose the original concatenated name as `full_name`
    # so the normalizer's fallback chain (full_name → name → phone →
    # guest) still has it even if `_split_name` ever returns empty
    # parts on weird inputs (RTL marks, single-character names, …).
    cn = (src.get("customer_name") or "").strip()
    if cn:
        customer["full_name"] = cn
    if src.get("customer_mobile"):
        customer["mobile"] = src["customer_mobile"]
    if src.get("customer_email"):
        customer["email"] = src["customer_email"]

    data: dict = {
        "id":            str(order_id) if order_id else None,
        "reference_id":  str(order_number) if order_number else None,
        "date":          src.get("created_at") or src.get("order_date"),
        "created_at":    src.get("created_at") or src.get("order_date"),
        "completed_at":  src.get("completed_at"),
        "currency":      currency,
        "items":         items,            # may be []
        "amounts":       amounts,
        "customer":      customer or None,
        "payment_method": src.get("payment_method") or None,
        "shipping_address": src.get("shipping_address"),
        "billing_address":  src.get("billing_address"),
    }
    # Drop None top-level keys so the canonical shape stays compact
    data = {k: v for k, v in data.items() if v is not None}

    adapted: dict = {
        "event": src.get("event_type") or "order_created",
        "data":  data,
    }

    # Anything else at the root (utm_source, utm_campaign, device,
    # received_from, shipping_company, …) is preserved for audit.
    legacy_extras = {
        k: v for k, v in src.items()
        if k not in _KNOWN_LEGACY_KEYS
    }

    meta = {
        "adapter_applied":   True,
        "items_source":      items_source,
        "legacy_status_slug": (status_node or {}).get("slug"),
        "legacy_extras":     legacy_extras,
    }
    # We attach status separately so we don't pollute the data envelope
    # when both keys are missing in source.
    if status_node:
        adapted["data"]["status"] = status_node

    return adapted, meta
