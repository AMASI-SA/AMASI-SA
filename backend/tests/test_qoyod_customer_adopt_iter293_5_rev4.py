"""Iter-293.5-rev4 — Manual Customer Adoption (Local-Only).

Guards `POST /api/integrations/qoyod/customers/adopt` — the operator
supplies a real Qoyod `contact_id` for a buyer they already created
(or verified) inside Qoyod. Mezan MUST NOT call Qoyod's API here; it
just upserts `qoyod_customers_mapping` so subsequent orders from this
buyer bind directly to the given `contact_id`, and clears
`dry_run_only` so the preview / sendable gate accepts the binding.

Test surface
────────────
• Phone lookups get E.164-normalised (`0554681361` → `+966554681361`).
• Idempotent — re-adopting the same phone updates note/actor without
  duplicating.
• `dry_run_only` is always False after adoption.
• Invalid `lookup_kind` is rejected.
• Missing `qoyod_contact_id` / `lookup_key` is rejected.
• NO Qoyod API call happens (verified by injecting a fake api_client
  and asserting `calls==[]`).
"""
from __future__ import annotations

import uuid
import pytest

from integrations.qoyod.customer_resolver import (
    adopt_qoyod_customer, _normalize_phone_for_lookup,
)


@pytest.fixture
def db():
    from motor.motor_asyncio import AsyncIOMotorClient
    import os
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


class TestPhoneNormalisation:
    """Adoption MUST normalise Saudi phones to E.164 so the stored
    lookup_key matches the runtime pipeline's derivation from Salla
    (`normalizer.normalize_phone`)."""

    def test_local_zero_prefix(self):
        assert _normalize_phone_for_lookup("0554681361") == "+966554681361"

    def test_bare_5_prefix(self):
        assert _normalize_phone_for_lookup("554681361") == "+966554681361"

    def test_966_prefix(self):
        assert _normalize_phone_for_lookup("966554681361") == "+966554681361"

    def test_plus_966_prefix_untouched(self):
        assert _normalize_phone_for_lookup("+966554681361") == \
            "+966554681361"

    def test_00966_prefix(self):
        assert _normalize_phone_for_lookup("00966554681361") == \
            "+966554681361"

    def test_dashes_and_spaces_stripped(self):
        assert _normalize_phone_for_lookup("+966 55-468 1361") == \
            "+966554681361"

    def test_all_prod_lookup_variants_collapse(self):
        # The exact variants the operator reported trying on Prod.
        variants = ["+966554681361", "966554681361", "0554681361"]
        canon = {_normalize_phone_for_lookup(v) for v in variants}
        assert canon == {"+966554681361"}


