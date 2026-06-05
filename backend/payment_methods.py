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
STC_PAY            = "STC Pay"
VISA               = "Visa"
MASTERCARD         = "MasterCard"
CREDIT_CARD        = "بطاقة ائتمانية"
DEBIT_CARD         = "بطاقة بنكية"
SALLA_WALLET       = "محفظة سلة"


# ── Default payment methods (used to seed user settings) ─────────────────
# Order matters → it's the order shown in Settings → Payment Methods.
DEFAULT_PAYMENT_METHODS: list[dict] = [
    # Salla card rails — editable so the merchant can override Salla's
    # actual commission for each rail.
    {"name": MADA,             "commission_percent": 1.00, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": APPLE_PAY,        "commission_percent": 2.50, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": STC_PAY,          "commission_percent": 2.50, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": VISA,             "commission_percent": 2.75, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": MASTERCARD,       "commission_percent": 2.75, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": CREDIT_CARD,      "commission_percent": 2.75, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": DEBIT_CARD,       "commission_percent": 2.00, "fixed_fee": 1.0, "vat_percent": 15.0},
    {"name": SALLA_WALLET,     "commission_percent": 0.00, "fixed_fee": 0.0, "vat_percent": 0.0},
    # BNPL — own accounts
    {"name": TAMARA,           "commission_percent": 6.99, "fixed_fee": 0.0, "vat_percent": 15.0},
    {"name": TABBY,            "commission_percent": 5.00, "fixed_fee": 0.0, "vat_percent": 15.0},
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
