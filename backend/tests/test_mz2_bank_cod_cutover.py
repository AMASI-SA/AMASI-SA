"""Isolated order-created cutoff checks at bank/COD financial entry points.

Storage and journal append are in-memory boundaries. The real preparation,
approval and conversion functions run without a Mongo or provider connection.
"""
import copy
from decimal import Decimal
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import accounting_bank_transfer_receipts as bank
import accounting_shipping_p02 as shipping


CUTOVER = "2020-01-02T00:00:00+03:00"
ACTOR = {"id": "owner", "name": "Synthetic owner"}
INVALID_DATES = (
    (None, "order_creation_timestamp_required"),
    ("", "order_creation_timestamp_required"),
    ("not-a-date", "order_creation_timestamp_invalid"),
    ("2020-01-01 23:59:59", "pre_cutover_order"),
)
VALID_DATES = ("2020-01-02 00:00:00", "2020-01-01T21:00:00Z", "2020-01-02 01:00:00")


def matches(row, query):
    for key, wanted in query.items():
        if key == "$or":
            if not any(matches(row, option) for option in wanted):
                return False
            continue
        value = row.get(key)
        if isinstance(wanted, dict):
            for operator, operand in wanted.items():
                if operator == "$ne" and value == operand:
                    return False
                if operator == "$in" and value not in operand:
                    return False
                if operator == "$nin" and value in operand:
                    return False
        elif value != wanted:
            return False
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *args):
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, count):
        return copy.deepcopy(self.rows[:count])


class Collection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def find(self, query, *args):
        return Cursor([row for row in self.db.rows.get(self.name, []) if matches(row, query)])

    async def find_one(self, query, *args):
        rows = await self.find(query).to_list(1)
        return rows[0] if rows else None

    async def insert_one(self, row):
        self.db.writes.append((self.name, "insert"))
        self.db.rows.setdefault(self.name, []).append(copy.deepcopy(row))

    async def update_one(self, query, changes, **kwargs):
        self.db.writes.append((self.name, "update"))
        for row in self.db.rows.get(self.name, []):
            if matches(row, query):
                row.update(copy.deepcopy(changes.get("$set", {})))
                return SimpleNamespace(modified_count=1, matched_count=1)
        return SimpleNamespace(modified_count=0, matched_count=0)


class Database:
    def __init__(self, rows):
        self.rows = copy.deepcopy(rows)
        self.writes = []

    def __getitem__(self, name):
        return Collection(self, name)

    def __getattr__(self, name):
        return self[name]


async def owner_transaction(db, owner, callback):
    return await callback(db)


async def append_journal(db, *, collection="general_ledger", **kwargs):
    group_id = "group-" + str(len(db.rows.get(collection, [])))
    for entry in kwargs["entries"]:
        await db[collection].insert_one({
            **entry, "user_id": kwargs["user_id"], "txn_group_id": group_id,
            "metadata": kwargs["metadata"], "status": "posted",
        })
    return {"txn_group_id": group_id}


async def append_native_journal(db, **kwargs):
    return await append_journal(db, collection="accounting_general_ledger_v2", **kwargs)


async def native_rows(db, owner):
    return copy.deepcopy([row for row in db.rows.get("accounting_general_ledger_v2", [])
                          if row["user_id"] == owner])


async def verified_native_metadata(db, owner, group_id, binding):
    # Unit storage boundary only; actual sealing/replay is covered by the
    # dedicated real-Mongo bank-transfer receipt suite.
    rows = [row for row in await native_rows(db, owner) if row["txn_group_id"] == group_id]
    if not rows or any(any(row["metadata"].get(key) != value for key, value in binding.items()) for row in rows):
        raise AssertionError("Synthetic native journal binding mismatch")
    return rows[0]["metadata"]


