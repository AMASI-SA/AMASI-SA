"""Single source of truth for payment-method names (iter-62).

The whole app — settings, accounts, settlements, reports, parser, UI — must
use the EXACT display names defined here. If you need to add a new method
or alias, do it ONCE in this file.

Naming rules
------------
- Arabic-only display names (no Latin in parentheses).
- "سلة" is the rollup account for every electronic rail Salla collects on
  the merchant's behalf (mada / Apple Pay / STC Pay / Visa / MasterCard /
  بطاقة ائتمانية / بطاقة بنكية / محفظة سلة).
- Standalone accounts: تابي, تمارا, إمكان, تحويل بنكي, الدفع عند الاستلام.
"""
from __future__ import annotations

# ── Canonical display names ───────────────────────────────────────────────
# These are the EXACT strings the UI / settings / reports must show.
SALLA              = "سلة"
TABBY              = "تابي"
TAMARA             = "تمارا"
EMKAN              = "إمكان"
BANK_TRANSFER      = "تحويل بنكي"
CASH_ON_DELIVERY   = "الدفع عند الاستلام"

# Salla sub-methods (rolled up under "سلة" — never their own asset account).
MADA               = "مدى"
APPLE_PAY          = "Apple Pay"
GOOGLE_PAY         = "Google Pay"
STC_PAY            = "STC Pay"
VISA               = "Visa"
MASTERCARD         = "MasterCard"
CREDIT_CARD        = "بطاقة ائتمانية"
DEBIT_CARD         = "بطاقة بنكية"
SALLA_WALLET       = "محفظة سلة"


# ── Default payment methods (used to seed user settings) ─────────────────
# Order matters → it's the order shown in Settings → Payment Methods.
#
# ``salla-tamara-tabby-statements-2026-08-v3`` combines three merchant
# evidence sets:
# seven Salla payment-detail invoices (528 rows) and five unique Tamara weekly
# statements (310 rows; three duplicate uploads were ignored by SHA-256), plus
# four unique Tabby weekly settlement reports (231 rows).
# The observed Salla rails matched every positive transaction exactly:
#
#   mada         = amount × 1.00% + SAR 1.00
#   credit card  = amount × 2.20% + SAR 1.00
#   STC Pay      = amount × 1.30% + SAR 1.00
#
# VAT is 15% of the *unrounded* per-order fee, rounded to halalas afterwards.
# Apple Pay / Google Pay / Visa / MasterCard did not appear as separate invoice
# labels in that evidence set.  They therefore inherit the observed generic
# credit-card rate as an explicit estimate until a later provider invoice
# proves a rail-specific rate.
#
# Tamara's observed PAY_BY_INSTALMENTS captures matched:
#
#   amount × 6.99% (rounded per order) + SAR 1.50 fixed
#
# VAT is 15% of the displayed rounded Tamara fee, rounded per event.  Refund
# rows carry no fee rebate; cancellation rows carry only SAR 1.50 + VAT.
#
# Tabby's observed sale split matched every row exactly:
#
#   refundable commission     = amount × 4.99% (rounded per order)
#   non-refundable commission = amount × 2.00% (rounded per order)
#   fixed fee                 = SAR 1.00 per captured order
#
# The displayed total MDR is therefore 6.99%, but refunds reverse only the
# 4.99% refundable slice and its VAT; the 2% slice and SAR 1 fixed fee remain.
# VAT is 15% rounded separately on each displayed fee leg.
PAYMENT_FEE_DEFAULTS_VERSION = "salla-tamara-tabby-statements-2026-08-v3"

