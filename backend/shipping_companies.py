"""Single source of truth for shipping-company nomenclature (iter-72).

Mirrors `payment_methods.py` for the shipping side. Every place in the
codebase that reads or writes a `shipping_company` value MUST go through
`normalize_shipping_company()` so we get a stable canonical key + display
name regardless of how the raw value was spelt by Salla / Excel / Make.com.

Concrete bugs this prevents:
  • Excel exports prefix every text cell with a leading apostrophe (')
    to force text-mode. Before this module those apostrophes stayed in
    the DB so `'iMile للتوصيل'` and `iMile للتوصيل` showed up as TWO
    distinct rows in the shipping_breakdown table.
  • Zero-width / RTL / NBSP characters silently splitting groups.
  • Case + spacing inconsistencies between Latin spellings
    (`iMile` / `IMile` / `i-mile`).
"""
from __future__ import annotations

# ── Canonical display names ────────────────────────────────────────────
IMILE = "iMile للتوصيل"
MANDOOB_RIYADH = "مندوب الرياض"
MANDOOB = "مندوب"
SMSA = "سمسا"
ARAMEX = "أرامكس"
DHL = "DHL"
NAQEL = "نقل"
FEDEX = "FedEx"
ZAJIL = "زاجل"
BOSTA = "بوسطة"
JT_EXPRESS = "J&T Express"
PICKUP = "استلام من المتجر"
UNKNOWN = "غير محدد"


# ── Alias table — ORDER MATTERS (specific before generic) ──────────────
# Each tuple is: (canonical_key, display_name, alias_substring_lowercased)
# Matching is case-insensitive substring AFTER strip/normalise.
SHIPPING_ALIASES: list[tuple[str, str, str]] = [
    # iMile
    ("imile",            IMILE,            "imile"),
    ("imile",            IMILE,            "i mile"),
    ("imile",            IMILE,            "i-mile"),
    ("imile",            IMILE,            "ايميل للتوصيل"),

    # مندوب الرياض (specific BEFORE bare "mandoob")
    ("mandoob_riyadh",   MANDOOB_RIYADH,   "مندوب الرياض"),
    ("mandoob_riyadh",   MANDOOB_RIYADH,   "mandoob riyadh"),

    # مندوب (generic)
    ("mandoob",          MANDOOB,          "مندوب"),
    ("mandoob",          MANDOOB,          "mandoob"),

    # سمسا / SMSA
    ("smsa",             SMSA,             "سمسا"),
    ("smsa",             SMSA,             "smsa"),

    # أرامكس / Aramex
    ("aramex",           ARAMEX,           "أرامكس"),
    ("aramex",           ARAMEX,           "ارامكس"),
    ("aramex",           ARAMEX,           "aramex"),

    # International couriers
    ("dhl",              DHL,              "dhl"),
    ("fedex",            FEDEX,            "fedex"),
    ("fedex",            FEDEX,            "fed ex"),

    # Naqel / نقل
    ("naqel",            NAQEL,            "naqel"),
    ("naqel",            NAQEL,            "نقل"),

    # Zajil
    ("zajil",            ZAJIL,            "zajil"),
    ("zajil",            ZAJIL,            "زاجل"),

    # Bosta
    ("bosta",            BOSTA,            "bosta"),
    ("bosta",            BOSTA,            "بوسطة"),

    # J&T Express
    ("jt_express",       JT_EXPRESS,       "j&t"),
    ("jt_express",       JT_EXPRESS,       "j t express"),
    ("jt_express",       JT_EXPRESS,       "jt express"),

    # Pickup / استلام من المتجر
    ("pickup",           PICKUP,           "استلام من المتجر"),
    ("pickup",           PICKUP,           "pickup"),
    ("pickup",           PICKUP,           "تسليم مباشر"),
]


# ── Null / unknown markers — these never become a canonical row ────────
_NULL_MARKERS = frozenset({
    "", "null", "none", "nan", "n/a", "na", "-", "—", "غير محدد", "\\n", "\\N",
})


def _scrub(raw: str | None) -> str:
    """Aggressive whitespace + invisible-char + quote stripper.

    Designed to neutralise:
      • Excel's "force-text" leading apostrophe (`'`).
      • Curly apostrophes (`’`), back-ticks (`).
      • Double quotes (").
      • Zero-width characters: U+200B, U+200C, U+200D, U+200E, U+200F,
        U+202A-U+202E, U+2066-U+2069, U+FEFF (BOM).
      • Trailing whitespace.
    """
    if raw is None:
        return ""
    s = str(raw)
    # Strip zero-width / direction marks anywhere in the string.
    for ch in (
        "\u200b", "\u200c", "\u200d", "\u200e", "\u200f",
        "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
        "\u2066", "\u2067", "\u2068", "\u2069",
        "\ufeff",
    ):
        s = s.replace(ch, "")
    s = s.strip()
    # Strip leading + trailing apostrophes / quotes (any number).
    while s and s[0] in ("'", "’", "`", "\""):
        s = s[1:]
    while s and s[-1] in ("'", "’", "`", "\""):
        s = s[:-1]
    return s.strip()


def normalize_shipping_company(raw: str | None) -> tuple[str, str]:
    """Return `(canonical_key, display_name)` for any raw shipping value.

    Unrecognised but non-empty values flow through as `("other:<slug>",
    "<scrubbed value>")` so they remain visible in reports (the operator
    can then add an alias to the table above). Truly empty / "غير محدد"
    inputs become `("unknown", "غير محدد")`.
    """
    s = _scrub(raw)
    if not s:
        return ("unknown", UNKNOWN)
    if s.lower() in _NULL_MARKERS:
        return ("unknown", UNKNOWN)

    s_lower = s.lower()
    for key, display, alias in SHIPPING_ALIASES:
        if alias in s_lower:
            return (key, display)

    # Unknown but non-empty — keep visible, just give it a stable slug
    # so /shipping_breakdown can still group consistent rows.
    slug = "other:" + "".join(c if c.isalnum() else "_" for c in s_lower)[:48]
    return (slug, s)


def scrub_shipping_company(raw: str | None) -> str:
    """Return the cleaned display string only (no canonical key).

    Used at the write boundary (excel parser / webhooks / import jobs) so
    raw payloads never persist apostrophes or BOM characters.
    """
    _, display = normalize_shipping_company(raw)
    return display