def database(created, *, cod=False, converting=False):
    evidence = {
        "id": "evidence", "user_id": "owner", "order_number": "order",
        "order_date_source_text": created, "conflict": False,
        "accounting_provider": "cod" if cod else "bank_transfer",
        "current_net_sar": "115.00", "refunded_sar": "0.00",
        "economic_hash": "order-hash", "bank_selected_from_order": "bank-label",
        "order_status": "تم التوصيل" if converting else "pending",
        "delivery_source_text": "2020-01-03 12:00:00" if converting else "",
        "bank_transfer_receipt_status": "confirmed_waiting_delivery" if converting else "pending_approval",
        "bank_transfer_receipt_review_id": "review", "latest_file_id": "file",
    }
    review = {
        "_id": "review", "id": "review", "user_id": "owner",
        "order_evidence_id": "evidence", "order_economic_hash": "order-hash",
        "status": "confirmed_waiting_delivery" if converting else "pending_approval",
        "selected_bank_from_order": "bank-label", "bank_account_id": "bank",
        "receipt_file_sha256": "synthetic-proof", "transfer_date": "2020-01-03",
        "received_amount": "115.00", "advance_id": "advance",
        "receipt_txn_group_id": "receipt-group",
    }
    return Database({
        "users": [{"id": "owner", "role": "owner"}],
        "general_ledger": [],
        "settings": [{"user_id": "owner", "mezan2_financial_cutover": {
            "operation_id": bank.OPERATION_ID, "status": "active", "cutover_at": CUTOVER,
            "p02_shipping_cod_enabled": True, "p02_shipping_cod_activation_ref": "synthetic",
        }}],
        "mz2_salla_order_evidence": [evidence],
        "mz2_bank_transfer_receipts": [review],
        "mz2_daily_movements": [{
            "_id": "movement", "id": "movement", "user_id": "owner",
            "direction": "in", "bank_account_id": "bank", "status": "unclassified",
            "amount": "115.00", "movement_date": "2020-01-03", "reference": "receipt",
        }],
        "store_drivers": [{"id": "driver", "user_id": "owner", "status": "active"}],
        "store_delivery_collections": [{
            "id": "collection", "user_id": "owner", "assignment_id": "assignment",
            "driver_id": "driver", "order_number": "order", "payment_method": "cash",
            "cod_custody_amount": "115.00", "amount": "115.00",
            "collected_at": "2020-01-03T10:00:00Z", "accounting_status": "pending",
        }],
        "store_delivery_driver_earnings": [{
            "id": "earning", "user_id": "owner", "assignment_id": "assignment",
            "driver_id": "driver", "order_number": "order", "amount": "20.00",
            "accounting_status": "pending",
        }],
        "accounting_general_ledger_v2": ([{
            "user_id": "owner", "txn_group_id": "receipt-group", "status": "posted",
            "entity_type": "liability", "entity_id": "advance", "sub_account": "customer_advance",
            "side": "credit", "amount": "115.00", "metadata": {"bank_transfer_review_id": "review", "bank_transfer_event_kind": "customer_receipt"},
        }] if converting else []),
    })


class BankCodCutoverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for module in (bank, shipping):
            for name, replacement in (
                ("atomic_owner", owner_transaction),

                ("read_mz2_write_balances", AsyncMock()),
                ("read_policy", AsyncMock(return_value={})),
                ("sale_snapshot", lambda *args: {"gross": "115.00", "net": "100.00", "tax": "15.00"}),
            ):
                self.enterContext(patch.object(module, name, replacement))
        self.enterContext(patch.object(bank, "post_customer_journal", append_native_journal))
        self.enterContext(patch.object(bank, "native_rows", native_rows))
        self.enterContext(patch.object(bank, "verified_customer_journal", verified_native_metadata))
        self.enterContext(patch.object(shipping, "post_txn_group", append_journal))
        self.enterContext(patch.object(bank, "_resolve_order_bank", AsyncMock(return_value={
            "state": "resolved", "bank_account_id": "bank",
        })))

    async def test_bank_approval_rejects_order_dates_before_any_financial_change(self):
        for created, code in INVALID_DATES:
            with self.subTest(created=created):
                db = database(created)
                before = copy.deepcopy(db.rows)
                with self.assertRaisesRegex(bank.BankTransferError, "^" + code + "$"):
                    await bank.approve_receipt(db, owner="owner", actor=ACTOR,
                        review_id="review", movement_id="movement")
                self.assertEqual(db.rows, before)
                self.assertEqual(db.writes, [])

    async def test_bank_approval_accepts_exact_and_postcut_dates_without_changing_advance_semantics(self):
        for created in VALID_DATES:
            with self.subTest(created=created):
                db = database(created)
                result = await bank.approve_receipt(db, owner="owner", actor=ACTOR,
                    review_id="review", movement_id="movement")
                self.assertEqual(result["status"], "confirmed_waiting_delivery")
                self.assertIsNone(result["sale_txn_group_id"])
                self.assertEqual({row["entry_type"] for row in db.rows["accounting_general_ledger_v2"]},
                    {"bank_transfer_advance"})
                self.assertEqual(len(db.rows["accounting_general_ledger_v2"]), 2)
                self.assertEqual({(r["entity_type"], r["entity_id"], r["side"], Decimal(str(r["amount"])))
                    for r in db.rows["accounting_general_ledger_v2"]}, {
                        ("bank", "bank", "debit", Decimal("115")),
                        ("liability", result["advance_id"], "credit", Decimal("115"))})
                self.assertEqual(db.rows["general_ledger"], [])

    async def test_bank_conversion_rejects_order_dates_in_preview_and_execution(self):
        for created, code in INVALID_DATES:
            for dry_run in (True, False):
                with self.subTest(created=created, dry_run=dry_run):
                    db = database(created, converting=True)
                    before = copy.deepcopy(db.rows)
                    result = await bank.convert_confirmed_deliveries(db, owner="owner",
                        actor=ACTOR, dry_run=dry_run)
                    self.assertEqual(result["items"][0]["state"], "blocked")
                    self.assertEqual(result["items"][0]["reason"], code)
                    self.assertEqual(db.rows, before)
                    self.assertEqual(db.writes, [])

    async def test_bank_conversion_accepts_exact_and_postcut_dates(self):
        for created in VALID_DATES:
            with self.subTest(created=created):
                db = database(created, converting=True)
                result = await bank.convert_confirmed_deliveries(db, owner="owner", actor=ACTOR, dry_run=False)
                self.assertEqual(result["posted_count"], 1)
                self.assertEqual(len(db.rows["accounting_general_ledger_v2"]), 4)
                sale = [r for r in db.rows["accounting_general_ledger_v2"] if r["txn_group_id"] != "receipt-group"]
                self.assertEqual({(r["entity_type"], r["side"], Decimal(str(r["amount"]))) for r in sale}, {
                    ("liability", "debit", Decimal("115")), ("revenue", "credit", Decimal("100")),
                    ("tax", "credit", Decimal("15"))})
                self.assertEqual(db.rows["general_ledger"], [])
                self.assertEqual(db.rows["mz2_bank_transfer_receipts"][0]["status"], "recognized")

    async def test_bank_conversion_rechecks_latest_order_date_inside_transaction(self):
        db = database(VALID_DATES[0], converting=True)
        expected = copy.deepcopy(db.rows)
        expected["mz2_salla_order_evidence"][0]["order_date_source_text"] = None

        async def changed_source(scoped, owner, callback):
            scoped.rows["mz2_salla_order_evidence"][0]["order_date_source_text"] = None
            return await callback(scoped)

        with patch.object(bank, "atomic_owner", changed_source):
            result = await bank.convert_confirmed_deliveries(db, owner="owner", actor=ACTOR, dry_run=False)
        self.assertEqual(result["items"][0]["reason"], "order_creation_timestamp_required")
        self.assertEqual(result["posted_count"], 0)
        self.assertEqual(db.rows, expected)
        self.assertEqual(db.writes, [])

    async def test_cod_prepare_and_post_reject_order_dates_without_financial_changes(self):
        for created, code in INVALID_DATES:
            for execute in (False, True):
                with self.subTest(created=created, execute=execute):
                    db = database(created, cod=True)
                    before = copy.deepcopy(db.rows)
                    with self.assertRaisesRegex(shipping.ShippingAccountingError, "^" + code + "$"):
                        if execute:
                            await shipping.post_store_driver_cod(db, owner="owner", actor=ACTOR,
                                assignment_id="assignment")
                        else:
                            await shipping.prepare_store_driver_cod(db, owner="owner", assignment_id="assignment")
                    self.assertEqual(db.rows, before)
                    self.assertEqual(db.writes, [])

    async def test_cod_exact_and_postcut_dates_keep_sale_and_separate_fee(self):
        for created in VALID_DATES:
            with self.subTest(created=created):
                db = database(created, cod=True)
                proposal = await shipping.prepare_store_driver_cod(db, owner="owner", assignment_id="assignment")
                self.assertEqual(proposal["state"], "eligible")
                result = await shipping.post_store_driver_cod(db, owner="owner", actor=ACTOR,
                    assignment_id="assignment")
                self.assertEqual(result["state"], "posted")
                self.assertEqual(len(db.rows["general_ledger"]), 5)
                self.assertNotEqual(result["sale_txn_group_id"], result["fee_txn_group_id"])
                self.assertEqual(db.rows["store_delivery_collections"][0]["mz2_p02_accounting_status"], "posted")

    async def test_cod_post_rechecks_latest_order_date_inside_transaction(self):
        db = database(VALID_DATES[0], cod=True)
        proposal = await shipping.prepare_store_driver_cod(db, owner="owner", assignment_id="assignment")
        self.assertEqual(proposal["state"], "eligible")
        expected = copy.deepcopy(db.rows)
        expected["mz2_salla_order_evidence"][0]["order_date_source_text"] = None

        async def changed_source(scoped, owner, callback):
            scoped.rows["mz2_salla_order_evidence"][0]["order_date_source_text"] = None
            return await callback(scoped)

        with patch.object(shipping, "atomic_owner", changed_source):
            with self.assertRaisesRegex(shipping.ShippingAccountingError, "^order_creation_timestamp_required$"):
                await shipping.post_store_driver_cod(db, owner="owner", actor=ACTOR, assignment_id="assignment")
        self.assertEqual(db.rows, expected)
        self.assertEqual(db.writes, [])


if __name__ == "__main__":
    unittest.main()