DEFAULT_PAYMENT_METHODS: list[dict] = [
    # Salla card rails — editable so the merchant can override Salla's
    # actual commission for each rail.
    {"name": MADA,             "commission_percent": 1.00, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": APPLE_PAY,        "commission_percent": 2.20, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": GOOGLE_PAY,       "commission_percent": 2.20, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": STC_PAY,          "commission_percent": 1.30, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": VISA,             "commission_percent": 2.20, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": MASTERCARD,       "commission_percent": 2.20, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": CREDIT_CARD,      "commission_percent": 2.20, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": DEBIT_CARD,       "commission_percent": 2.20, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": SALLA_WALLET,     "commission_percent": 0.00, "fixed_fee": 0.0, "vat_percent": 0.0},
    # BNPL — own accounts
    {"name": TAMARA,           "commission_percent": 6.99, "fixed_fee": 1.5, "vat_percent": 15.0},
    {"name": TABBY,            "commission_percent": 6.99, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": EMKAN,            "commission_percent": 5.00, "fixed_fee": 0.0, "vat_percent": 15.0},
    # Cash / bank — own accounts, zero commission
    {"name": BANK_TRANSFER,    "commission_percent": 0.00, "fixed_fee": 0.0, "vat_percent": 0.0},
    {"name": CASH_ON_DELIVERY, "commission_percent": 0.00, "fixed_fee": 0.0, "vat_percent": 0.0},
]


# ── Alias table — every raw spelling we've ever seen maps here ────────────
# Each row: (canonical_key, display_name, alias substring [lowercase], parent_key)
# parent_key="salla" → the method rolls up under the سلة account.
# parent_key=None    → the method has its own standalone account.
PAYMENT_ALIASES: list[tuple[str, str, str, str | None]] = [
    # ── Salla card rails ────────────────────────────────────────────────
    ("mada",          MADA,          "mada",                  "salla"),
    ("mada",          MADA,          "مدى",                   "salla"),
    ("apple_pay",     APPLE_PAY,     "apple pay",             "salla"),
    ("apple_pay",     APPLE_PAY,     "applepay",              "salla"),
    ("apple_pay",     APPLE_PAY,     "ابل باي",                "salla"),
    ("apple_pay",     APPLE_PAY,     "أبل باي",                "salla"),
    ("apple_pay",     APPLE_PAY,     "آبل باي",                "salla"),
    ("google_pay",    GOOGLE_PAY,    "google pay",            "salla"),
    ("google_pay",    GOOGLE_PAY,    "googlepay",             "salla"),
    ("google_pay",    GOOGLE_PAY,    "جوجل باي",               "salla"),
    ("google_pay",    GOOGLE_PAY,    "قوقل باي",               "salla"),
    ("stc_pay",       STC_PAY,       "stc pay",               "salla"),
    ("stc_pay",       STC_PAY,       "stcpay",                "salla"),
    ("stc_pay",       STC_PAY,       "اس تي سي",              "salla"),
    ("stc_pay",       STC_PAY,       "إس تي سي",              "salla"),
    ("mastercard",    MASTERCARD,    "mastercard",            "salla"),
    ("mastercard",    MASTERCARD,    "master card",           "salla"),
    ("mastercard",    MASTERCARD,    "ماستر كارد",             "salla"),
    ("mastercard",    MASTERCARD,    "ماستركارد",              "salla"),
    ("visa",          VISA,          "visa",                  "salla"),
    ("visa",          VISA,          "فيزا",                  "salla"),
    ("credit_card",   CREDIT_CARD,   "credit card",           "salla"),
    ("credit_card",   CREDIT_CARD,   "credit_card",           "salla"),
    ("credit_card",   CREDIT_CARD,   "بطاقة ائتمان",          "salla"),
    ("credit_card",   CREDIT_CARD,   "بطاقة ائتمانية",        "salla"),
    ("credit_card",   CREDIT_CARD,   "بطاقات ائتمانية",       "salla"),
    ("credit_card",   CREDIT_CARD,   "البطاقات الائتمانية",   "salla"),
    ("credit_card",   CREDIT_CARD,   "البطاقة الائتمانية",    "salla"),
    ("debit_card",    DEBIT_CARD,    "بطاقة بنكية",           "salla"),
    ("debit_card",    DEBIT_CARD,    "بطاقه بنكيه",           "salla"),
    ("debit_card",    DEBIT_CARD,    "debit card",            "salla"),
    ("debit_card",    DEBIT_CARD,    "debit_card",            "salla"),
    ("salla_wallet",  SALLA_WALLET,  "محفظة سلة",             "salla"),
    ("salla_wallet",  SALLA_WALLET,  "محفظه سلة",             "salla"),
    ("salla_wallet",  SALLA_WALLET,  "salla wallet",          "salla"),
    ("salla_wallet",  SALLA_WALLET,  "محفظة",                 "salla"),
    ("salla_wallet",  SALLA_WALLET,  "wallet",                "salla"),

    # ── Bank transfer — specific banks (rolled up under "تحويل بنكي") ──
    # ORDER MATTERS: specific bank aliases come BEFORE the generic
    # "حوالة بنكية" / "تحويل بنكي" catch-alls.
    ("bank_rajhi",    "بنك الراجحي",     "الراجحي",         "bank_transfer"),
    ("bank_rajhi",    "بنك الراجحي",     "rajhi",          "bank_transfer"),
    ("bank_inma",     "بنك الإنماء",     "الإنماء",         "bank_transfer"),
    ("bank_inma",     "بنك الإنماء",     "alinma",         "bank_transfer"),
    ("bank_inma",     "بنك الإنماء",     "inma",           "bank_transfer"),
    ("bank_ahli",     "البنك الأهلي",     "الأهلي",          "bank_transfer"),
    ("bank_ahli",     "البنك الأهلي",     "ahli",           "bank_transfer"),
    ("bank_ahli",     "البنك الأهلي",     "ncb",            "bank_transfer"),
    ("bank_riyad",    "بنك الرياض",      "الرياض",          "bank_transfer"),
    ("bank_riyad",    "بنك الرياض",      "riyad bank",     "bank_transfer"),
    ("bank_sab",      "البنك السعودي البريطاني", "ساب",      "bank_transfer"),
    ("bank_sab",      "البنك السعودي البريطاني", "sab",      "bank_transfer"),
    ("bank_albilad",  "بنك البلاد",      "البلاد",          "bank_transfer"),
    ("bank_albilad",  "بنك البلاد",      "albilad",        "bank_transfer"),
    ("bank_anb",      "البنك العربي",    "العربي",          "bank_transfer"),
    ("bank_anb",      "البنك العربي",    "anb",            "bank_transfer"),
    ("bank_aljazira", "بنك الجزيرة",    "الجزيرة",         "bank_transfer"),
    ("bank_alawwal",  "البنك الأول",    "الأول",           "bank_transfer"),
    ("bank_alawwal",  "البنك الأول",    "saudi awwal",    "bank_transfer"),

    # ── Generic bank transfer (no specific bank in the payment_method) ──
    ("bank_transfer",    BANK_TRANSFER,    "حوالة بنكية",            None),
    ("bank_transfer",    BANK_TRANSFER,    "تحويل بنكي",              None),
    ("bank_transfer",    BANK_TRANSFER,    "wire transfer",          None),
    ("bank_transfer",    BANK_TRANSFER,    "bank transfer",          None),
    # Bare "bank" — Salla sometimes uses this as the raw payment_method
    # for generic bank-transfer orders. Must come AFTER every specific
    # bank alias above so "rajhi" wins over bare "bank" when both match.
    ("bank_transfer",    BANK_TRANSFER,    " bank ",                 None),
    ("bank_transfer",    BANK_TRANSFER,    "bank",                   None),

    # ── Other standalone payment platforms ──────────────────────────────
    ("tabby",            TABBY,            "tabby",                  None),
    ("tabby",            TABBY,            "تابي",                    None),
    ("tamara",           TAMARA,           "tamara",                 None),
    ("tamara",           TAMARA,           "تمارا",                   None),
    ("emkan",            EMKAN,            "emkan",                  None),
    ("emkan",            EMKAN,            "إمكان",                   None),
    ("emkan",            EMKAN,            "امكان",                   None),
    # Raw "سلة" as a payment method → rolls up under Salla.
    ("salla_generic",    SALLA,            "سلة",                     "salla"),
    ("salla_generic",    SALLA,            "salla payments",         "salla"),
    ("salla_generic",    SALLA,            "salla_payments",         "salla"),
    ("cash_on_delivery", CASH_ON_DELIVERY, "cash on delivery",       None),
    ("cash_on_delivery", CASH_ON_DELIVERY, "cash_on_delivery",       None),
    ("cash_on_delivery", CASH_ON_DELIVERY, "cod",                    None),
    ("cash_on_delivery", CASH_ON_DELIVERY, "الدفع عند الاستلام",      None),
    ("cash_on_delivery", CASH_ON_DELIVERY, "دفع عند الاستلام",         None),
    ("cash_on_delivery", CASH_ON_DELIVERY, "عند الاستلام",            None),
]

# Display label for each rollup parent.
PARENT_LABELS = {
    "salla": SALLA,
    "bank_transfer": BANK_TRANSFER,
}

# Canonical TOP-LEVEL account keys — these are the ONLY values allowed as
# `normalized_payment_method` on an auto-created `payment_platform` account.
# Anything outside this set means the raw payment_method couldn't be classified
# and MUST NOT become an account (it gets logged to `unclassified_payment_methods`).
CANONICAL_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "salla",            # rollup for mada/Apple Pay/Visa/etc.
    "tabby",
    "tamara",
    "emkan",
    "bank_transfer",    # rollup for specific banks
    "cash_on_delivery",
})


