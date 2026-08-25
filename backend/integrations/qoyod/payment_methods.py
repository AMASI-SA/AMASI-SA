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


# Merchant-approved Qoyod receiving-bank accounts (2026-07-19).
# These are deliberately bank-specific: a transfer with no recognised
# receiving bank must be refused instead of falling back to the generic
# bank_transfer account and posting cash to the wrong bank.
APPROVED_RECEIVING_BANK_ACCOUNTS: dict[str, str] = {
    "bank_rajhi": "94",
    "bank_ahli": "95",
    "bank_inma": "8",
}


def extract_receiving_bank_name(*sources) -> Optional[str]:
    """Extract the receiving bank using the same multi-field strategy as
    Order Details.

    Salla has exposed this value at root level, inside ``bank``, and inside
    ``payment`` across different payloads.  Only a recognised merchant bank
    is returned; the generic payment value ``bank`` is never mistaken for a
    bank name.
    """
    candidates = []
    direct_keys = (
        "receiving_bank_name", "destination_bank_name",
        "transfer_bank_name", "bank_name", "receiving_bank",
        "target_bank_name", "target_bank",
    )

    def collect(value, depth=0):
        if not isinstance(value, dict) or depth > 4:
            return
        for key in direct_keys:
            candidate = value.get(key)
            if isinstance(candidate, dict):
                candidate = candidate.get("bank_name") or candidate.get(
                    "name") or candidate.get("label")
            if candidate not in (None, ""):
                candidates.append(str(candidate).strip())
        plain_bank = value.get("bank")
        if isinstance(plain_bank, str) and plain_bank.strip():
            candidates.append(plain_bank.strip())
        for nested_key in ("payment", "bank", "data", "order"):
            nested = value.get(nested_key)
            if isinstance(nested, dict):
                # A Salla `bank` object commonly uses plain `name`.
                if nested_key == "bank":
                    candidate = nested.get("bank_name") or nested.get("name")
                    if candidate not in (None, ""):
                        candidates.append(str(candidate).strip())
                collect(nested, depth + 1)

    for source in sources:
        collect(source)
    for candidate in candidates:
        if receiving_bank_key(None, candidate):
            return candidate
    return None


def receiving_bank_key(
    payment_method: Optional[str], receiving_bank_name: Optional[str],
) -> Optional[str]:
    """Resolve the specific receiving bank for a Salla transfer.

    Prefer a bank-specific canonical payment method when present, then
    inspect Salla's separate ``receiving_bank_name`` field.  Returns None
    for generic/unknown bank transfers so callers can refuse safely.
    """
    method_key = _norm(payment_method)
    if method_key in APPROVED_RECEIVING_BANK_ACCOUNTS:
        return method_key

    raw = _norm(receiving_bank_name)
    if not raw:
        return None
    compact = raw.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    aliases = {
        "bank_rajhi": ("الراجحي", "مصرف_الراجحي", "بنك_الراجحي",
                       "rajhi", "al_rajhi"),
        "bank_ahli": ("الاهلي", "البنك_الاهلي", "بنك_الاهلي",
                      "ahli", "ncb", "saudi_national_bank"),
        "bank_inma": ("الانماء", "بنك_الانماء", "مصرف_الانماء",
                      "alinma", "al_inma", "inma"),
    }
    for key, names in aliases.items():
        if any(name in compact for name in names):
            return key
    return None


