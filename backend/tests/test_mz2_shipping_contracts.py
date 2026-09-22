"""Behavioral contract tests against the real shared calculator; no DB access.

Persistence, API permission checks, journals, rollback and CI are separate,
still-pending parts of P02 Stage 1. These unit tests do not claim their coverage.
"""
from copy import deepcopy
from decimal import Decimal
import unittest
from unittest.mock import patch

from pydantic import ValidationError
import accounting_shipping_contracts as contracts
from accounting_shipping_contracts import (
    ShippingContractError, ShippingContractInput, ShippingContractVersion,
    quote_shipping_contract, require_postable_contract,
    require_shipping_contract_charges, select_shipping_contract,
)

OWNER = "SYN-P02-CONTRACT-OWNER"
AT = "2026-09-22T12:00:00+03:00"


def terms(**changes):
    data = dict(
        courier_id="smsa", payment_mode="postpaid", shipping_cost="17.25",
        shipping_cost_vat_inclusive=True, shipping_vat_percent="15",
        commission_vat_inclusive=True, commission_vat_percent="15",
        effective_from="2026-09-01T00:00:00+03:00",
        effective_to="2026-10-01T00:00:00+03:00",
        evidence_ref="SYN-SMSA-CONTRACT-NOT-A-RUNTIME-DEFAULT", source_kind="contract",
        cod_fee_tiers=[
            dict(min_amount="50", max_amount="1000", min_inclusive=True,
                 max_inclusive=True, commission_percent="0.01", fixed_fee="2"),
            dict(min_amount="1000", max_amount="3000", min_inclusive=False,
                 max_inclusive=True, commission_percent="0.02", fixed_fee="5"),
            dict(min_amount="3000", max_amount=None, min_inclusive=False,
                 max_inclusive=True, commission_percent="0.03", fixed_fee="0"),
        ],
    )
    data.update(changes)
    return data


def record(**changes):
    data = dict(**terms(), id="SYN-SMSA-V1", user_id=OWNER, revision=1,
                verification_status="approved", approved_by=OWNER,
                approved_at="2026-09-01T00:00:00+03:00")
    data.update(changes)
    return ShippingContractVersion.model_validate(data)


def quote(version=None, amount="1000"):
    return quote_shipping_contract(
        version if version is not None else record(), owner=OWNER,
        courier_id="smsa", accounting_at=AT, cod_amount=amount,
    )


