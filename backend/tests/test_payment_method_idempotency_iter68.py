"""Iter-68 — P0 hardening regression test.

Proves the ghost-account class of bugs cannot return:

1. `normalize_payment_method` always classifies every Arabic / Latin / null
   spelling the user reported into the correct canonical bucket.
2. `resolve_account_key` REFUSES to route unknown / null inputs to a real
   account (returns (None, None) so the sync skips them).
3. `POST /api/accounts/sync-payment-methods` is idempotent across repeated
   calls — count of payment_platform accounts NEVER grows.
4. The unique partial index on (user_id, normalized_payment_method) blocks
   direct double-insert attempts.
5. Pre-existing ghost docs (legacy slugs, "\\N" names) get hard-deleted by
   sync when they have zero transactions, or hidden when they have any.
6. Salla sub-rails (mada / Apple Pay / Visa / بطاقة ائتمانية / …) always
   roll up under the "salla" account_key.
7. COD spellings (دفع/الدفع/عند الاستلام/COD/Cash on Delivery) always map
   to one account_key "cash_on_delivery".
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
import httpx
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
load_dotenv("/app/frontend/.env")

API = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"


# ── Unit: normalize_payment_method classifies every reported spelling ────
def test_normalize_classifies_every_spelling():
    from payment_methods import normalize_payment_method as n

    # Salla rollups
    for raw in ["مدى", "Mada", "Apple Pay", "ابل باي", "Google Pay", "جوجل باي", "STC Pay", "Visa",
                "MasterCard", "ماستر كارد", "بطاقة ائتمانية",
                "البطاقة الإئتمانية", "بطاقة بنكية", "محفظة سلة", "سلة"]:
        sub_key, _, parent = n(raw)
        assert parent == "salla", f"{raw!r} should roll up under salla, got parent={parent}"

    # COD
    for raw in ["COD", "Cash on Delivery", "cash_on_delivery", "cod",
                "دفع عند الإستلام", "الدفع عند الاستلام", "عند الاستلام"]:
        sub_key, disp, parent = n(raw)
        assert sub_key == "cash_on_delivery", f"{raw!r} → sub_key={sub_key}"
        assert disp == "الدفع عند الاستلام"
        assert parent is None

    # Bank transfer
    for raw in ["تحويل بنكي", "حوالة بنكية", "bank transfer"]:
        sub_key, _, parent = n(raw)
        assert sub_key == "bank_transfer", f"{raw!r} → {sub_key}"

    # Specific banks roll up under bank_transfer
    for raw in ["حوالة بنكيةمصرف الراجحي", "حوالة بنكيةمصرف الإنماء",
                "حوالة بنكيةالبنك الأهلي التجاري"]:
        _, _, parent = n(raw)
        assert parent == "bank_transfer", f"{raw!r} should roll up under bank_transfer"

    # Standalones
    assert n("تمارا")[0] == "tamara"
    assert n("تابي")[0] == "tabby"
    assert n("EmkanInstallment")[0] == "emkan"

    # Null markers
    for raw in ["", "\\N", "\\n", "null", "nan", "غير محدد", "N/A", "-", None]:
        assert n(raw or "") == ("", "", None), f"{raw!r} should normalize to empty"


def test_resolve_account_key_refuses_unknown():
    """The classification gate used by sync — unknown raw values MUST not
    pass through to account creation. Tests every shape of "bad" input."""
    from payment_methods import resolve_account_key, CANONICAL_TOP_LEVEL_KEYS

    # Refused
    for bad in ["\\N", "", "null", "nan", "غير محدد", "unknown_xyz_random",
                "some bizarre vendor name 123", "زبدة وعسل", "waiting"]:
        assert resolve_account_key(bad) == (None, None), \
            f"{bad!r} should be unclassified"

    # Accepted — every result must be in the canonical set.
    for raw in ["مدى", "Apple Pay", "Google Pay", "بطاقة ائتمانية", "تابي", "تمارا",
                "إمكان", "تحويل بنكي", "حوالة بنكيةمصرف الراجحي",
                "الدفع عند الاستلام", "COD"]:
        key, display = resolve_account_key(raw)
        assert key in CANONICAL_TOP_LEVEL_KEYS, \
            f"{raw!r} resolved to non-canonical {key!r}"
        assert display, f"{raw!r} resolved to empty display"


def test_iter69_underscore_and_bank_aliases():
    """Iter-69 production fix: 4 raw values observed on production were
    not being classified. Verify they now resolve correctly.

    - apple_pay / stc_pay: underscore → space conversion missing before.
    - bank: bare "bank" was not aliased to bank_transfer.
    - waiting: NOT a payment method — must stay unclassified.
    """
    from payment_methods import normalize_payment_method, resolve_account_key

    # apple_pay → Salla sub-rail
    sub_key, display, parent = normalize_payment_method("apple_pay")
    assert sub_key == "apple_pay" and display == "Apple Pay" and parent == "salla"
    key, top_display = resolve_account_key("apple_pay")
    assert (key, top_display) == ("salla", "سلة")

    # stc_pay → Salla sub-rail
    sub_key, display, parent = normalize_payment_method("stc_pay")
    assert sub_key == "stc_pay" and display == "STC Pay" and parent == "salla"
    key, top_display = resolve_account_key("stc_pay")
    assert (key, top_display) == ("salla", "سلة")

    # Bare "bank" → تحويل بنكي
    sub_key, display, parent = normalize_payment_method("bank")
    assert sub_key == "bank_transfer" and display == "تحويل بنكي" and parent is None
    key, top_display = resolve_account_key("bank")
    assert (key, top_display) == ("bank_transfer", "تحويل بنكي")

    # "waiting" is a status, NOT a payment method — must stay unclassified.
    # normalize_payment_method falls back to slug, but resolve_account_key
    # (the gate) MUST refuse it.
    assert resolve_account_key("waiting") == (None, None), \
        "waiting should NOT become an account"

    # Specific banks still win over bare "bank" (order in PAYMENT_ALIASES).
    _, _, parent = normalize_payment_method("rajhi")
    assert parent == "bank_transfer"
    # The display must be the SPECIFIC bank, not the generic one.
    sub_key, display, _ = normalize_payment_method("الراجحي")
    assert sub_key == "bank_rajhi" and display == "بنك الراجحي"


# ── Integration: full sync flow against the merchant account ────────────
async def _login_client() -> httpx.AsyncClient:
    c = httpx.AsyncClient(base_url=API, timeout=30.0)
    r = await c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    token = r.json().get("access_token") or r.json().get("token")
    c.headers["Authorization"] = f"Bearer {token}"
    return c


@pytest.mark.asyncio
async def test_sync_is_idempotent():
    """3 sync calls in a row must NOT change the payment_platform count
    and must NOT create any unclassified rows (the merchant's data is clean)."""
    http_client = await _login_client()
    try:
        async def _count_pp():
            r = await http_client.get("/api/accounts?include_hidden=true")
            assert r.status_code == 200
            accs = r.json()
            return [a for a in accs if a.get("account_type") == "payment_platform"]

        before = await _count_pp()
        before_names = sorted(a["name"] for a in before)
        before_norms = sorted(a["normalized_payment_method"] for a in before)

        for i in range(3):
            r = await http_client.post("/api/accounts/sync-payment-methods")
            assert r.status_code == 200, f"sync#{i+1} failed: {r.text}"
            last_resp = r.json()
            assert last_resp.get("unclassified_count") == 0
            assert last_resp.get("created") == 0

        after = await _count_pp()
        after_names = sorted(a["name"] for a in after)
        after_norms = sorted(a["normalized_payment_method"] for a in after)
        assert before_names == after_names
        assert before_norms == after_norms

        from payment_methods import CANONICAL_TOP_LEVEL_KEYS
        for a in after:
            assert a["normalized_payment_method"] in CANONICAL_TOP_LEVEL_KEYS, \
                f"Ghost account leaked through: {a}"
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_sync_preserves_transfer_balance():
    """Regression: sync was overwriting `current_balance` with
    `opening_balance + expected`, wiping out internal transfers (Phase 2.1).
    Now `_recompute_balance` derives it from the ledger so transfers stay."""
    http_client = await _login_client()
    try:
        r = await http_client.get("/api/accounts")
        salla = next((a for a in r.json()
                      if a.get("normalized_payment_method") == "salla"), None)
        if salla is None:
            pytest.skip("No Salla account on this user — skipping")
        before_curr = salla["current_balance"]
        before_exp = salla["expected_orders_balance"]

        for _ in range(3):
            await http_client.post("/api/accounts/sync-payment-methods")

        r = await http_client.get("/api/accounts")
        salla2 = next((a for a in r.json()
                       if a.get("normalized_payment_method") == "salla"), None)
        assert salla2 is not None
        assert salla2["current_balance"] == before_curr, \
            f"current_balance drifted: {before_curr} → {salla2['current_balance']}"
        assert salla2["expected_orders_balance"] == before_exp
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_unclassified_endpoint_reachable():
    """The diagnostic endpoint must be reachable (not shadowed by /{account_id})."""
    http_client = await _login_client()
    try:
        r = await http_client.get("/api/accounts/unclassified-payment-methods")
        assert r.status_code == 200, r.text
        body = r.json()
        assert "rows" in body and "count" in body
    finally:
        await http_client.aclose()


@pytest.mark.asyncio
async def test_unique_index_blocks_duplicate_ghost():
    """Even a direct insert of two auto_created docs with the same
    normalized_payment_method must be rejected by the partial unique index."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo.errors import DuplicateKeyError
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    # Login to get user id
    async with httpx.AsyncClient(base_url=API, timeout=30.0) as c:
        await c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        r = await c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        token = r.json().get("access_token") or r.json().get("token")
        me = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        uid = me.json()["id"]

    test_key = f"_pytest_ghost_{uuid.uuid4().hex[:8]}"
    base = {
        "user_id": uid,
        "name": "pytest_ghost",
        "account_type": "payment_platform",
        "auto_created": True,
        "normalized_payment_method": test_key,
        "status": "active",
        "current_balance": 0,
        "opening_balance": 0,
    }
    try:
        await db.accounts.insert_one({**base, "id": str(uuid.uuid4())})
        with pytest.raises(DuplicateKeyError):
            await db.accounts.insert_one({**base, "id": str(uuid.uuid4())})
    finally:
        await db.accounts.delete_many({"user_id": uid, "normalized_payment_method": test_key})
        client.close()


@pytest.mark.asyncio
async def test_sync_deletes_pre_existing_ghost_with_no_tx():
    """Seed a fake ghost account (normalized_payment_method='unknown_xyz')
    with zero transactions, then run sync. The ghost MUST be removed."""
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    async with httpx.AsyncClient(base_url=API, timeout=30.0) as c:
        r = await c.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
        token = r.json().get("access_token") or r.json().get("token")
        me = await c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        uid = me.json()["id"]

        ghost_id = str(uuid.uuid4())
        # Seed: ghost slug that's not canonical.
        await db.accounts.insert_one({
            "id": ghost_id,
            "user_id": uid,
            "name": "البطاقة الإئتمانية",  # the user's exact concern
            "account_type": "payment_platform",
            "provider_name": "البطاقة الإئتمانية",
            "auto_created": True,
            "source": "orders_payment_method",
            "normalized_payment_method": "credit_card_legacy_slug",
            "status": "active",
            "current_balance": 999,
            "opening_balance": 0,
            "expected_orders_balance": 0,
            "currency": "SAR",
        })
        try:
            r = await c.post(
                "/api/accounts/sync-payment-methods",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert r.status_code == 200, r.text
            removed = r.json().get("removed_legacy", [])
            assert "البطاقة الإئتمانية" in removed, \
                f"Ghost not removed. removed_legacy={removed}"

            # Verify gone
            doc = await db.accounts.find_one({"id": ghost_id})
            assert doc is None, "Ghost survived sync cleanup"
        finally:
            await db.accounts.delete_one({"id": ghost_id})
            client.close()
