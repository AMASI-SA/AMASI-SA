"""Synthetic contract tests only. No HTTP or persistent Mongo acceptance claim."""
import ast
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timezone
from decimal import Decimal as D
from pathlib import Path
import unittest

import accounting_advertising_contract as c

DAY = date(2026, 9, 21)


def account(**kw):
    return replace(c.LinkedAccount("owner-a", "linked-1", "snapchat", "source-1", "Synthetic", "SAR", "Asia/Riyadh"), **kw)


def fx(value="100", currency="SAR", **kw):
    args = dict(fx_source="cost-settings:synthetic", settings_revision="v1", settings={"bank_fee": "2.5", "rate_version": 1})
    args.update(kw)
    return c.freeze_fx(value, currency, **args)


def daily(value="100", revision=1, state="DRAFT_CREATED", a=None, mode="postpaid", accrual="daily_spend", snapshot=None):
    a = a or account()
    evidence = c.DailyEvidence(a.identity, DAY, a.timezone_name, "source-report", revision, True, snapshot or fx(value, a.currency))
    return replace(c.daily_draft(a, evidence, mode, accrual), state=state)


def invoice(value="100", **kw):
    args = dict(account=account(), external_number="INV-1", period_start=DAY, period_end=DAY,
                issued_on=DAY, due_on=date(2026, 9, 30), fx=fx(value), evidence_ref="file:synthetic", source="upload")
    args.update(kw)
    return c.InvoiceDraft(**args)


def payment(positions, allocations, **kw):
    args = dict(payment_mode="postpaid", source_ref="actual-bank-not-default", movement_ref="movement-1", evidence_ref="proof-1", bank_principal_sar="40")
    args.update(kw)
    return c.payment_preview("owner-a", positions, allocations, **args)


