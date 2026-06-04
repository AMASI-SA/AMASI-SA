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
    ("debit_card",    DEBIT_CARD,    "بطاقة بنكية",           "salla"),
    ("debit_card",    DEBIT_CARD,    "بطاقه بنكيه",           "salla"),
    ("debit_card",    DEBIT_CARD,    "debit card",            "salla"),
    ("debit_card",    DEBIT_CARD,    "debit_card",            "salla"),
    ("salla_wallet",  SALLA_WALLET,  "محفظة سلة",             "salla"),
    ("salla_wallet",  SALLA_WALLET,  "محفظه سلة",             "salla"),
    ("salla_wallet",  SALLA_WALLET,  "salla wallet",          "salla"),
    ("salla_wallet",  SALLA_WALLET,  "محفظة",                 "salla"),
    ("salla_wallet",  SALLA_WALLET,  "wallet",                "salla"),

    # ── Standalone payment platforms ────────────────────────────────────
    ("tabby",            TABBY,            "tabby",                  None),
    ("tabby",            TABBY,            "تابي",                    None),
    ("tamara",           TAMARA,           "tamara",                 None),
    ("tamara",           TAMARA,           "تمارا",                   None),
    ("emkan",            EMKAN,            "emkan",                  None),
    ("emkan",            EMKAN,            "إمكان",                   None),
    ("emkan",            EMKAN,            "امكان",                   None),
    ("cash_on_delivery", CASH_ON_DELIVERY, "cash on delivery",       None),
    ("cash_on_delivery", CASH_ON_DELIVERY, "cash_on_delivery",       None),
    ("cash_on_delivery", CASH_ON_DELIVERY, "cod",                    None),
    ("cash_on_delivery", CASH_ON_DELIVERY, "الدفع عند الاستلام",      None),
    ("cash_on_delivery", CASH_ON_DELIVERY, "دفع عند الاستلام",         None),
    ("bank_transfer",    BANK_TRANSFER,    "bank transfer",          None),
    ("bank_transfer",    BANK_TRANSFER,    "تحويل بنكي",              None),
    ("bank_transfer",    BANK_TRANSFER,    "حوالة بنكية",             None),
    ("bank_transfer",    BANK_TRANSFER,    "wire transfer",          None),
]

# Display label for each rollup parent.
PARENT_LABELS = {
    "salla": SALLA,
}


def normalize_payment_method(raw: str) -> tuple[str, str, str | None]:
    """Return (sub_key, sub_display, parent_key) for a raw payment-method.

    Returns ("", "", None) when the input is empty / غير محدد. Unknown
    methods fall back to a slug derived from the input (parent_key=None).
    """
    if not raw:
        return ("", "", None)
    s = str(raw).strip().lower()
    for ch in (".", ",", "،", "(", ")", "/", "\\"):
        s = s.replace(ch, " ")
    s = " ".join(s.split())
    if not s or s in {"غير محدد", "none", "n/a", "-"}:
        return ("", "", None)
    for sub_key, display, alias, parent in PAYMENT_ALIASES:
        if alias in s:
            return (sub_key, display, parent)
    slug = "".join(c if c.isalnum() else "_" for c in str(raw).strip().lower())
    slug = "_".join(filter(None, slug.split("_")))[:60] or "other"
    return (slug, str(raw).strip(), None)


# Salla-rollup sub-keys (used by settlements/reports to know whether a
# given canonical_key belongs to the سلة bucket).
SALLA_SUB_KEYS = frozenset(
    k for k, _, _, parent in PAYMENT_ALIASES if parent == "salla"
)