def resolve_account_key(raw: str) -> tuple[str | None, str | None]:
    """High-level helper for the sync pipeline.

    Returns `(account_key, account_display)` where `account_key` is ALWAYS
    one of CANONICAL_TOP_LEVEL_KEYS, or `(None, None)` if the raw string is
    null/unknown and should be logged as unclassified.

    The whole app (accounts sync, reconciliation, dashboard breakdown,
    reports, settlements) should rely on this single helper instead of
    re-implementing classification logic.
    """
    sub_key, sub_display, parent_key = normalize_payment_method(raw)
    if not sub_key:
        return (None, None)
    account_key = parent_key or sub_key
    if account_key not in CANONICAL_TOP_LEVEL_KEYS:
        return (None, None)
    account_display = PARENT_LABELS.get(parent_key, sub_display) if parent_key else sub_display
    return (account_key, account_display)


# Provider buckets for settlements (kept as a thin alias of CANONICAL_TOP_LEVEL_KEYS
# but with "cod" instead of "cash_on_delivery" for backward compatibility with
# existing settlement docs).
def detect_settlement_provider(raw: str) -> str:
    """Map a raw payment method to a settlements-table provider bucket."""
    key, _ = resolve_account_key(raw)
    if key is None:
        return "other"
    if key == "cash_on_delivery":
        return "cod"
    return key