class ContractTests(unittest.TestCase):
    def error(self, code, fn, *args, **kw):
        with self.assertRaises(c.ContractError) as got:
            fn(*args, **kw)
        self.assertEqual(got.exception.code, code)

    def test_actual_registry_selection_only(self):
        self.assertEqual(c.select_linked("owner-a", [account()], "linked-1"), account())
        self.error("ACCOUNT_NOT_LINKED", c.select_linked, "owner-a", [account()], "free-external-id")

    def test_other_owner_registry_hidden(self):
        self.assertEqual(c.linked_accounts("owner-b", [account()]), ())

    def test_duplicate_identity_rejected_even_different_link_ref(self):
        self.error("DUPLICATE_SOURCE", c.linked_accounts, "owner-a", [account(), account(linked_account_ref="other")])

    def test_duplicate_link_ref_rejected(self):
        self.error("DUPLICATE_SOURCE", c.linked_accounts, "owner-a", [account(), account(external_account_id="other")])

    def test_identity_partitions_provider_and_owner(self):
        keys = [c.identity_key(a.identity) for a in [account(), account(ad_provider="meta"), account(owner_id="owner-b")]]
        self.assertEqual(len(set(keys)), 3)

    def test_four_provider_contract(self):
        for provider in c.PROVIDERS:
            self.assertEqual(account(ad_provider=provider).ad_provider, provider)
        self.error("UNSUPPORTED_PROVIDER", account, ad_provider="free-provider")

    def test_three_modes_and_explicit_accrual(self):
        for mode in c.PAYMENT_MODES:
            self.assertEqual(daily(mode=mode).payment_mode, mode)
        self.error("INVALID_ACCOUNT_POLICY", daily, mode="wallet-and-credit")
        self.error("INVALID_ACCOUNT_POLICY", daily, accrual="both")

    def test_riyadh_0100_local(self):
        window = c.close_window(DAY, "Asia/Riyadh")
        self.assertEqual(window.due_utc, datetime(2026, 9, 21, 22, tzinfo=timezone.utc))
        self.assertIsNone(c.yesterday_due(datetime(2026, 9, 21, 21, 59, tzinfo=timezone.utc), "Asia/Riyadh"))
        self.assertEqual(c.yesterday_due(window.due_utc, "Asia/Riyadh"), window)

    def test_los_angeles_winter_and_summer(self):
        self.assertEqual(c.close_window(date(2026, 1, 1), "America/Los_Angeles").due_utc.hour, 9)
        self.assertEqual(c.close_window(date(2026, 7, 1), "America/Los_Angeles").due_utc.hour, 8)

    def test_spring_dst_local_day_not_24_hour_subtraction(self):
        before = c.close_window(date(2026, 3, 7), "America/Los_Angeles")
        after = c.close_window(date(2026, 3, 8), "America/Los_Angeles")
        self.assertEqual((after.due_utc - before.due_utc).total_seconds(), 23 * 3600)

    def test_fall_repeated_one_am_same_close_window(self):
        first = c.yesterday_due(datetime(2026, 11, 1, 8, tzinfo=timezone.utc), "America/Los_Angeles")
        second = c.yesterday_due(datetime(2026, 11, 1, 9, tzinfo=timezone.utc), "America/Los_Angeles")
        self.assertEqual(first, second)
        self.assertEqual(first.economic_date, date(2026, 10, 31))
        self.assertEqual(first.due_utc.hour, 8)
        following = c.close_window(date(2026, 11, 1), "America/Los_Angeles")
        self.assertEqual((following.due_utc - first.due_utc).total_seconds(), 25 * 3600)

    def test_missing_invalid_timezone_and_naive_time_blocked(self):
        self.error("BLOCKED_TIMEZONE_MISSING", c.close_window, DAY, None)
        self.error("BLOCKED_TIMEZONE_INVALID", c.close_window, DAY, "not/a/zone")
        self.error("BLOCKED_TIMEZONE_MISSING", daily, a=account(timezone_name=None))
        self.error("AWARE_TIME_REQUIRED", c.yesterday_due, datetime(2026, 9, 22), "Asia/Riyadh")

    def test_failed_sync_not_zero_draft(self):
        evidence = replace(daily("0").evidence, complete=False)
        self.error("INCOMPLETE_DATA", c.daily_draft, account(), evidence, "postpaid", "daily_spend")
        self.error("INCOMPLETE_DATA", fx, None)
        self.assertEqual(daily("0").proposed_expense_sar, D("0.00"))

    def test_source_scope_and_currency_must_match(self):
        self.error("SOURCE_SCOPE_MISMATCH", c.daily_draft, account(), replace(daily().evidence, account_identity=account(owner_id="other").identity), "postpaid", "daily_spend")
        self.error("SOURCE_CURRENCY_MISMATCH", daily, snapshot=fx("100", "USD", rate="3.75"))

    def test_explicit_source_revision_required(self):
        self.error("SOURCE_REVISION_REQUIRED", daily, revision=0)
        self.error("SOURCE_REVISION_REQUIRED", daily, revision=True)

    def test_money_fail_closed_no_float_or_nan(self):
        for bad in (float("nan"), 10.25, True, "NaN", "Infinity", "-1", "1e100", "1e-100"):
            self.error("INVALID_AMOUNT", fx, bad)

    def test_snapshot_immutable_and_detached(self):
        settings = {"nested": {"rate": "3.75"}, "bank_fee": "2.5"}
        frozen = fx("100", "USD", rate="3.75", settings=settings)
        settings["nested"]["rate"] = "4.5"
        self.assertEqual(frozen.value_sar, D("375.00"))
        self.assertIn("3.75", frozen.settings_json)
        with self.assertRaises(FrozenInstanceError):
            frozen.rate_used = D("4.5")

    def test_no_double_fx_ready_sar(self):
        frozen = fx("100", "USD", rate="3.75", source_already_sar=True, source_sar="375")
        self.assertEqual(frozen.value_sar, D("375.00"))
        self.assertEqual(frozen.original_amount, D("100"))
        self.assertEqual(frozen.rate_used, D("1"))
        self.error("DOUBLE_FX", fx, "100", rate="3.75")
        self.error("INCOMPLETE_DATA", fx, "100", "USD", source_already_sar=True)

    def test_fx_required_and_contradictory_sar_rejected(self):
        self.error("INCOMPLETE_DATA", fx, "100", "USD")
        self.error("FX_RATE_REQUIRED", fx, "100", "USD", rate="0")
        self.error("SOURCE_AMOUNT_CONFLICT", fx, "100", source_already_sar=True, source_sar="375")
        self.error("AMBIGUOUS_SAR_SOURCE", fx, "100", source_sar="100")

    def test_bank_commission_not_in_daily_wallet_spend(self):
        preview = c.expense_preview("prepaid_wallet", fx().value_sar, "sibling-wallet", accrual_source="daily_spend", wallet_balance="500")
        self.assertEqual(sum(p.debit for p in preview.legs), D("100"))
        self.assertFalse(any(p.role == "bank_fee_expense" for p in preview.legs))
        self.error("BANK_FEE_NOT_DAILY_EXPENSE", c.expense_preview, "prepaid_wallet", "100", "sibling-wallet", accrual_source="daily_spend", wallet_balance="500", actual_fee="2.5")

    def test_missing_and_insufficient_wallet(self):
        self.error("MISSING_WALLET", c.expense_preview, "prepaid_wallet", "100", None, accrual_source="daily_spend")
        self.error("NEEDS_REVIEW", c.expense_preview, "prepaid_wallet", "100", "wallet", accrual_source="daily_spend")
        self.error("INSUFFICIENT_WALLET_BALANCE", c.expense_preview, "prepaid_wallet", "100", "wallet", accrual_source="daily_spend", wallet_balance="99.99")

    def test_postpaid_and_direct_debit_differ(self):
        credit = c.expense_preview("postpaid", "100", "payable", accrual_source="daily_spend")
        debit = c.expense_preview("direct_debit", "100", "actual-cash", accrual_source="daily_spend", movement_ref="matched", bank_principal="100", actual_fee="2")
        self.assertEqual(credit.legs[-1].role, "platform_payable")
        self.assertEqual(debit.legs[-1].credit, D("102"))
        self.assertEqual(debit.legs[-1].reference, "actual-cash")
        self.error("BANK_DIFFERENCE", c.expense_preview, "direct_debit", "100", "bank", accrual_source="daily_spend", movement_ref="matched", bank_principal="99")

    def test_invoice_unique_key(self):
        self.assertEqual(invoice().key, invoice(source="api").key)
        self.assertNotEqual(invoice().key, invoice(external_number="INV-2").key)
        self.assertNotEqual(invoice().key, invoice(account=account(owner_id="other")).key)

    def test_invoice_import_draft_ignores_provider_paid_flag(self):
        pos = c.InvoicePosition(invoice(provider_status="paid"))
        self.assertEqual(pos.status(DAY), "draft")
        self.assertEqual(pos.status(DAY, reviewed=True), "unpaid")
        self.assertEqual(pos.remaining_sar, D("100"))

    def test_manual_invoice_requires_evidence_note(self):
        self.error("INVOICE_EVIDENCE_REQUIRED", invoice, source="manual")
        self.assertEqual(invoice(source="manual", manual_note="Synthetic documented intake").source, "manual")
        self.error("INVALID_INVOICE_DATE", invoice, due_on=date(2026, 9, 20))

    def test_invoice_difference_needs_review_not_full_expense(self):
        match = c.match_invoice(invoice("10050"), "daily_spend", "10000", coverage_complete=True)
        self.assertEqual((match.difference_sar, match.proposed_expense_sar), (D("50"), D("0")))
        self.assertEqual(match.kind, "NEEDS_REVIEW")

    def test_approved_invoice_difference_only_adjustment_draft(self):
        match = c.match_invoice(invoice("10050"), "daily_spend", "10000", coverage_complete=True, approved_difference=True)
        self.assertEqual((match.kind, match.proposed_expense_sar), ("adjustment_draft", D("50")))
        self.assertEqual(c.match_invoice(invoice(), "daily_spend", "100", coverage_complete=True).kind, "matched")

    def test_negative_invoice_difference_remains_visible(self):
        match = c.match_invoice(invoice("9950"), "daily_spend", "10000", coverage_complete=True, approved_difference=True)
        self.assertEqual(match.proposed_expense_sar, D("-50"))

    def test_invoice_source_excludes_daily_accrual(self):
        self.assertEqual(daily(accrual="invoice").proposed_expense_sar, D("0"))
        self.error("NON_ACCRUING_DAILY_SOURCE", c.expense_preview, "postpaid", "100", "payable", accrual_source="invoice")
        self.error("DUPLICATE_SOURCE", c.match_invoice, invoice(), "invoice", "100", coverage_complete=True)
        self.assertEqual(c.match_invoice(invoice(), "invoice", "0", coverage_complete=True).proposed_expense_sar, D("100"))

    def test_missing_coverage_not_assumed_zero(self):
        self.error("INCOMPLETE_DATA", c.match_invoice, invoice(), "daily_spend", "0", coverage_complete=False)
        self.error("INCOMPLETE_DATA", c.match_invoice, invoice(), "daily_spend", None, coverage_complete=True)
        self.error("INCOMPLETE_DATA", c.match_invoice, invoice(), "daily_spend", "0", coverage_complete="false")

    def test_partial_payment_preserves_remaining(self):
        inv = invoice()
        preview = payment([c.InvoicePosition(inv)], [(inv.key, "40")])
        self.assertEqual(preview.remaining[0][1:], (D("60"), D("60")))
        self.assertFalse(preview.journal.posting_available)
        self.assertEqual(c.InvoicePosition(inv, D("40"), D("40")).status(DAY, reviewed=True), "partially_paid")

    def test_multiple_installments_stop_at_total(self):
        inv = invoice()
        pos = c.InvoicePosition(inv, D("40"), D("40"))
        final = payment([pos], [(inv.key, "60")], bank_principal_sar="60")
        self.assertEqual(final.remaining[0][1:], (D("0"), D("0")))
        self.error("ALLOCATION_EXCEEDS_REMAINING", payment, [pos], [(inv.key, "60.01")])
        self.assertEqual(c.InvoicePosition(inv, D("100"), D("100")).status(DAY, reviewed=True), "paid")

    def test_one_payment_allocated_to_two_invoices(self):
        first, second = invoice(), invoice(external_number="INV-2")
        preview = payment([c.InvoicePosition(first), c.InvoicePosition(second)], [(first.key, "40"), (second.key, "50")], bank_principal_sar="90")
        self.assertEqual(len(preview.allocations), 2)
        self.assertEqual([r[1] for r in preview.remaining], [D("60"), D("50")])

    def test_duplicate_invoice_or_allocation_rejected(self):
        inv = invoice()
        pos = c.InvoicePosition(inv)
        self.error("DUPLICATE_SOURCE", payment, [pos, pos], [(inv.key, "40")])
        self.error("DUPLICATE_SOURCE", payment, [pos], [(inv.key, "20"), (inv.key, "20")])

    def test_actual_bank_and_evidence_retained_not_defaulted(self):
        inv = invoice()
        preview = payment([c.InvoicePosition(inv)], [(inv.key, "40")])
        self.assertEqual((preview.source_ref, preview.movement_ref, preview.evidence_ref), ("actual-bank-not-default", "movement-1", "proof-1"))
        for field in ("source_ref", "movement_ref", "evidence_ref"):
            self.error("INVALID_REFERENCE", payment, [c.InvoicePosition(inv)], [(inv.key, "40")], **{field: ""})

    def test_cross_owner_payment_denied(self):
        inv = invoice(account=account(owner_id="other"))
        self.error("INVOICE_SCOPE_MISMATCH", payment, [c.InvoicePosition(inv)], [(inv.key, "40")])

    def test_actual_fee_fx_gain_and_loss_separate_and_balanced(self):
        inv = invoice()
        for actual, role in (("42", "fx_loss"), ("38", "fx_gain")):
            preview = payment([c.InvoicePosition(inv)], [(inv.key, "40")], bank_principal_sar=actual, bank_fee_sar="2")
            roles = {p.role for p in preview.journal.legs}
            self.assertIn(role, roles)
            self.assertIn("bank_fee_expense", roles)
            self.assertEqual(sum(p.debit for p in preview.journal.legs), sum(p.credit for p in preview.journal.legs))

    def test_native_currency_partial_carrying_and_no_double_fx(self):
        inv = invoice(account=account(currency="USD"), fx=fx("100", "USD", source_already_sar=True, source_sar="375", rate="3.75"))
        preview = payment([c.InvoicePosition(inv)], [(inv.key, "40")], bank_principal_sar="150")
        self.assertEqual(preview.allocations[0][1:], (D("40"), D("150")))
        self.assertEqual(preview.remaining[0][1:], (D("60"), D("225")))

    def test_last_installment_consumes_rounding_residual(self):
        inv = invoice(account=account(currency="USD"), fx=fx("3", "USD", source_already_sar=True, source_sar="0.10"))
        pos = c.InvoicePosition(inv)
        paid_native, paid_sar = D("0"), D("0")
        for _ in range(3):
            preview = payment([pos], [(inv.key, "1")], bank_principal_sar="0.04")
            paid_native += preview.allocations[0][1]
            paid_sar += preview.allocations[0][2]
            pos = c.InvoicePosition(inv, paid_native, paid_sar)
        self.assertEqual((pos.remaining_original, pos.remaining_sar), (D("0"), D("0")))

    def test_overdue_and_paid_states(self):
        inv = invoice()
        self.assertEqual(c.InvoicePosition(inv, D("40"), D("40")).status(date(2026, 10, 1), reviewed=True), "overdue")
        self.assertEqual(c.InvoicePosition(inv, D("100"), D("100")).status(date(2026, 10, 1), reviewed=True), "paid")

    def test_unposted_late_change_resets_review_and_audits(self):
        current = daily(state="REVIEWED")
        result = c.revise_daily(current, daily("120", revision=2))
        self.assertEqual((result.kind, result.replacement.state), ("replace_draft", "DRAFT_CREATED"))
        self.assertEqual(result.difference_sar, D("20"))
        self.assertEqual(current.state, "REVIEWED")
        self.assertIn("review_invalidated", result.audit)

    def test_posted_late_change_never_mutates_original(self):
        current = daily(state="POSTED")
        result = c.revise_daily(current, daily("120", revision=2), accounted_total_sar="100")
        self.assertEqual((result.kind, result.difference_sar, result.replacement), ("adjustment_draft", D("20"), None))
        self.assertEqual(current.evidence.fx.value_sar, D("100"))
        self.assertEqual(current.state, "POSTED")

    def test_adjustment_uses_already_accounted_total(self):
        result = c.revise_daily(daily(state="POSTED"), daily("130", revision=3), accounted_total_sar="120")
        self.assertEqual(result.difference_sar, D("10"))
        self.error("INCOMPLETE_DATA", c.revise_daily, daily(state="POSTED"), daily("130", revision=3))

    def test_historical_fx_not_replaced_on_late_update(self):
        a = account(currency="USD")
        old = daily(a=a, snapshot=fx("100", "USD", rate="3.75"))
        fresh = daily(a=a, revision=2, snapshot=fx("120", "USD", rate="4", settings_revision="v2"))
        result = c.revise_daily(old, fresh)
        self.assertEqual(result.replacement.evidence.fx.value_sar, D("450"))
        self.assertEqual(result.replacement.evidence.fx.settings_revision, "v1")

    def test_same_revision_replay_conflict_and_stale(self):
        self.assertEqual(c.revise_daily(daily(), daily()).kind, "unchanged")
        self.error("DUPLICATE_SOURCE", c.revise_daily, daily(), daily("120"))
        self.error("STALE_SOURCE_REVISION", c.revise_daily, daily(revision=2), daily())

    def test_day_historical_policy_cannot_change(self):
        self.error("HISTORICAL_POLICY_CHANGED", c.revise_daily, daily(), daily(revision=2, accrual="invoice"))

    def test_nine_permissions_fail_closed_and_post_always_denied(self):
        self.assertEqual(len(c.PERMISSIONS), 9)
        for permission in c.PERMISSIONS:
            self.assertFalse(c.can_preview(None, "owner-a", list(c.PERMISSIONS), permission))
            self.assertFalse(c.can_preview("other", "owner-a", list(c.PERMISSIONS), permission))
            self.assertFalse(c.can_preview("owner-a", "owner-a", [], permission))
            self.assertEqual(c.can_preview("owner-a", "owner-a", list(c.PERMISSIONS), permission), permission != c.POST_PERMISSION)
        self.assertFalse(c.can_preview("owner-a", "owner-a", "accounting.advertising.view-extra", "accounting.advertising.view"))
        self.assertFalse(c.can_preview("owner-a", "owner-a", ["unknown"], "unknown"))

    def test_concurrent_key_computation_only_not_database_idempotency(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            keys = list(pool.map(lambda _: daily().source_key, range(100)))
        self.assertEqual(len(set(keys)), 1)

    def test_no_ledger_database_http_scheduler_or_legacy_imports(self):
        source = Path(c.__file__).read_text()
        tree = ast.parse(source)
        imports = {node.module.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imports |= {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertLessEqual(imports, {"__future__", "dataclasses", "datetime", "decimal", "hashlib", "json", "typing", "zoneinfo"})
        self.assertNotIn("post_txn_group", source)
        self.assertNotIn("APIRouter", source)

    def test_negative_journal_leg_rejected(self):
        self.error("INVALID_AMOUNT", c.PreviewLeg, "bank", "ref", D("0"), D("-1"))

    def test_two_sided_or_fractional_sar_leg_rejected(self):
        self.error("INVALID_PREVIEW", c.PreviewLeg, "bank", "ref", D("1"), D("1"))
        self.error("INVALID_PREVIEW", c.PreviewLeg, "bank", "ref", D("0.001"))

    def test_inexact_paid_carrying_value_rejected(self):
        self.error("INVALID_PAID_POSITION", c.InvoicePosition, invoice(), D("1"), D("1.001"))

    def test_review_flag_must_be_explicit_boolean(self):
        self.assertEqual(c.InvoicePosition(invoice()).status(DAY, reviewed="false"), "draft")

    def test_invoice_accrual_cannot_emit_daily_posted_adjustment(self):
        self.error("NON_ACCRUING_DAILY_SOURCE", c.revise_daily, daily(state="POSTED", accrual="invoice"), daily("120", revision=2, accrual="invoice"), accounted_total_sar="100")

    def test_revision_does_not_trust_a_spoofed_key(self):
        fresh = daily(revision=2)
        fresh = replace(fresh, evidence=replace(fresh.evidence, economic_date=date(2026, 9, 20)))
        self.error("SOURCE_SCOPE_MISMATCH", c.revise_daily, daily(), fresh)

    def test_payment_mode_cannot_be_wallet_or_direct_debit(self):
        inv = invoice()
        for mode in ("prepaid_wallet", "direct_debit"):
            self.error("PAYMENT_MODE_MISMATCH", payment, [c.InvoicePosition(inv)], [(inv.key, "40")], payment_mode=mode)

    def test_invoice_billing_currency_can_differ_from_ad_account(self):
        inv = invoice(fx=fx("100", "USD", rate="3.75"))
        self.assertEqual(inv.account.currency, "SAR")
        self.assertEqual(inv.fx.original_currency, "USD")
        self.assertEqual(inv.fx.value_sar, D("375"))

    def test_preview_cannot_turn_on_posting(self):
        self.error("INVALID_PREVIEW", c.JournalPreview, (), True)


if __name__ == "__main__":
    unittest.main()
