"""Real Mongo acceptance: MZ2 reports cannot absorb legacy balances or scope."""
import unittest
from uuid import uuid4
from fastapi import APIRouter
import test_mz2_daily_refunds as daily
from mz2_report_fixtures import provision_report_opening
from accounting_module_contract import OPERATION_ID
from accounting_mz2_reports import (
    read_mz2_ledger,
    mz2_financial_position,
    mz2_income_statement,
    mz2_trial_balance,
    install_mz2_report_routes,
)

class ReportIsolationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = daily.DailyRefundTests.asyncSetUp
    asyncTearDown = daily.DailyRefundTests.asyncTearDown
    configure = daily.DailyRefundTests.configure
    source = daily.DailyRefundTests.source
    payload = daily.DailyRefundTests.payload
    preview_and_post = daily.DailyRefundTests.preview_and_post
    post = daily.DailyRefundTests.post
    setup_sale = daily.DailyRefundTests.setup_sale
    bank = daily.DailyRefundTests.bank
    case = daily.DailyRefundTests.case
    confirm = daily.DailyRefundTests.confirm

    async def legacy_sentinels(self):
        await self.db.accounts.update_one({"user_id": "owner", "id": "bank"}, {"$set": {"account_type": "bank", "current_balance": 987654321}}, upsert=True)
        for at in ("2019-12-31T12:00:00Z", "2020-09-04T12:00:00Z"):
            await self.db.general_ledger.insert_one({"id": uuid4().hex, "user_id": "owner", "status": "posted",
                "txn_group_id": uuid4().hex, "entry_type": "bank_transfer", "entity_type": "bank", "entity_id": "bank",
                "sub_account": "main", "side": "debit", "amount": 1234567, "metadata": {"accounting_at": at},
                "created_at": at, "posted_at": at})

    async def test_missing_opening_never_substitutes_legacy_or_zero_balances(self):
        await self.legacy_sentinels()
        for as_of in (None, "2020-08-31"):
            report = await mz2_financial_position(self.db, owner="owner", as_of=as_of)
            self.assertIn(report["status"], {"not_ready", "needs_opening_balance"})
            income = await mz2_income_statement(self.db, owner="owner", as_of=as_of)
            self.assertIn(income["status"], {"not_ready", "needs_opening_balance"})
            self.assertIsNone(income["revenues"])
            self.assertIsNone(income["expenses"])
            self.assertIsNone(income["totals"])
            for key in ("assets", "liabilities", "totals"):
                self.assertIsNone(report[key])
            self.assertNotEqual((await read_mz2_ledger(self.db, owner="owner", as_of=as_of))["status"], "available")
        await provision_report_opening(self.db)
        await self.db.general_ledger.delete_many({"entry_type": "opening_balance"})
        self.assertEqual((await mz2_financial_position(self.db, owner="owner"))["status"], "needs_opening_balance")

    async def test_approved_opening_and_sale_exclude_legacy_current_and_historical(self):
        opening = await provision_report_opening(self.db)
        await self.setup_sale(gross="115")
        before = await mz2_financial_position(self.db, owner="owner", as_of="2020-08-31")
        self.assertEqual(before["status"], "available")
        self.assertEqual(before["assets"]["banks"], 1000)
        self.assertEqual(before["assets"]["payment_platforms_remaining"], 115)
        self.assertEqual(before["liabilities"]["sales_vat_payable"], 15)
        trial_before = await mz2_trial_balance(self.db, owner="owner", as_of="2020-08-31")
        await self.legacy_sentinels()
        # Foreign tenant reuses our group ID: the journal must remain scoped.
        await self.db.general_ledger.insert_one({"id": uuid4().hex, "user_id": "other", "txn_group_id": opening,
            "status": "posted", "entry_type": "opening_balance", "entity_type": "bank", "entity_id": "bank",
            "sub_account": "main", "side": "debit", "amount": 987654,
            "metadata": {"operation_id": OPERATION_ID, "accounting_at": "2020-01-01T00:00:00Z"}})
        after = await mz2_financial_position(self.db, owner="owner", as_of="2020-08-31")
        self.assertEqual(after, before)
        current = await mz2_financial_position(self.db, owner="owner")
        self.assertEqual(current["assets"], before["assets"])
        journal = await read_mz2_ledger(self.db, owner="owner")
        self.assertEqual(len(journal["items"]), 5)
        self.assertTrue(all(r["metadata"]["operation_id"] == OPERATION_ID for r in journal["items"]))
        self.assertEqual(journal["opening_balance_txn_group_id"], opening)
        self.assertTrue(all(r["user_id"] == "owner" for r in journal["items"]))
        trial_after = await mz2_trial_balance(self.db, owner="owner", as_of="2020-08-31")
        self.assertEqual(trial_after, trial_before)
        self.assertEqual(sum(r["debits"] for r in trial_after["items"]), 1115)
        self.assertEqual(sum(r["credits"] for r in trial_after["items"]), 1115)
        self.assertEqual({(r["entity_type"], r["entity_id"]): r["net"] for r in trial_after["items"]},
            {("bank", "bank"): 1000, ("equity", "SYN"): -1000, ("payment_gateway", "tamara"): 115,
             ("revenue", "bnpl_sales"): -100, ("tax", "sales_vat_payable"): -15})

    async def test_income_statement_signed_revenue_cogs_reversal_and_operating_expenses(self):
        await provision_report_opening(self.db)
        await self.setup_sale(gross="115")

        async def group(entry_type, at, metadata, entries):
            group_id = uuid4().hex
            docs = []
            for entity_type, entity_id, sub_account, side, amount in entries:
                docs.append({
                    "id": uuid4().hex,
                    "user_id": "owner",
                    "status": "posted",
                    "txn_group_id": group_id,
                    "entry_type": entry_type,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "sub_account": sub_account,
                    "side": side,
                    "amount": amount,
                    "metadata": {
                        "operation_id": OPERATION_ID,
                        "accounting_at": at,
                        **metadata,
                    },
                    "posted_at": at,
                    "created_at": at,
                })
            await self.db.general_ledger.insert_many(docs)

        await group(
            "inventory_cogs",
            "2020-01-03T12:00:00Z",
            {
                "source": "accounting_inventory_p03",
                "p03_kind": "inventory_cogs",
                "p03_event_id": "cogs-1",
                "inventory_consumption_event_id": "consume-1",
                "sale_recognition_txn_group_id": "sale-1",
                "order_reference_id": "ORDER-1",
            },
            [
                ("expense", "cogs", None, "debit", 60),
                ("asset", "inventory", "inventory", "credit", 60),
            ],
        )
        await group(
            "customer_refund_due",
            "2020-01-04T12:00:00Z",
            {"refund_accounting_version": 2},
            [
                ("revenue", "bnpl_sales", None, "debit", 20),
                ("tax", "sales_vat_payable", None, "debit", 3),
                ("liability", "refund-1", "customer_refund_payable", "credit", 23),
            ],
        )
        await group(
            "inventory_cogs_reversal",
            "2020-01-05T12:00:00Z",
            {
                "source": "accounting_inventory_p03",
                "p03_kind": "inventory_cogs_reversal",
                "p03_event_id": "cogs-reversal-1",
                "return_restock_id": "restock-1",
                "return_case_id": "return-1",
                "inventory_receipt_id": "return-receipt-1",
                "order_reference_id": "ORDER-1",
            },
            [
                ("asset", "inventory", "inventory", "debit", 20),
                ("expense", "cogs", None, "credit", 20),
            ],
        )
        await group(
            "expense_record",
            "2020-01-06T12:00:00Z",
            {
                "source": "accounting_daily_outgoing_p01",
                "outgoing_event_id": "expense-1",
                "daily_movement_id": "movement-1",
            },
            [
                ("expense", "rent", None, "debit", 10),
                ("bank", "bank", "main", "credit", 10),
            ],
        )
        await group(
            "shipping_fee_accrual",
            "2020-01-07T12:00:00Z",
            {
                "source": "accounting_shipping_p02",
                "shipping_event_id": "shipping-1",
            },
            [
                ("expense", "shipping", None, "debit", 17.25),
                ("courier", "smsa", "payable", "credit", 17.25),
            ],
        )

        report = await mz2_income_statement(
            self.db,
            owner="owner",
            as_of="2020-08-31",
        )
        self.assertEqual(report["status"], "available", report)
        self.assertEqual(
            report["revenues"]["bnpl_sales"],
            {"debits": 20.0, "credits": 100.0, "net": 80.0},
        )
        self.assertEqual(
            report["expenses"]["cogs"],
            {"debits": 60.0, "credits": 20.0, "net": 40.0},
        )
        self.assertEqual(report["expenses"]["rent"]["net"], 10.0)
        self.assertEqual(report["expenses"]["shipping"]["net"], 17.25)
        self.assertEqual(report["totals"], {
            "net_revenue": 80.0,
            "cogs": 40.0,
            "gross_profit": 40.0,
            "operating_expenses": 27.25,
            "total_expenses": 67.25,
            "net_profit": 12.75,
        })

        await self.legacy_sentinels()
        self.assertEqual(
            await mz2_income_statement(
                self.db,
                owner="owner",
                as_of="2020-08-31",
            ),
            report,
        )

    async def test_actual_refund_month_end_partial_and_final_payment_with_legacy_sentinels(self):
        await self.bank()
        bank_leg = await self.db.general_ledger.find_one({"entity_type": "bank"})
        await provision_report_opening(self.db, existing_group_id=bank_leg["txn_group_id"])
        key = await self.setup_sale(gross="115")
        row = await self.case(key, "SYN-ISOLATED-AUG", "115")
        await self.confirm(row, "2020-08-31T23:30:00+03:00")
        await self.legacy_sentinels()
        async def check(day, liability, bank):
            report = await mz2_financial_position(self.db, owner="owner", as_of=day)
            self.assertEqual(report["status"], "available", report)
            self.assertEqual(report["liabilities"]["customer_refund_payable"], liability)
            self.assertEqual(report["assets"]["banks"], bank)
        await check("2020-08-31", 115, 1000)
        for day, amount, remaining, bank in (("2020-09-02", "40", 75, 960), ("2020-09-05", "75", 0, 885)):
            payment = await self.post("/bank-payments", dict(original_key=key, case_reference=row["case_reference"], amount=amount,
                paid_at=day+"T10:00:00+03:00", bank_account_id="bank", bank_reference="SYN-ISOLATED-"+day, execution_channel="bank"))
            await self.post("/bank-payments/"+payment["id"]+"/approve")
            await check(day, remaining, bank)
            await check("2020-08-31", 115, 1000)
        await check("2020-09-02", 75, 960)

    async def test_tagged_invalid_source_or_missing_date_blocks_partial_report(self):
        await provision_report_opening(self.db)
        for entry_type, metadata in (("unknown_mz2", {"accounting_at": "2020-01-02T12:00:00Z"}),
                                     ("customer_refund_due", {"refund_accounting_version": 2})):
            row_id = uuid4().hex
            await self.db.general_ledger.insert_one({"id": row_id, "user_id": "owner", "status": "posted", "txn_group_id": row_id,
                "entity_type": "liability", "entity_id": "SYN", "sub_account": "customer_refund_payable", "side": "credit",
                "amount": 115, "entry_type": entry_type, "metadata": {"operation_id": OPERATION_ID, **metadata},
                "created_at": "2020-01-02T12:00:00Z"})
            report = await mz2_financial_position(self.db, owner="owner")
            self.assertEqual(report["status"], "not_ready")
            self.assertIsNone(report["totals"])
            await self.db.general_ledger.delete_one({"id": row_id})

    async def test_route_employee_scope_malicious_query_revocation_and_disabled(self):
        await provision_report_opening(self.db)
        router = APIRouter()
        async def actor(): return {"id": self.actor, "role": "owner"}
        install_mz2_report_routes(router, self.db, actor)
        self.app.include_router(router)
        await self.db.users.update_one({"id": "viewer"}, {"$set": {"accounting_permissions": ["accounting.journals_reports.view"]}})
        self.actor = "viewer"
        base = "/accounting-module/reports/financial-position"
        good = await self.client.get(base, params={"as_of": "2020-08-31"})
        self.assertEqual(good.status_code, 200, good.text)
        self.assertEqual(good.json()["assets"]["banks"], 1000)
        attack = await self.client.get(base, params={"owner": "other", "user_id": "other", "operation_id": "legacy"})
        self.assertIn(attack.status_code, (200, 422), attack.text)
        if attack.status_code == 200:
            self.assertEqual(attack.json()["assets"]["banks"], 1000)
            self.assertEqual(attack.json()["operation_id"], OPERATION_ID)
        for update in ({"accounting_permissions": []}, {"accounting_permissions": ["accounting.journals_reports.view"], "disabled": True}):
            await self.db.users.update_one({"id": "viewer"}, {"$set": update})
            for path in ("financial-position", "trial-balance", "journals"):
                denied = await self.client.get("/accounting-module/reports/"+path)
                self.assertEqual(denied.status_code, 403, denied.text)

    async def test_unapproved_or_corrupt_opening_never_presents_balances(self):
        opening = await provision_report_opening(self.db)
        for field, value in (("status", "pending"), ("opening_balance_preview_balanced", False),
                             ("opening_balance_approved_by", ""), ("evidence_sheet_ref", "")):
            await provision_report_opening(self.db, existing_group_id=opening)
            await self.db.settings.update_one({"user_id": "owner"}, {"$set": {"mezan2_financial_cutover."+field: value}})
            report = await mz2_financial_position(self.db, owner="owner")
            self.assertNotEqual(report["status"], "available", field)
            self.assertIsNone(report["totals"], field)
        await provision_report_opening(self.db, existing_group_id=opening)
        await self.db.general_ledger.update_one({"txn_group_id": opening, "side": "credit"}, {"$set": {"amount": 999}})
        self.assertNotEqual((await mz2_financial_position(self.db, owner="owner"))["status"], "available")
        await self.db.general_ledger.update_one({"txn_group_id": opening, "side": "credit"}, {"$set": {"amount": 1000,
            "metadata.accounting_at": "2020-01-02T00:00:00Z"}})
        self.assertNotEqual((await mz2_financial_position(self.db, owner="owner"))["status"], "available")

    async def test_known_precutover_and_other_operation_never_leak(self):
        await provision_report_opening(self.db)
        baseline = await mz2_financial_position(self.db, owner="owner", as_of="2020-08-31")
        for operation, at in ((OPERATION_ID, "2019-12-31T12:00:00Z"), ("OTHER-OPERATION", "2020-01-02T12:00:00Z")):
            group = uuid4().hex
            await self.db.general_ledger.insert_many([
                {"id": uuid4().hex, "user_id": "owner", "status": "posted", "txn_group_id": group,
                 "entry_type": "bnpl_sale", "entity_type": entity, "entity_id": identifier, "sub_account": sub,
                 "side": side, "amount": 555555, "metadata": {"operation_id": operation, "recognition_event_key": group,
                    "recognized_at": at}, "created_at": "2026-09-20T12:00:00Z"}
                for entity, identifier, sub, side in (("payment_gateway", "tamara", "receivable", "debit"),
                                                      ("revenue", "bnpl_sales", None, "credit"))])
        self.assertEqual(await mz2_financial_position(self.db, owner="owner", as_of="2020-08-31"), baseline)
        self.assertEqual(len((await read_mz2_ledger(self.db, owner="owner"))["items"]), 2)

    async def test_existing_bank_without_approved_opening_is_not_an_approved_zero(self):
        await provision_report_opening(self.db, bank="approved-bank")
        await self.db.accounts.insert_one({"user_id": "owner", "id": "unapproved-bank", "account_type": "bank", "current_balance": 777777})
        group = "SYN-LATER-BANK-ACTIVITY"
        await self.db.general_ledger.insert_many([
            {"id": uuid4().hex, "user_id": "owner", "txn_group_id": group, "status": "posted", "entry_type": "settlement",
             "entity_type": entity, "entity_id": identifier, "sub_account": sub, "side": side, "amount": 10,
             "metadata": {"operation_id": OPERATION_ID, "source": "accounting_settlement_p01", "accounting_at": "2020-01-02T12:00:00Z"}}
            for entity, identifier, sub, side in (("bank", "unapproved-bank", "main", "debit"),
                                                ("payment_gateway", "tamara", "receivable", "credit"))])
        for as_of in (None, "2020-08-31"):
            report = await mz2_financial_position(self.db, owner="owner", as_of=as_of)
            self.assertEqual(report["status"], "needs_opening_balance")
            self.assertIn("bank/unapproved-bank/main", report["missing_accounts"])
            self.assertIsNone(report["assets"])
            self.assertIsNone(report["totals"])

    async def test_legacy_control_reproduces_current_balance_leak_before_cutover(self):
        from financial_position_ssot import compute_financial_position
        await self.db.accounts.insert_one({"user_id": "owner", "id": "bank", "account_type": "bank", "current_balance": 987654321})
        legacy = await compute_financial_position(self.db, "owner")
        self.assertEqual(legacy["assets"]["banks"], 987654321)
        isolated = await mz2_financial_position(self.db, owner="owner")
        self.assertNotEqual(isolated["status"], "available")
        self.assertIsNone(isolated["assets"])

    async def test_provider_zero_opening_requires_exact_approved_batch_and_date(self):
        opening = await provision_report_opening(self.db)
        await self.setup_sale(gross="115")
        report = await mz2_financial_position(self.db, owner="owner")
        self.assertEqual(report["status"], "available", report)
        self.assertEqual(report["liabilities"]["sales_vat_payable"], 15)
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"mezan2_financial_cutover.opening_balance_zero_accounts": ""}})
        missing = await mz2_financial_position(self.db, owner="owner")
        self.assertEqual(missing["status"], "needs_opening_balance", missing)
        self.assertIsNone(missing["totals"])
        for changed in ({"opening_balance_txn_group_id": "SYN-STALE-OTHER-BATCH"},
                        {"accounting_at": "2020-01-02T00:00:00Z"}, {"evidence_ref": ""}):
            declaration = {"entity_type": "payment_gateway", "entity_id": "tamara", "sub_account": "receivable",
                "evidence_ref": "SYN-ZERO-TAMARA", "accounting_at": "2020-01-01T00:00:00Z",
                "opening_balance_txn_group_id": opening, **changed}
            await self.db.settings.update_one({"user_id": "owner"}, {"$set": {"mezan2_financial_cutover.opening_balance_zero_accounts": [declaration]}})
            blocked = await mz2_financial_position(self.db, owner="owner")
            self.assertNotEqual(blocked["status"], "available", changed)
            self.assertIsNone(blocked["totals"])

    async def test_home_uses_same_gate_and_ignores_caller_date_and_legacy(self):
        from accounting_module_ledger import ledger_only_home_balances
        self.assertIsNone(await ledger_only_home_balances(self.db, user_id="owner", cutover_at="1900-01-01T00:00:00Z"))
        await provision_report_opening(self.db)
        await self.setup_sale(gross="115")
        before = await ledger_only_home_balances(self.db, user_id="owner", cutover_at="2020-01-01T00:00:00Z")
        self.assertEqual(before["banks"], 1000)
        self.assertEqual(before["providers"], 115)
        await self.legacy_sentinels()
        after = await ledger_only_home_balances(self.db, user_id="owner", cutover_at="1900-01-01T00:00:00Z")
        self.assertEqual(after, before)
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {"mezan2_financial_cutover.opening_balance_approved_by": ""}})
        self.assertIsNone(await ledger_only_home_balances(self.db, user_id="owner", cutover_at="2020-01-01T00:00:00Z"))

    async def test_refund_period_journal_shares_gate_dates_and_current_horizon(self):
        from accounting_refund_entitlements import period_journal
        params = dict(owner="owner", from_at="2020-08-01T00:00:00+03:00", to_at="2020-09-01T00:00:00+03:00")
        blocked = await period_journal(self.db, **params)
        self.assertNotEqual(blocked["status"], "available")
        self.assertEqual(blocked["items"], [])
        await provision_report_opening(self.db)
        key = await self.setup_sale(gross="115")
        row = await self.case(key, "SYN-PERIOD-GATE", "115")
        await self.confirm(row, "2020-08-31T23:30:00+03:00")
        august = await period_journal(self.db, **params)
        self.assertEqual(len(august["items"]), 3)
        self.assertEqual({(r["entity_type"], r["side"]): r["amount"] for r in august["items"]},
            {("revenue", "debit"): 100, ("tax", "debit"): 15, ("liability", "credit"): 115})
        await self.legacy_sentinels()
        self.assertEqual(await period_journal(self.db, **params), august)
        # Explicit future economic dates cannot be pulled into the current
        # committed report simply by widening requested period boundaries.
        group = "SYN-FUTURE-DUE"
        future = []
        for original in august["items"]:
            future.append({**original, "id": uuid4().hex, "txn_group_id": group,
                "metadata": {**original["metadata"], "accounting_at": "2100-08-31T20:30:00Z", "recognized_at": "2100-08-31T20:30:00Z"}})
        await self.db.general_ledger.insert_many(future)
        future_period = await period_journal(self.db, owner="owner", from_at="2100-08-01T00:00:00Z", to_at="2100-09-01T00:00:00Z")
        self.assertEqual(future_period["items"], [])
        self.assertEqual(await period_journal(self.db, **params), august)
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {"mezan2_financial_cutover.status": "pending"}})
        blocked = await period_journal(self.db, **params)
        self.assertEqual(blocked["status"], "not_ready")
        self.assertEqual(blocked["items"], [])

    async def test_settlement_detail_hides_ledger_when_central_readiness_is_blocked(self):
        from accounting_settlement_register_routes import install_accounting_settlement_register_routes
        router = APIRouter()
        async def actor(): return {"id": self.actor}
        install_accounting_settlement_register_routes(router, self.db, actor)
        self.app.include_router(router)
        group = "SYN-SETTLEMENT-DETAIL"
        await self.db.accounting_settlements_v2.insert_one({"id": "SYN-DETAIL", "user_id": "owner", "provider": "tamara",
            "status": "posted", "ledger_txn_group_id": group, "statement_reference": "SYN-DETAIL"})
        # Legacy rows matching the draft's group cannot bypass the central gate.
        await self.db.general_ledger.insert_one({"id": uuid4().hex, "user_id": "owner", "txn_group_id": group,
            "status": "posted", "entry_type": "settlement", "entity_type": "bank", "entity_id": "bank",
            "sub_account": "main", "side": "debit", "amount": 999999, "created_at": "2020-01-02T00:00:00Z"})
        response = await self.client.get("/accounting-module/settlements/register/SYN-DETAIL")
        self.assertEqual(response.status_code, 200, response.text)
        ledger = response.json()["ledger"]
        self.assertNotEqual(ledger["status"], "available")
        self.assertEqual(ledger["entries"], [])
        self.assertEqual(ledger["entry_count"], 0)

    async def test_reversed_mz2_group_requires_review_instead_of_changed_history(self):
        await provision_report_opening(self.db)
        await self.setup_sale(gross="115")
        await self.db.general_ledger.update_many({"entry_type": "bnpl_sale"}, {"$set": {"status": "reversed"}})
        for as_of in (None, "2020-08-31"):
            report = await mz2_financial_position(self.db, owner="owner", as_of=as_of)
            self.assertEqual(report["status"], "not_ready")
            self.assertEqual(report["reason"], "reversed_mz2_group_requires_review")
            self.assertIsNone(report["totals"])

    async def test_zero_attestation_cannot_contradict_posted_opening(self):
        group = await provision_report_opening(self.db)
        contradiction = {"entity_type": "bank", "entity_id": "bank", "sub_account": "main", "evidence_ref": "SYN-CONTRADICTS-1000",
            "accounting_at": "2020-01-01T00:00:00Z", "opening_balance_txn_group_id": group}
        await self.db.settings.update_one({"user_id": "owner"}, {"$push": {"mezan2_financial_cutover.opening_balance_zero_accounts": contradiction}})
        report = await mz2_financial_position(self.db, owner="owner")
        self.assertNotEqual(report["status"], "available")
        self.assertIsNone(report["totals"])

    async def test_corrupt_entity_shapes_block_without_uncaught_report_error(self):
        await provision_report_opening(self.db)
        await self.setup_sale(gross="115")
        original = await self.db.general_ledger.find_one({"entry_type": "bnpl_sale", "entity_type": "payment_gateway"})
        for field, value in (("entity_id", {"bad": "shape"}), ("entity_type", None), ("sub_account", ["receivable"])):
            await self.db.general_ledger.update_one({"_id": original["_id"]}, {"$set": {field: value}})
            report = await mz2_financial_position(self.db, owner="owner")
            self.assertEqual(report["status"], "not_ready", field)
            self.assertIsNone(report["totals"])
            await self.db.general_ledger.update_one({"_id": original["_id"]}, {"$set": {field: original.get(field)}})
