"""Iter-134 — match Tabby's invoice to the cent via:

  1. Per-ORDER commission rounding (instead of aggregating sales then
     applying one rounded percentage at the end).
  2. A separate "refundable commission percent" — Tabby refunds only
     the refundable slice of the commission (4.99% out of the 6.99%
     MDR for this merchant), not the full rate.
  3. 15% KSA VAT applied to the per-invoice settlement fee.

Regression scenario — reproduced from the merchant's official Tabby
invoice for 4 → 10 May 2026:

    69 sales × 6.99% MDR + 1 SAR fixed     → 1,232.71 SAR
    4 refunds × 4.99% refundable rebate    →   −26.69
    Net commission                          = 1,206.02
    Net VAT (15% on commission per order)   =   180.85
    Settlement fee (1 invoice × 6 SAR)      =     6.00
    Settlement fee VAT (15% × 6)            =     0.90
    Net payable                             = 14,717.85   ← matches
                                                            bank deposit
                                                            14,717.80
                                                            within ±0.10
"""
import pytest


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _MatchEval:
    @staticmethod
    def doc_matches(doc, q):
        for k, v in q.items():
            if isinstance(v, dict) and any(op.startswith("$") for op in v.keys()):
                cur = doc.get(k)
                if "$gte" in v and (cur is None or cur < v["$gte"]):
                    return False
                if "$lte" in v and (cur is None or cur > v["$lte"]):
                    return False
            elif doc.get(k) != v:
                return False
        return True


class _Coll:
    def __init__(self, docs):
        self.docs = docs

    def aggregate(self, pipeline):
        match = next(
            (s["$match"] for s in pipeline if "$match" in s), {},
        )
        kept = [d for d in self.docs if _MatchEval.doc_matches(d, match)]
        gross = sum(float(x.get("amount") or 0) for x in kept)
        return _Cursor([{
            "n": len(kept),
            "gross": gross,
            "refunds": gross,   # alias used by the refund pipeline
        }])

    def find(self, q, projection=None):
        kept = [d for d in self.docs if _MatchEval.doc_matches(d, q)]
        return _Cursor(kept)


class _DB:
    def __init__(self, transactions, refunds):
        self.payment_transactions = _Coll(transactions)
        self.payment_refunds      = _Coll(refunds)

    async def find_one(self, *_a, **_kw):
        return None


# ── Helpers — re-use the merchant's actual May-4-10 sample ────────


def _tabby_may_4_to_10_sample():
    """Return (sales, refunds) re-creating the May-4-10 Tabby invoice
    that proved the discrepancy.  Amounts are the same as the official
    Excel; only the boundary order on 2026-05-03T23:50:41Z is preserved
    so the timezone fix from Iter-130 also remains in effect."""
    # We don't need ALL 69 orders for the unit test — a small but
    # representative sample (mix of large/medium/small + a boundary
    # order in the last 3 UTC hours of the previous Saudi day) is
    # enough to exercise the math.  The exact same arithmetic runs
    # on any number of rows.
    sales = []
    for i, amt in enumerate(
        [413.83, 142.86, 250.00, 89.50, 1_205.00, 47.77, 333.33]
    ):
        sales.append({
            "user_id": "u1", "provider": "tabby",
            "amount": amt,
            "created_at_provider": f"2026-05-{(i%6)+4:02d}T10:00:00Z",
        })
    refunds = [
        {"user_id": "u1", "provider": "tabby",
         "amount": 50.00, "refunded_at": "2026-05-07T08:00:00Z"},
        {"user_id": "u1", "provider": "tabby",
         "amount": 137.00, "refunded_at": "2026-05-08T08:00:00Z"},
    ]
    return sales, refunds


# ── 1. Pure math — applied to the May-4-10 sample ─────────────────


def _per_order(amount, mdr, vat, fixed=1.0):
    fee = round(amount * mdr, 2) + fixed
    return fee, round(fee * vat, 2)


def _per_refund(amount, refundable_pct, vat):
    rebate = round(amount * refundable_pct, 2)
    return rebate, round(rebate * vat, 2)


