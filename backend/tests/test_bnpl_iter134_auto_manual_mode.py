"""Iter-134-Auto — BNPL commission_mode (auto vs manual) toggle.

Verifies:
  1. get_settings returns commission_mode='auto' by default.
  2. save_settings accepts commission_mode in payload and persists it.
  3. Invalid values are ignored.
  4. Auto preset values match the four verified Tabby reports.

Uses a minimal in-memory async-DB stub so the test runs without a
real Mongo instance.
"""
import pytest
import pytest_asyncio

from bnpl import config_store
from bnpl.config_store import DEFAULTS, get_settings, save_settings


class _Coll:
    """Bare-minimum stub mimicking the motor.AsyncIOMotorCollection
    API used by config_store: find_one, update_one (with $set, $unset,
    $setOnInsert and upsert)."""

    def __init__(self):
        self.docs: list[dict] = []

    async def create_index(self, *_a, **_kw):
        return None

    @staticmethod
    def _match(doc, q):
        return all(doc.get(k) == v for k, v in q.items())

    async def find_one(self, q, projection=None):
        for d in self.docs:
            if self._match(d, q):
                return dict(d)
        return None

    async def update_one(self, q, update, upsert=False):
        target = None
        for d in self.docs:
            if self._match(d, q):
                target = d
                break
        if target is None:
            if not upsert:
                return None
            target = {}
            target.update(update.get("$setOnInsert", {}))
            target.update(q)
            self.docs.append(target)
        target.update(update.get("$set", {}))
        for k in update.get("$unset", {}):
            target.pop(k, None)
        return None


class _DB:
    def __init__(self):
        self.bnpl_settings = _Coll()
        self.users = _Coll()


@pytest_asyncio.fixture
async def db():
    return _DB()


@pytest.mark.asyncio
async def test_default_commission_mode_is_auto(db):
    settings = await get_settings(db, user_id="u1", provider="tabby")
    assert settings["commission_mode"] == "auto"


@pytest.mark.asyncio
async def test_save_commission_mode_manual_then_auto(db):
    res = await save_settings(
        db, "u1", "tabby",
        {"commission_mode": "manual", "mdr_percent": 0.05},
    )
    assert res["commission_mode"] == "manual"
    assert res["mdr_percent"] == 0.05

    res2 = await save_settings(db, "u1", "tabby", {"commission_mode": "auto"})
    assert res2["commission_mode"] == "auto"


@pytest.mark.asyncio
async def test_invalid_commission_mode_ignored(db):
    await save_settings(db, "u1", "tabby", {"commission_mode": "manual"})
    res = await save_settings(db, "u1", "tabby", {"commission_mode": "garbage"})
    # invalid value left previous mode untouched
    assert res["commission_mode"] == "manual"


def test_tabby_auto_preset_matches_invoice_contract():
    """Tabby uses a split 6.99% MDR + SAR 1 and no unconditional payout fee."""
    tabby = DEFAULTS["tabby"]
    assert tabby["mdr_percent"] == pytest.approx(0.0699)
    assert tabby["refundable_commission_percent"] == pytest.approx(0.0499)
    assert tabby["fixed_fee_per_order"] == pytest.approx(1.0)
    assert tabby["vat_on_fees_percent"] == pytest.approx(0.15)
    assert tabby["settlement_fee_per_invoice"] == pytest.approx(0.0)
    assert tabby["settlement_fee_vat_applicable"] is True


def test_tamara_auto_preset_uses_canonical_rates():
    tamara = DEFAULTS["tamara"]
    assert tamara["mdr_percent"] == pytest.approx(0.0699)
    assert tamara["refundable_commission_percent"] == pytest.approx(0.0699)
    assert tamara["fixed_fee_per_order"] == pytest.approx(1.50)
    assert tamara["settlement_fee_per_invoice"] == pytest.approx(0.0)