class ShippingContractTests(unittest.TestCase):
    def test_all_smsa_boundaries_are_inclusive_without_double_vat(self):
        rows = [("50", "2.17", "0.33", "2.50"), ("1000", "10.43", "1.57", "12.00"),
                ("1000.01", "21.74", "3.26", "25.00"), ("3000", "56.52", "8.48", "65.00"),
                ("3000.01", "78.26", "11.74", "90.00")]
        for amount, net, vat, gross in rows:
            with self.subTest(amount=amount):
                p = quote(amount=amount)
                c = p["calculation"]
                self.assertEqual(p["state"], "eligible")
                self.assertEqual([c[k] for k in ("cod_commission", "cod_commission_vat", "cod_commission_gross")],
                                 list(map(Decimal, (net, vat, gross))))
                self.assertEqual(c["cod_commission"] + c["cod_commission_vat"], c["cod_commission_gross"])

    def test_49_99_is_needs_review_and_not_postable_zero(self):
        self.assertEqual(quote(amount="49.99")["state"], "needs_review")
        with self.assertRaisesRegex(ShippingContractError, "needs_review"):
            require_shipping_contract_charges(record(), owner=OWNER, courier_id="smsa",
                                               accounting_at=AT, cod_amount="49.99")

    def test_shipping_17_25_is_15_plus_2_25(self):
        c = quote()["calculation"]
        self.assertEqual([c[k] for k in ("shipping_net", "shipping_vat", "shipping_gross", "payable_total")],
                         list(map(Decimal, ("15", "2.25", "17.25", "29.25"))))

    def test_prepaid_customer_order_has_no_cod_even_without_tiers(self):
        c = quote(record(cod_fee_tiers=[]), amount=None)["calculation"]
        self.assertEqual(c["cod_gross"], Decimal(0))
        self.assertEqual(c["cod_commission_gross"], Decimal(0))
        self.assertEqual(c["payable_total"], Decimal("17.25"))

    def test_carrier_prepayment_mode_does_not_remove_customer_cod(self):
        c = quote(record(payment_mode="prepaid"))["calculation"]
        self.assertEqual(c["cod_gross"], Decimal("1000"))
        self.assertEqual(c["cod_commission_gross"], Decimal("12"))

    def test_cod_without_tiers_requires_review(self):
        self.assertEqual(quote(record(cod_fee_tiers=[]))["state"], "needs_review")

    def test_imile_is_independent_by_courier_id_and_contract(self):
        v = record(id="SYN-IMILE-V1", courier_id="imile", commission_vat_inclusive=False)
        c = quote_shipping_contract(v, owner=OWNER, courier_id="imile",
                                    accounting_at=AT, cod_amount="1000")["calculation"]
        self.assertEqual(c["cod_commission_gross"], Decimal("13.80"))
        self.assertEqual(c["shipping_gross"], Decimal("17.25"))
        self.assertEqual(quote()["calculation"]["cod_commission_gross"], Decimal("12"))

    def test_shipping_and_commission_inclusion_are_independent(self):
        c = quote(record(shipping_cost="15", shipping_cost_vat_inclusive=False))["calculation"]
        self.assertEqual(c["shipping_gross"], Decimal("17.25"))
        self.assertEqual(c["cod_commission_gross"], Decimal("12"))

    def test_unverified_and_legacy_are_rejected_before_calculation(self):
        for source in ("contract", "legacy_copy"):
            draft = record(source_kind=source, verification_status="unverified",
                           approved_by=None, approved_at=None)
            with self.subTest(source=source), patch.object(contracts, "calculate_courier_charges") as engine:
                with self.assertRaisesRegex(ShippingContractError, "not_approved"):
                    quote(draft)
                engine.assert_not_called()
        with self.assertRaises(ValidationError):
            record(source_kind="legacy_copy")

    def test_approval_requires_both_identity_and_time(self):
        for changes in ({"approved_by": None}, {"approved_at": None}, {"approved_by": " "},
                        {"verification_status": "unverified"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                record(**changes)

    def test_terms_input_cannot_supply_tenant_or_approval_metadata(self):
        for field, value in (("user_id", "forged"), ("approved_by", "forged"),
                             ("verification_status", "approved"), ("approved_at", AT)):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ShippingContractInput.model_validate(terms(**{field: value}))

    def test_exact_owner_and_courier_scope(self):
        for owner, courier in (("OTHER", "smsa"), (OWNER, "imile")):
            with self.subTest(owner=owner, courier=courier), self.assertRaisesRegex(ShippingContractError, "scope"):
                require_postable_contract(record(), owner=owner, courier_id=courier, accounting_at=AT)

    def test_effective_window_is_start_inclusive_end_exclusive(self):
        v = record()
        require_postable_contract(v, owner=OWNER, courier_id="smsa", accounting_at=v.effective_from)
        for when in ("2026-08-31T23:59:59+03:00", v.effective_to):
            with self.subTest(when=when), self.assertRaisesRegex(ShippingContractError, "not_effective"):
                require_postable_contract(v, owner=OWNER, courier_id="smsa", accounting_at=when)

    def test_timezone_and_interval_validation(self):
        for changes in ({"effective_from": "2026-09-01"},
                        {"effective_to": "2026-09-01T00:00:00+03:00"},
                        {"effective_to": "2026-08-01T00:00:00+03:00"},
                        {"approved_at": "2026-09-01T00:00:00"}):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                record(**changes)

    def test_overlapping_contracts_fail_instead_of_choosing_newest(self):
        old = record()
        new = record(id="SYN-V2", revision=2, shipping_cost="23")
        with self.assertRaisesRegex(ShippingContractError, "ambiguous"):
            select_shipping_contract([old, new], owner=OWNER, courier_id="smsa", accounting_at=AT)
        other = record(id="OTHER", user_id="OTHER")
        selected = select_shipping_contract([other, old], owner=OWNER, courier_id="smsa", accounting_at=AT)
        self.assertEqual(selected.id, old.id)

    def test_adjacent_versions_select_exact_boundary_without_changing_old_terms(self):
        old = record()
        new = record(id="SYN-OCT", revision=2, effective_from=old.effective_to,
                     effective_to=None, shipping_cost="23")
        selected = select_shipping_contract([new, old], owner=OWNER, courier_id="smsa",
                                           accounting_at=old.effective_to)
        self.assertEqual(selected.id, new.id)
        self.assertEqual(old.shipping_cost, Decimal("17.25"))

    def test_unverified_draft_does_not_shadow_approved_contract(self):
        draft = record(id="SYN-DRAFT", revision=2, shipping_cost="99", verification_status="unverified",
                       approved_by=None, approved_at=None)
        selected = select_shipping_contract([record(), draft], owner=OWNER, courier_id="smsa", accounting_at=AT)
        self.assertEqual(selected.id, "SYN-SMSA-V1")
        with self.assertRaisesRegex(ShippingContractError, "approved_version_missing"):
            select_shipping_contract([draft], owner=OWNER, courier_id="smsa", accounting_at=AT)

    def test_overlapping_and_ambiguous_cod_tiers_rejected(self):
        for changes in ({"min_inclusive": True}, {"min_amount": "999.99"}):
            tiers = terms()["cod_fee_tiers"]
            tiers[1].update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                record(cod_fee_tiers=tiers)

    def test_boundary_flags_and_tax_flags_are_explicit_booleans(self):
        tiers = terms()["cod_fee_tiers"]
        del tiers[0]["max_inclusive"]
        with self.assertRaises(ValidationError):
            record(cod_fee_tiers=tiers)
        for field in ("shipping_cost_vat_inclusive", "commission_vat_inclusive"):
            for value in ("true", "false", 0, 1, None):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    record(**{field: value})

    def test_tier_tax_cannot_contradict_contract(self):
        for field, value in (("vat_included", False), ("vat_percent", "5")):
            tiers = terms()["cod_fee_tiers"]
            tiers[0][field] = value
            with self.subTest(field=field), self.assertRaises(ValidationError):
                record(cod_fee_tiers=tiers)

    def test_missing_tax_never_inherits_historical_defaults(self):
        for field in ("commission_vat_percent", "commission_vat_inclusive",
                      "shipping_cost_vat_inclusive", "shipping_vat_percent"):
            raw = terms()
            del raw[field]
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ShippingContractInput.model_validate(raw)

    def test_immutable_values_and_json_roundtrip(self):
        v = record()
        with self.assertRaises(ValidationError):
            v.shipping_cost = Decimal("999")
        with self.assertRaises(ValidationError):
            v.cod_fee_tiers[0].fixed_fee = Decimal("999")
        self.assertEqual(ShippingContractVersion.model_validate_json(v.model_dump_json()), v)
        raw = terms()
        before = deepcopy(raw)
        ShippingContractInput.model_validate(raw)
        self.assertEqual(raw, before)
        self.assertIsInstance(quote(v)["contract_snapshot"]["shipping_cost"], str)

    def test_model_copy_cannot_bypass_posting_guard(self):
        with self.assertRaises(ValidationError):
            quote(record().model_copy(update={"approved_by": None}))

    def test_invalid_amount_and_blank_evidence_rejected(self):
        for value in ("NaN", "Infinity", "-1", "17.251"):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                record(shipping_cost=value)
        with self.assertRaises(ValidationError):
            record(evidence_ref="   ")

    def test_real_shared_engine_receives_decimal_tiers(self):
        shared = contracts.calculate_courier_charges
        with patch.object(contracts, "calculate_courier_charges", wraps=shared) as engine:
            result = quote()
        self.assertEqual(engine.call_count, 1)
        tier = engine.call_args.kwargs["contract"]["cod_fee_tiers"][0]
        self.assertIsInstance(tier.min_amount, Decimal)
        self.assertIsInstance(tier.commission_percent, Decimal)
        self.assertEqual(result["calculation"]["calculator_source"], "courier_cod_fee_rules")


if __name__ == "__main__":
    unittest.main()