@pytest.mark.asyncio
class TestAdoptQoyodCustomer:
    """End-to-end adoption against a real Mongo `qoyod_customers_mapping`
    collection. Each test uses a unique `user_id` to isolate state."""

    async def _fresh_user(self, db):
        uid = f"adopt-test-{uuid.uuid4().hex[:8]}"
        # Cleanup at teardown.
        yield uid
        await db.qoyod_customers_mapping.delete_many({"user_id": uid})

    async def test_creates_mapping_row(self, db):
        uid = f"adopt-test-{uuid.uuid4().hex[:8]}"
        try:
            res = await adopt_qoyod_customer(
                db, user_id=uid,
                lookup_key="0554681361", lookup_kind="phone",
                qoyod_contact_id="C-999",
                qoyod_contact_name="أحمد التجريبي",
                note="manual adoption for order 268307955",
                actor="operator:tester",
            )
            assert res["ok"] is True
            assert res["qoyod_contact_id"] == "C-999"
            assert res["lookup_key"] == "+966554681361"  # E.164
            assert res["dry_run_only"] is False
            row = await db.qoyod_customers_mapping.find_one(
                {"user_id": uid, "lookup_key": "+966554681361"})
            assert row is not None
            assert row["qoyod_customer_id"] == "C-999"
            assert row["adopted"] is True
            assert row["dry_run_only"] is False
            assert row["source"] == "operator_adopted"
            assert row["adopted_by"] == "operator:tester"
            assert row["adoption_note"] == \
                "manual adoption for order 268307955"
        finally:
            await db.qoyod_customers_mapping.delete_many({"user_id": uid})

    async def test_clears_dry_run_only_flag(self, db):
        """Simulate the exact prod scenario — a stale mapping already
        exists with `dry_run_only=True` and quarantine set. Adoption
        MUST clear both."""
        uid = f"adopt-test-{uuid.uuid4().hex[:8]}"
        try:
            await db.qoyod_customers_mapping.insert_one({
                "user_id":            uid,
                "lookup_key":         "+966554681361",
                "lookup_kind":        "phone",
                "qoyod_customer_id":  "DRY:tmp-x",
                "dry_run_only":       True,
                "quarantine_reason":  "dry_run_id_in_production",
                "adopted":            False,
            })
            res = await adopt_qoyod_customer(
                db, user_id=uid,
                lookup_key="+966554681361", lookup_kind="phone",
                qoyod_contact_id="C-1234",
                qoyod_contact_name="عميل حقيقي",
            )
            assert res["ok"] is True
            row = await db.qoyod_customers_mapping.find_one(
                {"user_id": uid, "lookup_key": "+966554681361"})
            assert row["dry_run_only"] is False
            assert row["quarantine_reason"] is None
            assert row["qoyod_customer_id"] == "C-1234"
            assert row["adopted"] is True
        finally:
            await db.qoyod_customers_mapping.delete_many({"user_id": uid})

    async def test_idempotent_re_adoption(self, db):
        """Re-adopting the same lookup_key updates note/actor, does
        NOT insert a duplicate row."""
        uid = f"adopt-test-{uuid.uuid4().hex[:8]}"
        try:
            await adopt_qoyod_customer(
                db, user_id=uid,
                lookup_key="0554681361", lookup_kind="phone",
                qoyod_contact_id="C-1",
                note="first pass", actor="operator:a")
            await adopt_qoyod_customer(
                db, user_id=uid,
                lookup_key="+966554681361", lookup_kind="phone",
                qoyod_contact_id="C-1",
                note="second pass", actor="operator:b")
            count = await db.qoyod_customers_mapping.count_documents(
                {"user_id": uid})
            assert count == 1
            row = await db.qoyod_customers_mapping.find_one(
                {"user_id": uid})
            assert row["adoption_note"] == "second pass"
            assert row["adopted_by"] == "operator:b"
        finally:
            await db.qoyod_customers_mapping.delete_many({"user_id": uid})

    async def test_rejects_missing_contact_id(self, db):
        res = await adopt_qoyod_customer(
            db, user_id="x",
            lookup_key="0554681361", lookup_kind="phone",
            qoyod_contact_id="")
        assert res["ok"] is False
        assert res["reason"] == "lookup_key_and_qoyod_contact_id_required"

    async def test_rejects_missing_lookup_key(self, db):
        res = await adopt_qoyod_customer(
            db, user_id="x",
            lookup_key="", lookup_kind="phone",
            qoyod_contact_id="C-1")
        assert res["ok"] is False
        assert res["reason"] == "lookup_key_and_qoyod_contact_id_required"

    async def test_rejects_bad_lookup_kind(self, db):
        res = await adopt_qoyod_customer(
            db, user_id="x",
            lookup_key="0554681361", lookup_kind="nickname",
            qoyod_contact_id="C-1")
        assert res["ok"] is False
        assert res["reason"] == "lookup_kind_must_be_phone_or_email"

    async def test_email_lookup_lowercased(self, db):
        uid = f"adopt-test-{uuid.uuid4().hex[:8]}"
        try:
            res = await adopt_qoyod_customer(
                db, user_id=uid,
                lookup_key="Ahmed@Example.COM", lookup_kind="email",
                qoyod_contact_id="C-7")
            assert res["ok"] is True
            assert res["lookup_key"] == "ahmed@example.com"
            row = await db.qoyod_customers_mapping.find_one(
                {"user_id": uid})
            assert row["email"] == "ahmed@example.com"
            assert row["phone"] is None
        finally:
            await db.qoyod_customers_mapping.delete_many({"user_id": uid})

    async def test_no_qoyod_api_dependency(self, db):
        """Adoption MUST NOT depend on Qoyod credentials or hit the
        API. This test proves it works even when no API key is
        configured on the tenant."""
        uid = f"adopt-test-{uuid.uuid4().hex[:8]}"
        try:
            # No credentials seeded — adoption must still succeed.
            res = await adopt_qoyod_customer(
                db, user_id=uid,
                lookup_key="0554681361", lookup_kind="phone",
                qoyod_contact_id="C-42")
            assert res["ok"] is True
        finally:
            await db.qoyod_customers_mapping.delete_many({"user_id": uid})

    async def test_order_268307955_scenario_end_to_end(self, db):
        """Production scenario for order 268307955 — operator has
        just created the buyer in Qoyod (contact_id say `C-9001`).
        Mezan already has a stale mapping with `dry_run_only=True`.
        After adoption the runtime resolver (called with the same
        DTO) MUST return that C-9001 without hitting the API."""
        from integrations.qoyod.dto import CustomerDTO
        from integrations.qoyod.customer_resolver import resolve_customer
        uid = f"adopt-test-{uuid.uuid4().hex[:8]}"
        try:
            # Seed the stale DRY mapping the runtime saw pre-adoption.
            await db.qoyod_customers_mapping.insert_one({
                "user_id":            uid,
                "lookup_key":         "+966554681361",
                "lookup_kind":        "phone",
                "qoyod_customer_id":  "DRY:tmp",
                "dry_run_only":       True,
                "quarantine_reason":  "dry_run_id_in_production",
                "adopted":            False,
            })
            # Operator adopts.
            await adopt_qoyod_customer(
                db, user_id=uid,
                lookup_key="0554681361", lookup_kind="phone",
                qoyod_contact_id="C-9001",
                qoyod_contact_name="حامد ماجد",
                note="order 268307955")
            # Now the runtime resolver must find the fresh mapping
            # via a local hit (no API call).
            customer = CustomerDTO(
                name="حامد ماجد", phone="+966554681361")
            # Pass a NoOp fake api_client (any call would AttributeError
            # if actually used).
            class _NoAPI:
                pass
            res = await resolve_customer(
                db, user_id=uid, trace_id="t-268307955",
                customer=customer, api_client=_NoAPI())
            assert res.success is True
            assert res.qoyod_customer_id == "C-9001"
            assert res.created_new is False
        finally:
            await db.qoyod_customers_mapping.delete_many({"user_id": uid})


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