def _normalize_arabic(s: str) -> str:
    """Fold Arabic letter variants to a canonical form for matching.

    - أ / إ / آ → ا   (all hamza-bearing alef → bare alef)
    - ى         → ي  (alef maksura → ya)
    - ة         → ه  (ta marbouta → ha — matches search-engine behaviour)
    - ـ         → "" (tatweel — decorative kashida)
    """
    if not s:
        return s
    table = str.maketrans({
        "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
        "ى": "ي",
        "ة": "ه",
        "ـ": "",
    })
    return s.translate(table)


# Null markers we treat as "no payment method".
_NULL_MARKERS = {
    "", "غير محدد", "none", "n/a", "-", "null", "nan", "غير معروف",
    "\\n", "\\N", r"\n", r"\N",
}


def normalize_payment_method(raw: str) -> tuple[str, str, str | None]:
    """Return (sub_key, sub_display, parent_key) for a raw payment-method.

    Returns ("", "", None) when the input is empty / null. Unknown methods
    fall back to a slug derived from the input.
    """
    if not raw:
        return ("", "", None)
    # Null sentinels first (case-insensitive). Catches both literal "\N"
    # (the CSV/Postgres null marker) and "null"/"nan" strings.
    raw_stripped = str(raw).strip()
    if raw_stripped.lower() in _NULL_MARKERS or raw_stripped in {"\\N", "\\n"}:
        return ("", "", None)
    s = raw_stripped.lower()
    for ch in (".", ",", "،", "(", ")", "/", "\\", "_"):
        s = s.replace(ch, " ")
    s = " ".join(s.split())
    if not s or s in _NULL_MARKERS:
        return ("", "", None)
    # Apply Arabic letter folding to BOTH the input and the aliases so we
    # match across hamza / alef-maksura / ta-marbouta variants. Without
    # this, "البطاقة الإئتمانية" never matched alias "بطاقة ائتمانية".
    s_norm = _normalize_arabic(s)
    for sub_key, display, alias, parent in PAYMENT_ALIASES:
        if _normalize_arabic(alias) in s_norm:
            return (sub_key, display, parent)
    slug = "".join(c if c.isalnum() else "_" for c in raw_stripped.lower())
    slug = "_".join(filter(None, slug.split("_")))[:60] or "other"
    return (slug, raw_stripped, None)


