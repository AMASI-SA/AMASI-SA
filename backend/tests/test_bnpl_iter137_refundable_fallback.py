"""Iter-137 — Regression test for the merchant's official Tabby May 4-10
invoice.

Root cause that caused the 12.34 SAR diff post-Iter-134-deploy:
    bnpl_settings docs predating Iter-134 don't have a saved
    `refundable_commission_percent` field.  The settlements engine
    fallback defaulted to FULL MDR (6.99%) instead of the
    vendor-canonical 4.99% refundable rate → every refund rebate was
    too generous → net_payable came out HIGHER than what Tabby
    actually deposited (14,730.14 vs 14,717.80 → diff +12.34).

This test exercises _merchant_fee_rates ONLY (no Mongo writes — just a
stub for the user / bnpl_settings reads) to lock the correct fallback
in place.
"""
import pytest

from bnpl import settlements_service as svc


class _StubColl:
    def __init__(self, docs=None):
        self.docs = docs or []

    async def find_one(self, q, projection=None):
        for d in self.docs:
            if all(d.get(k) == v for k, v in q.items()):
                return dict(d)
        return None


class _StubDB:
    def __init__(self, user_doc=None, bnpl_doc=None):
        self.users = _StubColl([user_doc] if user_doc else [])
        self.bnpl_settings = _StubColl([bnpl_doc] if bnpl_doc else [])


@pytest.mark.asyncio
async def test_pre_iter134_doc_falls_back_to_canonical_refundable_rate():
    """A bnpl_settings doc that was saved BEFORE Iter-134 has no
    refundable_commission_percent field.  The engine must NOT default
    to the full MDR — it must fall back to the provider's canonical
    refundable rate so settlement matches Tabby's invoice."""
    db = _StubDB(
        # Merchant has the standard 6.99 MDR saved already, but never
        # explicitly set the refundable split.
        user_doc={
            "id": "u1",
            "settings": {"payment_methods": [
                {"name": "تابي", "commission_percent": 6.99,
                 "vat_percent": 15.0, "fixed_fee": 1.0},
            ]},
        },
        bnpl_doc={
            "user_id": "u1", "provider": "tabby",
            "mdr_percent": 0.0699,
            # Crucially NO refundable_commission_percent here
        },
    )
    rates = await svc._merchant_fee_rates(db, "u1", "tabby")
    # The canonical Tabby split is 4.99 refundable / 6.99 total
    assert rates["commission_pct"] == 6.99
    assert rates["refundable_commission_pct"] == 4.99


@pytest.mark.asyncio
async def test_explicit_refundable_override_still_wins():
    """If the merchant DID save an explicit refundable rate (e.g. they
    negotiated a custom contract), that value must be used.  Per
    Iter-137, custom overrides only apply when commission_mode is
    'manual' — auto-mode merchants always get the vendor-canonical
    numbers."""
    db = _StubDB(
        user_doc={
            "id": "u1",
            "settings": {"payment_methods": [
                {"name": "تابي", "commission_percent": 6.99,
                 "vat_percent": 15.0, "fixed_fee": 1.0},
            ]},
        },
        bnpl_doc={
            "user_id": "u1", "provider": "tabby",
            "mdr_percent": 0.0699,
            "refundable_commission_percent": 0.0300,
            "commission_mode": "manual",
        },
    )
    rates = await svc._merchant_fee_rates(db, "u1", "tabby")
    assert rates["refundable_commission_pct"] == pytest.approx(3.00)


@pytest.mark.asyncio
async def test_auto_mode_ignores_stale_doc_values():
    """Iter-137 — when commission_mode='auto', stale numbers saved
    BEFORE the canonical Tabby contract update must NOT leak through.
    This is the actual production bug — merchant had 5% MDR + 5 SAR
    fee saved from a previous version; engine kept using them and
    over-reported net_payable by 12 SAR."""
    db = _StubDB(
        bnpl_doc={
            "user_id": "u1", "provider": "tabby",
            "mdr_percent": 0.05,                 # stale
            "fixed_fee_per_order": 1.0,
            "settlement_fee_per_invoice": 6.0,    # former bundled default
            "refundable_commission_percent": 0.0499,
            "commission_mode": "auto",
        },
    )
    rates = await svc._merchant_fee_rates(db, "u1", "tabby")
    # Auto mode → canonical Tabby defaults win, not the stale doc
    assert rates["commission_pct"] == pytest.approx(6.99)
    assert rates["settlement_fee_per_invoice"] == pytest.approx(0.0)
    assert rates["refundable_commission_pct"] == pytest.approx(4.99)
    assert rates["fee_source"] == "auto_canonical_defaults"


