"""Iter-250b · P1.5.ab — Unit checks for the unification forensic.

Validates the helper utilities that classify suppliers across
`db.suppliers` and `db.counterparties`. No DB hit — we only test the
pure helpers (`_norm_lower`, `_norm_phone`).
"""
from suppliers_unification_forensic_routes import (
    _norm_lower,
    _norm_phone,
)


def test_norm_lower_collapses_whitespace():
    assert _norm_lower("  مَصنع   العمبري  ") == "مَصنع العمبري"
    assert _norm_lower("Hello   WORLD") == "hello world"
    assert _norm_lower(None) == ""


def test_norm_lower_strips_tatweel():
    # Arabic tatweel U+0640 must be removed so "العنبري" and
    # "العـنبري" collide as duplicates.
    assert _norm_lower("العـنبري") == "العنبري"


def test_norm_phone_keeps_digits_only():
    assert _norm_phone("+966 50 148 3166") == "966501483166"
    assert _norm_phone("050-148-3166") == "0501483166"
    assert _norm_phone(None) == ""
    assert _norm_phone("abc") == ""
