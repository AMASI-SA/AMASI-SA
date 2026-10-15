"""Iter-178 — Regression test for COD fee % save error.

Bug
====
The ShippingCompanySettings page accepts ``cod_fee_percent`` and
sends it to ``PUT /api/settings``. The backend Pydantic schema
constrains the field to ``[0, 1]`` (decimal: ``0.05`` = 5%).

A merchant entering ``5`` (intending 5%) hit a 422 validation error:
::

    cod_fee_percent: Input should be less than or equal to 1

The toast then displayed "فشل الحفظ — راجع الكونسول" with no clue.

Fix
===
Frontend now displays ``cod_fee_percent`` as a percent (0-100) and
divides by 100 before sending. A pre-save clamp also guards against
any legacy row whose stored value is already out of range.

These tests pin the backend contract so future schema changes don't
silently break the UI.
"""
from __future__ import annotations

import os
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, "/app/backend")


@pytest.fixture(autouse=True)
def _set_env():
    # ShippingCompany imports from server which needs MONGO_URL.
    os.environ.setdefault("MONGO_URL", "mongodb://localhost:27017")
    os.environ.setdefault("DB_NAME", "hesab_test_iter178")
    yield


def test_shipping_company_accepts_zero_cod_fee():
    """A freshly added company has cod_fee_percent=0 — must validate."""
    from server import ShippingCompany
    c = ShippingCompany(
        name="SMSA",
        cost_per_order=15,
        vat_percent=15,
        is_deferred=True,
        cod_fee_percent=0.0,
        cod_fee_fixed_per_order=0.0,
    )
    assert c.cod_fee_percent == 0.0


def test_shipping_company_accepts_decimal_cod_fee():
    """0.05 (= 5%) must validate."""
    from server import ShippingCompany
    c = ShippingCompany(
        name="iMile",
        cost_per_order=12,
        vat_percent=15,
        is_deferred=True,
        cod_fee_percent=0.05,
        cod_fee_fixed_per_order=2,
    )
    assert c.cod_fee_percent == pytest.approx(0.05)


def test_shipping_company_rejects_percent_shaped_value():
    """The historical bug — a merchant entering 5 (instead of 0.05).
    The schema MUST reject it so we surface a meaningful error,
    rather than silently storing 500% fee."""
    from server import ShippingCompany
    with pytest.raises(ValidationError):
        ShippingCompany(
            name="Aramex",
            cost_per_order=20,
            vat_percent=15,
            is_deferred=True,
            cod_fee_percent=5.0,
        )


def test_shipping_company_rejects_negative_cod_fee():
    from server import ShippingCompany
    with pytest.raises(ValidationError):
        ShippingCompany(
            name="Aramex",
            cost_per_order=20,
            is_deferred=True,
            cod_fee_percent=-0.01,
        )


def test_shipping_company_accepts_upper_bound_cod_fee():
    """1.0 (= 100%) is the inclusive upper bound."""
    from server import ShippingCompany
    c = ShippingCompany(
        name="Edge",
        cost_per_order=0,
        is_deferred=True,
        cod_fee_percent=1.0,
    )
    assert c.cod_fee_percent == 1.0


def test_shipping_company_accepts_fixed_fee():
    from server import ShippingCompany
    c = ShippingCompany(
        name="SMSA",
        cost_per_order=15,
        is_deferred=True,
        cod_fee_percent=0.0,
        cod_fee_fixed_per_order=4.5,
    )
    assert c.cod_fee_fixed_per_order == 4.5


def test_shipping_company_rejects_negative_fixed_fee():
    from server import ShippingCompany
    with pytest.raises(ValidationError):
        ShippingCompany(
            name="X",
            cost_per_order=0,
            is_deferred=True,
            cod_fee_fixed_per_order=-1,
        )


def test_settings_in_accepts_companies_with_cod_fields():
    """The aggregate SettingsIn payload (what PUT /settings consumes)
    must round-trip a company with cod_fee_* fields."""
    from server import SettingsIn
    payload = SettingsIn(
        payment_methods=[],
        shipping_companies=[
            {
                "name": "SMSA",
                "cost_per_order": 15,
                "vat_percent": 15,
                "is_deferred": True,
                "cod_fee_percent": 0.05,
                "cod_fee_fixed_per_order": 2.0,
            }
        ],
    )
    assert payload.shipping_companies[0].cod_fee_percent == pytest.approx(0.05)
    assert payload.shipping_companies[0].cod_fee_fixed_per_order == 2.0


def test_shipping_company_accepts_non_overlapping_cod_fee_tiers():
    from server import ShippingCompany
    company = ShippingCompany(
        name="SMSA",
        cost_per_order=20,
        vat_percent=15,
        is_deferred=True,
        cod_fee_tiers=[
            {
                "min_amount": 50, "max_amount": 1000,
                "min_inclusive": True, "max_inclusive": True,
                "commission_percent": 0.01, "fixed_fee": 2,
                "vat_percent": 15,
            },
            {
                "min_amount": 1000, "max_amount": None,
                "min_inclusive": False, "max_inclusive": True,
                "commission_percent": 0.02, "fixed_fee": 5,
                "vat_percent": 15,
            },
        ],
    )
    assert len(company.cod_fee_tiers) == 2
    assert company.cod_fee_tiers[0].commission_percent == pytest.approx(0.01)


def test_shipping_company_rejects_overlapping_cod_fee_tiers():
    from server import ShippingCompany
    with pytest.raises(ValidationError, match="overlap"):
        ShippingCompany(
            name="SMSA", cost_per_order=20, is_deferred=True,
            cod_fee_tiers=[
                {
                    "min_amount": 0, "max_amount": 1000,
                    "commission_percent": 0.01,
                },
                {
                    "min_amount": 1000, "max_amount": None,
                    "commission_percent": 0.02,
                },
            ],
        )