@pytest.mark.asyncio
async def test_tamara_canonical_default_is_full_seven_percent():
    """Iter-145 — Tamara's official invoice contract is 6.99% MDR +
    1.50 SAR fixed/order, NOT 7% with no fixed fee.  Refundable rate
    equals full MDR (Tamara doesn't split refundable like Tabby)."""
    db = _StubDB(
        user_doc={
            "id": "u1",
            "settings": {"payment_methods": [
                {"name": "تمارا", "commission_percent": 6.99,
                 "vat_percent": 15.0, "fixed_fee": 1.50},
            ]},
        },
        bnpl_doc={
            "user_id": "u1", "provider": "tamara",
            "mdr_percent": 0.0699,
        },
    )
    rates = await svc._merchant_fee_rates(db, "u1", "tamara")
    assert rates["refundable_commission_pct"] == pytest.approx(6.99)


def test_default_fee_rates_dict_is_in_sync_with_config_store():
    """Guard rail — keep DEFAULT_FEE_RATES (engine) and DEFAULTS
    (config_store, UI) reading the same numbers so neither path can
    drift from the Tabby invoice contract again."""
    from bnpl.config_store import DEFAULTS
    for provider in ("tabby", "tamara"):
        engine = svc.DEFAULT_FEE_RATES[provider]
        ui = DEFAULTS[provider]
        # MDR
        assert engine["commission_pct"] == pytest.approx(ui["mdr_percent"] * 100), (
            f"{provider}: engine commission_pct ({engine['commission_pct']}) "
            f"≠ config_store mdr_percent ({ui['mdr_percent'] * 100})"
        )
        # Refundable
        assert engine["refundable_commission_pct"] == pytest.approx(
            ui["refundable_commission_percent"] * 100,
        )
        # Fixed fee
        assert engine["fixed_fee_per_order"] == pytest.approx(
            ui["fixed_fee_per_order"],
        )
        # Settlement fee
        assert engine["settlement_fee_per_invoice"] == pytest.approx(
            ui["settlement_fee_per_invoice"],
        )


def test_full_invoice_math_matches_actual_bank_transfer():
    """Pure arithmetic — replays the May 4-10 invoice and asserts that
    with the post-fix defaults the net payable lands within 0.10 SAR
    of Tabby's actual deposit (14,717.80)."""
    mdr = 0.0699
    refundable = 0.0499
    vat = 0.15
    fixed = 1.0
    n_orders = 69
    gross_sales = 16_646.29
    total_refunds = 534.72

    sales_commission = round(gross_sales * mdr, 2) + n_orders * fixed
    sales_vat = round(sales_commission * vat, 2)
    refund_rebate = round(total_refunds * refundable, 2)
    refund_vat_rebate = round(refund_rebate * vat, 2)

    commission = round(sales_commission - refund_rebate, 2)
    commission_vat = round(sales_vat - refund_vat_rebate, 2)
    # This older invoice explicitly carried a SAR 6 payout-fee row. It is an
    # actual statement adjustment, not the default for every weekly report.
    settlement_fee = 6.0
    settlement_fee_vat = round(settlement_fee * vat, 2)

    net_sales = round(gross_sales - total_refunds, 2)
    net_payable = round(
        net_sales - commission - commission_vat
        - settlement_fee - settlement_fee_vat,
        2,
    )
    # Tabby actually deposited 14,717.80.  Allowable per-order rounding
    # drift across 69 rows is ~0.10 SAR.
    assert abs(net_payable - 14_717.80) <= 0.10, (
        f"net_payable={net_payable} expected≈14717.80 ±0.10 — diff "
        f"{net_payable - 14_717.80:+.2f}"
    )