# Salla-rollup sub-keys (used by settlements/reports to know whether a
# given canonical_key belongs to the سلة bucket).
SALLA_SUB_KEYS = frozenset(
    k for k, _, _, parent in PAYMENT_ALIASES if parent == "salla"
)

# All aliases that represent a known payment sub-method.  Consumers use this
# to distinguish a genuinely unknown label (where fuzzy matching is useful)
# from two different known card rails (which must not steal each other's fee
# rule merely because both are cards).
KNOWN_PAYMENT_SUB_KEYS = frozenset(k for k, *_ in PAYMENT_ALIASES)

# A specific card rail may fall back to the generic credit-card fee only when
# the merchant has no explicit row for that rail.  Exact rail settings always
# win first.
CARD_FALLBACK_SUB_KEYS = frozenset({
    "apple_pay", "google_pay", "visa", "mastercard", "debit_card",
})


# Previous bundled values.  They are used only by the safe settings migration:
# an existing row is upgraded when it still equals one of these untouched
# defaults.  Merchant-edited values are never overwritten automatically.
_LEGACY_FEE_DEFAULTS_BY_KEY: dict[str, tuple[float, float, float]] = {
    "mada": (1.00, 1.00, 15.00),
    "apple_pay": (2.50, 1.00, 15.00),
    "stc_pay": (2.50, 1.00, 15.00),
    "visa": (2.75, 1.00, 15.00),
    "mastercard": (2.75, 1.00, 15.00),
    "credit_card": (2.75, 1.00, 15.00),
    "debit_card": (2.00, 1.00, 15.00),
    "salla_wallet": (0.00, 0.00, 0.00),
    "tamara": (6.99, 0.00, 15.00),
    "tabby": (5.00, 0.00, 15.00),
    "emkan": (5.00, 0.00, 15.00),
    "bank_transfer": (0.00, 0.00, 0.00),
    "cash_on_delivery": (0.00, 0.00, 0.00),
}


def _fee_tuple(row: dict) -> tuple[float, float, float]:
    return (
        round(float(row.get("commission_percent") or 0), 4),
        round(float(row.get("fixed_fee") or 0), 2),
        round(float(row.get("vat_percent") or 0), 4),
    )


def migrate_payment_method_defaults(
    rows: list[dict] | None,
    *,
    current_version: str | None,
) -> tuple[list[dict], bool]:
    """Append new methods and safely upgrade untouched bundled fee defaults.

    The version protects the migration from repeating.  A merchant-edited fee
    survives because only an exact match with the prior bundled tuple is
    replaced.  Missing canonical rows (for example the new Google Pay rail)
    are appended from ``DEFAULT_PAYMENT_METHODS``.
    """
    migrated = [dict(row) for row in (rows or [])]
    by_key: dict[str, int] = {}
    for index, row in enumerate(migrated):
        key, _display, _parent = normalize_payment_method(row.get("name") or "")
        if key and key not in by_key:
            by_key[key] = index

    changed = False
    for default in DEFAULT_PAYMENT_METHODS:
        key, _display, _parent = normalize_payment_method(default.get("name") or "")
        index = by_key.get(key)
        if index is None:
            migrated.append(dict(default))
            by_key[key] = len(migrated) - 1
            changed = True
            continue

        if current_version == PAYMENT_FEE_DEFAULTS_VERSION:
            continue
        legacy = _LEGACY_FEE_DEFAULTS_BY_KEY.get(key)
        if legacy is None or _fee_tuple(migrated[index]) != legacy:
            continue
        migrated[index] = {
            **migrated[index],
            "commission_percent": default["commission_percent"],
            "fixed_fee": default["fixed_fee"],
            "vat_percent": default["vat_percent"],
        }
        if _fee_tuple(migrated[index]) != legacy:
            changed = True

    return migrated, changed