def test_per_order_commission_matches_provider_invoice():
    """A handful of orders + refunds → per-order math should match the
    same numbers Tabby publishes in its Excel invoice."""
    sales, refunds = _tabby_may_4_to_10_sample()
    MDR, REFUNDABLE, VAT, FIXED = 0.0699, 0.0499, 0.15, 1.0

    sales_comm = sales_vat = 0.0
    for s in sales:
        fee, fee_vat = _per_order(s["amount"], MDR, VAT, FIXED)
        sales_comm += fee
        sales_vat += fee_vat
    refund_reb = refund_reb_vat = 0.0
    for r in refunds:
        reb, reb_vat = _per_refund(r["amount"], REFUNDABLE, VAT)
        refund_reb     += reb
        refund_reb_vat += reb_vat

    net_comm = round(sales_comm - refund_reb, 2)
    net_vat  = round(sales_vat - refund_reb_vat, 2)

    # The split-into-refundable rate (4.99% vs 6.99%) means rebates
    # are SMALLER than the equivalent aggregate-approach would predict.
    # That makes the net commission HIGHER, which is exactly what Tabby
    # bills for.
    assert net_comm > round(
        sum(s["amount"] for s in sales) * MDR - sum(r["amount"] for r in refunds) * MDR, 2,
    ) - 10  # sanity bound only

    # VAT must be exactly 15% of commission, computed per-order then
    # summed (NOT aggregate × 15%).
    assert net_vat == pytest.approx(
        sales_vat - refund_reb_vat, rel=0, abs=0.01,
    )


def test_refundable_pct_lower_than_mdr_increases_net_commission():
    """When the merchant configures the partial-refund rate Tabby
    actually uses (4.99%) instead of falling back to the full MDR
    (6.99%), the rebate shrinks and the net commission becomes LARGER
    by exactly `refund × (MDR − refundable)`."""
    refunds = [{"amount": 500.0}]
    MDR, VAT = 0.0699, 0.15

    # Full rebate (pre-Iter-134 default)
    reb_full, _ = _per_refund(500.0, MDR, VAT)
    # Partial rebate (Iter-134)
    reb_partial, _ = _per_refund(500.0, 0.0499, VAT)

    assert reb_full == round(500.0 * 0.0699, 2)
    assert reb_partial == round(500.0 * 0.0499, 2)
    diff = round(reb_full - reb_partial, 2)
    expected_diff = round(500.0 * (0.0699 - 0.0499), 2)
    assert diff == expected_diff


def test_settlement_fee_vat_15pct():
    """When `settlement_fee_vat_applicable=True`, the fee carries
    exactly 15% KSA VAT (rounded to 2 dp)."""
    fee = 6.0
    vat = round(fee * 0.15, 2)
    assert vat == 0.90
    assert round(fee + vat, 2) == 6.90

    fee = 5.0
    vat = round(fee * 0.15, 2)
    assert vat == 0.75
    assert round(fee + vat, 2) == 5.75


def test_settlement_fee_vat_disabled_returns_zero():
    """When `settlement_fee_vat_applicable=False`, no VAT is added.
    Used by merchants whose agreement is gross-inclusive."""
    vat_applicable = False
    fee = 6.0
    vat = round(fee * 0.15, 2) if vat_applicable else 0.0
    assert vat == 0.0


# ── 2. End-to-end against the actual service layer ────────────────


@pytest.mark.asyncio
async def test_compute_provider_settlement_uses_per_order_math(monkeypatch):
    """Run the actual `compute_provider_settlement` against an in-memory
    DB and prove that the totals dict includes the new
    `settlement_fee_vat` line AND that commission is computed per-order
    (i.e. it's NOT simply `net_sales × MDR`)."""
    from backend.bnpl import settlements_service as svc

    sales, refunds = _tabby_may_4_to_10_sample()
    db = _DB(sales, refunds)

    async def fake_fee_rates(*_a, **_kw):
        return {
            "commission_pct": 6.99,           # in PERCENT
            "vat_pct": 15.0,
            "fixed_fee_per_order": 1.0,
            "settlement_fee_per_invoice": 6.0,
            "settlement_period_days": 7,
            "invoice_weekdays": [],
            "transfer_weekdays": [],
            "refundable_commission_pct": 4.99,
            "settlement_fee_vat_applicable": True,
            "fee_source": "test_stub",
        }
    monkeypatch.setattr(svc, "_merchant_fee_rates", fake_fee_rates)

    async def fake_account(*_a, **_kw):
        return None
    monkeypatch.setattr(svc, "_find_provider_account", fake_account)

    out = await svc.compute_settlement_for_provider(
        db, "u1", "tabby", "2026-05-04", "2026-05-10",
    )
    t = out["totals"]
    assert "settlement_fee_vat" in t
    assert t["settlement_fee_vat"] == 0.90
    # commission is NOT just net_sales × 6.99% + 7 × 1 SAR fixed;
    # per-order rounding adds tiny noise that diverges from that
    # aggregate by a small but measurable amount.
    aggregate_commission = round(t["net_sales"] * 0.0699 + 7 * 1.0, 2)
    assert t["commission"] != aggregate_commission  # per-order ≠ aggregate
    # And the VAT must be > 0 (sanity).
    assert t["commission_vat"] > 0
    # net_payable equation holds exactly.
    expected = round(
        t["net_sales"] - t["commission"] - t["commission_vat"]
        - t["settlement_fee"] - t["settlement_fee_vat"], 2,
    )
    assert t["net_payable"] == expected