def resolve_receiving_bank_account(
    settings: dict, payment_method: Optional[str],
    receiving_bank_name: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Return ``(bank_key, Qoyod account_id)`` for bank transfers.

    An explicit bank-specific row in ``payment_method_mapping`` wins.
    Otherwise the merchant-approved account table above is used.  The
    generic ``bank_transfer`` mapping is intentionally never consulted.
    """
    key = receiving_bank_key(payment_method, receiving_bank_name)
    if not key:
        return None, None

    for row in settings.get("payment_method_mapping") or []:
        if _norm(row.get("salla_method")) != key:
            continue
        account_id = str(row.get("qoyod_account_id") or "").strip()
        if account_id:
            return key, account_id
    return key, APPROVED_RECEIVING_BANK_ACCOUNTS[key]


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
    "bank_rajhi":          "bank_transfer",
    "bank_inma":           "bank_transfer",
    "bank_ahli":           "bank_transfer",
    # Cards
    "credit":              "credit_card",
    "card":                "credit_card",
    "credit_card_payment": "credit_card",
    # COD
    "cash_on_delivery":    "cod",
    "cash":                "cod",
    # Arabic variants of COD that may have made it into the canonical
    # field on legacy rows (before the normalizer table was extended).
    "الدفع_عند_الاستلام":  "cod",
    "النوع_عند_الاستلام":  "cod",
    "الدفع_نقدا_عند_الاستلام": "cod",
    "نقد_عند_الاستلام":    "cod",
    "نقدًا_عند_الاستلام":   "cod",
    # STC Pay
    "stcpay":              "stc_pay",
    # Apple Pay
    "applepay":            "apple_pay",
}


# ─────────────────────────────────────────────────────────────────────
# Iter-292 — Transitional / pending payment statuses
# ─────────────────────────────────────────────────────────────────────
# These are NOT real payment methods — they are order-lifecycle states
# (customer hasn't paid yet OR we're waiting for bank settlement).
# They must NEVER appear on the Payment Method Mapping screen and they
# must NEVER trigger a Qoyod receipt POST. They flow through as
# `skipped_pending_payment` in the Webhook Monitor.
PENDING_PAYMENT_STATUSES: set[str] = {
    "waiting", "pending", "pending_payment", "awaiting_payment",
    "unpaid", "not_paid",
    "بانتظار_الدفع", "انتظار_الدفع", "في_انتظار_الدفع",
    "waiting_payment",
}


def is_pending_payment_status(payment_method: Optional[str]) -> bool:
    """Return True when the value represents a non-payment lifecycle
    state rather than an actual payment method.

    Used by:
      • Normalizer: blanks out `payment_method` to None on pending.
      • Preflight: skips the missing-mapping check when pending.
      • Receipt step: aborts cleanly with skipped_reason=pending.
      • Settings UI: filters these values out of the mapping table.
    """
    return _norm(payment_method) in PENDING_PAYMENT_STATUSES


# ─────────────────────────────────────────────────────────────────────
# Iter-293 — Posting modes (paid_receipt | credit_invoice_only | disabled)
# ─────────────────────────────────────────────────────────────────────
# Accounting policy for how a payment method is posted to Qoyod:
#
#   paid_receipt        — Instant payments (Mada / Apple Pay / Visa /
#                         STC Pay / bank_transfer with confirmed receipt).
#                         Pipeline creates BOTH an invoice AND an
#                         invoice_payment (closes the balance to zero).
#                         REQUIRES qoyod_account_id.
#
#   credit_invoice_only — Cash on Delivery (COD) ONLY for now.
#                         Pipeline creates ONLY an invoice. NO
#                         invoice_payment, NO qoyod_account_id needed.
#                         The full amount stays as receivable in Qoyod
#                         and is collected later when the courier
#                         delivers + remits cash.
#
#   disabled            — Payment method exists in Salla data but is
#                         intentionally NOT synced to Qoyod (e.g. test
#                         methods, deprecated methods). Pipeline skips
#                         the order entirely with SKIPPED_DISABLED.

POSTING_MODE_PAID_RECEIPT        = "paid_receipt"
POSTING_MODE_CREDIT_INVOICE_ONLY = "credit_invoice_only"
POSTING_MODE_DISABLED            = "disabled"

VALID_POSTING_MODES: set[str] = {
    POSTING_MODE_PAID_RECEIPT,
    POSTING_MODE_CREDIT_INVOICE_ONLY,
    POSTING_MODE_DISABLED,
}


def is_cod_family(payment_method: Optional[str]) -> bool:
    """Return True iff this payment method collapses to the COD family
    (cash on delivery, in any language / spelling variant). This is the
    SOURCE OF TRUTH for the rule: "COD = credit_invoice_only, ALWAYS".

    Resolution: direct match against "cod" OR alias collapses to "cod".
    """
    key = _norm(payment_method)
    if not key:
        return False
    return key == "cod" or PAYMENT_METHOD_ALIASES.get(key) == "cod"


def resolve_posting_mode(
    settings: dict, payment_method: Optional[str],
) -> str:
    """Return the posting_mode for an incoming payment method.

    Strict rule (Iter-293, user-mandated): if `is_cod_family` returns
    True, posting_mode is ALWAYS `credit_invoice_only`. The user's
    explicit Settings selection is IGNORED for COD — this is enforced
    at both the API write boundary (routes.py validates + coerces)
    AND at the pipeline read boundary (this function), so even a
    bypass of the API won't book a COD order as paid.

    For non-COD methods:
        1. Direct row in payment_method_mapping → use its posting_mode.
        2. Alias-family row in payment_method_mapping → use that row's
           posting_mode.
        3. Otherwise default to `paid_receipt` (backwards-compatible
           with rows that pre-date the posting_mode field).
    """
    # 0) Iron-clad COD override.
    if is_cod_family(payment_method):
        return POSTING_MODE_CREDIT_INVOICE_ONLY

    key = _norm(payment_method)
    if not key:
        return POSTING_MODE_PAID_RECEIPT

    mapping = settings.get("payment_method_mapping") or []
    by_key: dict[str, str] = {}
    for m in mapping:
        sm = _norm(m.get("salla_method"))
        pm = (m.get("posting_mode") or "").strip()
        if sm and pm in VALID_POSTING_MODES:
            by_key[sm] = pm

    if key in by_key:
        return by_key[key]
    base = PAYMENT_METHOD_ALIASES.get(key)
    if base and base in by_key:
        return by_key[base]
    return POSTING_MODE_PAID_RECEIPT


def needs_qoyod_account(posting_mode: str) -> bool:
    """Only `paid_receipt` requires a qoyod_account_id. Used by both
    Settings validation (block save if missing) and the pipeline
    pre-POST guard (block invoice_payment step if missing)."""
    return posting_mode == POSTING_MODE_PAID_RECEIPT


def coerce_cod_rows(mapping: list[dict]) -> list[dict]:
    """Iter-293 — Enforce the COD invariant at the API boundary:

        salla_method ∈ COD family  →  posting_mode = credit_invoice_only
                                       qoyod_account_id = None

    Returns a NEW list (original is not mutated). Any other rows pass
    through with whatever the operator submitted (subject to per-field
    validation elsewhere).

    Iter-293.1 — Additional invariant for `bank_transfer`:
        bank_transfer family rows MUST NOT use credit_invoice_only.
        Per user policy (2026-06-30), bank_transfer is paid_receipt
        per receiving bank (Iter-294) — NEVER an open credit invoice.
        Until Iter-294 ships, the row stays on paid_receipt (Legacy
        general-bank mapping). Any attempt to save it as
        credit_invoice_only raises `ValueError` so the API caller
        gets a 400 with an actionable message — far better than a
        silent payment-method-mapping-missing failure on a real order.
    """
    out: list[dict] = []
    for row in mapping or []:
        if not isinstance(row, dict):
            continue
        new_row = dict(row)
        salla_method = new_row.get("salla_method")
        if is_cod_family(salla_method):
            new_row["posting_mode"] = POSTING_MODE_CREDIT_INVOICE_ONLY
            new_row["qoyod_account_id"] = None
        else:
            # Default any unset/invalid posting_mode to paid_receipt.
            pm = (new_row.get("posting_mode") or "").strip()
            if pm not in VALID_POSTING_MODES:
                new_row["posting_mode"] = POSTING_MODE_PAID_RECEIPT
            # bank_transfer guard (Iter-293.1).
            if _is_bank_transfer_family(salla_method) \
               and new_row["posting_mode"] == POSTING_MODE_CREDIT_INVOICE_ONLY:
                raise ValueError(
                    "bank_transfer cannot be credit_invoice_only. "
                    "It must use receiving-bank routing (Iter-294)."
                )
        out.append(new_row)
    return out


def is_bank_transfer_family(salla_method) -> bool:
    """Return True iff this payment method collapses to the
    bank_transfer family (any alias). Used by `coerce_cod_rows` to
    block the credit_invoice_only mis-configuration."""
    key = _norm(salla_method)
    if not key:
        return False
    return key == "bank_transfer" or PAYMENT_METHOD_ALIASES.get(key) == "bank_transfer"


def _is_bank_transfer_family(salla_method) -> bool:
    """Backward-compatible private alias."""
    return is_bank_transfer_family(salla_method)


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
