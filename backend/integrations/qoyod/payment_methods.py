"""Canonical payment-method resolution + alias mapping.

Spec (2026-02-26, user)
───────────────────────
The Qoyod pipeline must NOT hardcode which payment methods Salla
returns. It must instead:

    1. Accept any payment_method string Salla sends (e.g.
       "tamara_installment", "bank_transfer", "stc_pay", or even a
       brand-new method we've never seen).
    2. Treat unmapped methods as a "needs configuration" row on the
       Settings page — NOT as a permanent runtime blocker.
    3. Provide an alias table so multiple Salla variants of the SAME
       provider (e.g. "tamara" + "tamara_installment") share ONE Qoyod
       account mapping by default.
    4. Still let the operator override the alias by mapping a variant
       explicitly to a DIFFERENT Qoyod account when needed.

Resolution order
────────────────
For a given normalised payment method key:

    1. Direct match in `settings.payment_method_mapping` →
       use that Qoyod account.
    2. Alias lookup: collapse to base provider (e.g.
       "tamara_installment" → "tamara") and retry direct match.
    3. Otherwise → unmapped (caller decides what to do; preflight
       reports it as `payment_method_mapping_missing`).

Aliases are tested only when no direct mapping exists. This lets the
operator add `tamara_installment → Qoyod account X` explicitly without
it being silently overridden by the alias to `tamara`.
"""
from __future__ import annotations

from typing import Optional


# ─────────────────────────────────────────────────────────────────────
# Alias table — variant → base provider
# ─────────────────────────────────────────────────────────────────────
# Keys MUST be already lowercased + underscore-normalised (what we get
# out of `_canonical_payment_method` for unknown values).
PAYMENT_METHOD_ALIASES: dict[str, str] = {
    # Tamara
    "tamara_installment":  "tamara",
    "tamara_installments": "tamara",
    "tamara_pay":          "tamara",
    "tamara_payment":      "tamara",
    # Tabby
    "tabby_installment":   "tabby",
    "tabby_installments":  "tabby",
    "tabby_pay":           "tabby",
    "tabby_payment":       "tabby",
    # Emkan
    "emkan_installment":   "emkan",
    "emkan_installments":  "emkan",
    # Bank
    "bank":                "bank_transfer",
    "wire_transfer":       "bank_transfer",
    # Cards
    "credit":              "credit_card",
    "card":                "credit_card",
    "credit_card_payment": "credit_card",
    # COD
    "cash_on_delivery":    "cod",
    "cash":                "cod",
    # STC Pay
    "stcpay":              "stc_pay",
    # Apple Pay
    "applepay":            "apple_pay",
}


def _norm(v: Optional[str]) -> str:
    """Lowercase + strip + collapse whitespace to underscore. Mirrors
    the tail of `_canonical_payment_method` so keys compare cleanly."""
    if not v:
        return ""
    return str(v).strip().lower().replace(" ", "_")


def provider_family(payment_method: Optional[str]) -> Optional[str]:
    """Return the base provider for a payment method, or the input
    itself if no alias applies. Used by the UI to group rows and by
    the resolver as the fallback key.

    Examples
    ────────
    >>> provider_family("tamara_installment")
    'tamara'
    >>> provider_family("TAMARA_INSTALLMENT")
    'tamara'
    >>> provider_family("mada")
    'mada'
    >>> provider_family("brand_new_method")
    'brand_new_method'
    """
    key = _norm(payment_method)
    if not key:
        return None
    return PAYMENT_METHOD_ALIASES.get(key, key)


def resolve_payment_account(
    settings: dict, payment_method: Optional[str],
) -> Optional[str]:
    """Resolve a Qoyod account_id for a given payment method.

    Lookup order:
        1. Direct match in `settings.payment_method_mapping`.
        2. Alias → base provider → direct match.
        3. None.

    Returns the `qoyod_account_id` string, or None when the method is
    unmapped (caller is responsible for surfacing a UI hint to the
    operator — never silently drop the payment).
    """
    key = _norm(payment_method)
    if not key:
        return None

    mapping = settings.get("payment_method_mapping") or []
    # Build a single-pass index: normalised salla_method → account_id.
    by_key: dict[str, str] = {}
    for m in mapping:
        sm = _norm(m.get("salla_method"))
        aid = (m.get("qoyod_account_id") or "").strip()
        if sm and aid:
            by_key[sm] = aid

    # 1) Direct match
    if key in by_key:
        return by_key[key]

    # 2) Alias → base provider
    base = PAYMENT_METHOD_ALIASES.get(key)
    if base and base in by_key:
        return by_key[base]

    return None


def explain_resolution(
    settings: dict, payment_method: Optional[str],
) -> dict:
    """Diagnostic — what the resolver did, for the UI/Monitor.
    Returns: {
        input,            # the raw input (lowercased/normalised)
        family,           # the base provider (alias collapsed)
        matched_via,      # "direct" | "alias" | None
        matched_key,      # the salla_method key in mapping that matched
        qoyod_account_id, # the account_id if matched else None
    }
    """
    key = _norm(payment_method)
    family = provider_family(payment_method)
    mapping = settings.get("payment_method_mapping") or []
    by_key: dict[str, str] = {}
    for m in mapping:
        sm = _norm(m.get("salla_method"))
        aid = (m.get("qoyod_account_id") or "").strip()
        if sm and aid:
            by_key[sm] = aid
    if key in by_key:
        return {"input": key, "family": family, "matched_via": "direct",
                "matched_key": key, "qoyod_account_id": by_key[key]}
    base = PAYMENT_METHOD_ALIASES.get(key)
    if base and base in by_key:
        return {"input": key, "family": family, "matched_via": "alias",
                "matched_key": base, "qoyod_account_id": by_key[base]}
    return {"input": key, "family": family, "matched_via": None,
            "matched_key": None, "qoyod_account_id": None}
