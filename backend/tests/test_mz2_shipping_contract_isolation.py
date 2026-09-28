"""V4 permission/gate/Decimal specifications. Authored only; not executed in review.

No Mongo, synthetic evidence service, live route wiring or production data.
Positive persistence/accrual tests remain pending the real integration slice.
"""
from copy import deepcopy
from contextlib import ExitStack, contextmanager
from decimal import Decimal, Inexact, Rounded, ROUND_DOWN, ROUND_HALF_UP, localcontext
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import APIRouter, HTTPException
import accounting_shipping_contract_service as service
from accounting_module_contract import (
    ACCOUNTING_PERMISSION_KEYS, SHIPPING_CONTRACT_PERMISSIONS,
    accounting_permissions_for_user, require_accounting_permission,
)
from accounting_shipping_contract_gate import (
    native_contract_readiness, require_native_contract_runtime,
)

REVIEW = "accounting.shipping.contracts.review"


class ShippingContractIsolationTests(unittest.TestCase):
    def actor(self, *, role="employee", permissions=()):
        return {"id": "SYN-P02-ACTOR", "role": role, "created_by": "SYN-P02-OWNER",
                "accounting_permissions": list(permissions)}

    def denied(self, actor, permission):
        with self.assertRaises(HTTPException) as result:
            require_accounting_permission(actor, permission)
        self.assertEqual(result.exception.status_code, 403)

    def test_review_key_is_registered_centrally_and_independent(self):
        self.assertIn(REVIEW, ACCOUNTING_PERMISSION_KEYS)
        self.assertEqual(SHIPPING_CONTRACT_PERMISSIONS["review"], REVIEW)
        self.assertEqual(len(set(SHIPPING_CONTRACT_PERMISSIONS.values())), 4)

    def test_owner_does_not_automatically_receive_new_review_permission(self):
        actor = self.actor(role="owner")
        self.assertNotIn(REVIEW, accounting_permissions_for_user(actor))
        self.denied(actor, REVIEW)

    def test_owner_keeps_existing_permissions_except_explicit_review(self):
        actor = self.actor(role="owner")
        self.assertEqual(set(accounting_permissions_for_user(actor)),
                         set(ACCOUNTING_PERMISSION_KEYS) - {REVIEW})

    def test_owner_receives_review_only_after_explicit_assignment(self):
        require_accounting_permission(self.actor(role="owner", permissions=[REVIEW]), REVIEW)

    def test_manager_has_management_but_not_review_or_posting(self):
        actor = self.actor(permissions=["accounting.shipping.view", "accounting.rules.manage"])
        require_accounting_permission(actor, "accounting.rules.manage")
        self.denied(actor, REVIEW)
        self.denied(actor, "accounting.settlements.post")

    def test_reviewer_does_not_inherit_management_or_posting(self):
        actor = self.actor(permissions=["accounting.shipping.view", REVIEW])
        require_accounting_permission(actor, REVIEW)
        self.denied(actor, "accounting.rules.manage")
        self.denied(actor, "accounting.settlements.post")

    def test_poster_cannot_manage_or_review_contracts(self):
        actor = self.actor(permissions=["accounting.shipping.view", "accounting.settlements.post"])
        require_accounting_permission(actor, "accounting.settlements.post")
        self.denied(actor, "accounting.rules.manage")
        self.denied(actor, REVIEW)

    def test_review_does_not_implicitly_grant_shipping_view(self):
        actor = self.actor(permissions=[REVIEW])
        require_accounting_permission(actor, REVIEW)
        self.denied(actor, "accounting.shipping.view")

    def test_permission_resolution_does_not_mutate_or_grant_on_user_record(self):
        actor = self.actor(role="owner")
        before = deepcopy(actor)
        accounting_permissions_for_user(actor)
        self.assertEqual(actor, before)

    def test_unknown_permission_remains_excluded(self):
        actor = self.actor(permissions=["accounting.shipping.contracts.fake"])
        self.assertEqual(accounting_permissions_for_user(actor), [])

    def test_native_gate_is_closed_and_cannot_be_changed_by_readiness_result(self):
        status = native_contract_readiness()
        status["enabled"] = True
        self.assertFalse(native_contract_readiness()["enabled"])
        with self.assertRaises(HTTPException) as closed:
            require_native_contract_runtime()
        self.assertEqual(closed.exception.status_code, 423)
        self.assertEqual(closed.exception.detail["code"], "shipping_contract_native_path_locked")

    def test_readiness_exposes_missing_evidence_and_first_cod_gap(self):
        status = native_contract_readiness()
        self.assertEqual(status["production_evidence_service"], "NOT_INTEGRATED")
        self.assertEqual(status["first_external_cod_sale"], "cod_base_receivable_required")
        self.assertEqual(status["current_shipping_routes"], "UNCHANGED")




class ForbiddenAccess:
    """Record any attempted data/source access and fail immediately."""
    def __init__(self):
        self.accesses = []

    def __getattr__(self, name):
        self.accesses.append(("attribute", name))
        raise AssertionError("UNEXPECTED_DEPENDENCY_ACCESS:" + name)

    def __getitem__(self, key):
        self.accesses.append(("item", key))
        raise AssertionError("UNEXPECTED_DEPENDENCY_ACCESS:" + str(key))


class ShippingContractCallBoundaryTests(unittest.IsolatedAsyncioTestCase):
    """Exercise real closed entry functions; never replace/open their gate."""

    @contextmanager
    def no_dependencies(self, *, include_prepare=False):
        # These observers must remain entirely unused. They are not synthetic
        # evidence adapters or transaction/ledger implementations.
        async_names = (
            "_actor", "fresh_actor", "_courier", "configured_couriers",
            "_lock_courier_identity", "_cod_source", "_event_record",
            "read_shipping_policy", "read_mz2_write_balances", "read_mz2_ledger",
            "_insert_event_posting", "_finish_event", "atomic_owner",
            "post_txn_group", "assert_open_journal_periods",
            "require_p02_shipping_financial_writes",
        )
        sync_names = (
            "normalize_shipping_company", "shipping_order_provider",
            "select_shipping_contract", "quote_shipping_contract",
            "_event_date_from_salla", "_money", "_positive", "_hash",
            "accrual_entries",
        )
        if include_prepare:
            async_names += ("prepare_contract_courier",)
        observers = {}
        with ExitStack() as stack:
            for name in async_names:
                observers[name] = stack.enter_context(patch.object(
                    service, name, new_callable=AsyncMock,
                    side_effect=AssertionError("UNEXPECTED_CALL:" + name),
                ))
            for name in sync_names:
                observers[name] = stack.enter_context(patch.object(
                    service, name, new_callable=Mock,
                    side_effect=AssertionError("UNEXPECTED_CALL:" + name),
                ))
            for name in ("_approved_service", "readiness", "validate_bundle", "bundle_of"):
                observers["evidence." + name] = stack.enter_context(patch.object(
                    service.shipping_evidence, name, new_callable=Mock,
                    side_effect=AssertionError("UNEXPECTED_EVIDENCE:" + name),
                ))
            for name in ("approval_snapshot", "snapshot_one", "verify_bundle", "pin_bundle"):
                observers["evidence." + name] = stack.enter_context(patch.object(
                    service.shipping_evidence, name, new_callable=AsyncMock,
                    side_effect=AssertionError("UNEXPECTED_EVIDENCE:" + name),
                ))
            yield observers
            for name, observer in observers.items():
                with self.subTest(dependency=name):
                    observer.assert_not_called()
                    if isinstance(observer, AsyncMock):
                        observer.assert_not_awaited()

    async def expect_closed(self, name, **changes):
        db = ForbiddenAccess()
        kwargs = dict(owner="SYN-OWNER", evidence_id="SYN-ORDER")
        if name == "post_contract_courier":
            kwargs["actor"] = ForbiddenAccess()
        kwargs.update(changes)
        with self.no_dependencies(include_prepare=name == "post_contract_courier"):
            with self.assertRaises(HTTPException) as denied:
                await getattr(service, name)(db, **kwargs)
            self.assertEqual(denied.exception.status_code, 423)
            self.assertEqual(
                denied.exception.detail["code"], "shipping_contract_native_path_locked",
            )
        self.assertEqual(db.accesses, [])
        actor = kwargs.get("actor")
        if isinstance(actor, ForbiddenAccess):
            self.assertEqual(actor.accesses, [])

    async def test_prepare_rejects_423_before_order_courier_mongo_evidence_or_audit(self):
        await self.expect_closed("prepare_contract_courier")

    async def test_post_rejects_423_before_actor_prepare_mongo_evidence_or_audit(self):
        await self.expect_closed("post_contract_courier")

    async def test_prepare_gate_precedes_all_input_or_missing_cod_validation(self):
        await self.expect_closed(
            "prepare_contract_courier", owner=None, evidence_id=None,
            cod_source_entry_id=ForbiddenAccess(),
            cod_handover_evidence_ref=ForbiddenAccess(),
        )

    async def test_post_gate_precedes_all_input_or_actor_validation(self):
        await self.expect_closed(
            "post_contract_courier", owner=None, actor=None, evidence_id=None,
            cod_source_entry_id=ForbiddenAccess(),
            cod_handover_evidence_ref=ForbiddenAccess(),
        )

    async def test_roles_permissions_and_payload_flags_cannot_open_the_gate(self):
        for role in ("owner", "manager", "reviewer", "poster"):
            with self.subTest(role=role):
                await self.expect_closed("post_contract_courier", actor={
                    "id": "SYN-ACTOR", "role": role,
                    "accounting_permissions": list(ACCOUNTING_PERMISSION_KEYS),
                    "native_contract_enabled": True, "p02_shipping_cod_enabled": True,
                    "is_owner": True,
                })

    async def test_environment_flags_do_not_enable_prepare_or_post(self):
        with patch.dict(os.environ, {
            "MZ2_SHIPPING_CONTRACTS_ENABLED": "true",
            "MZ2_P02_SHIPPING_COD_ENABLED": "true",
            "P02_ENABLED": "1",
        }):
            await self.expect_closed("prepare_contract_courier")
            await self.expect_closed("post_contract_courier")

    async def test_draft_and_approval_remain_closed_before_any_access(self):
        for name in ("save_contract_draft", "approve_contract"):
            db, actor, payload = ForbiddenAccess(), ForbiddenAccess(), ForbiddenAccess()
            with self.subTest(entrypoint=name), self.no_dependencies():
                with self.assertRaises(HTTPException) as denied:
                    await getattr(service, name)(
                        db, owner="SYN-OWNER", actor=actor, payload=payload,
                    )
                self.assertEqual(denied.exception.status_code, 423)
            self.assertEqual(db.accesses, [])
            self.assertEqual(actor.accesses, [])
            self.assertEqual(payload.accesses, [])

    async def test_installer_registers_no_endpoints_or_dependency_calls(self):
        router, db = APIRouter(), ForbiddenAccess()
        before = tuple(router.routes)
        with self.no_dependencies():
            result = service.install_shipping_contract_routes(router, db, ForbiddenAccess())
        self.assertEqual(tuple(router.routes), before)
        self.assertFalse(result["registered"])
        self.assertFalse(result["enabled"])
        self.assertEqual(db.accesses, [])

    async def test_first_cod_receivable_guard_remains_below_the_closed_boundary(self):
        # The outer entrypoints above always return 423 without inspecting COD.
        # Directly specify the retained inner business guard, without opening
        # the runtime gate or touching Mongo or an evidence service.
        db = ForbiddenAccess()
        with patch.object(service, "read_mz2_ledger", new_callable=AsyncMock) as ledger:
            with self.assertRaisesRegex(
                service.ShippingAccountingError, "^cod_base_receivable_required$",
            ):
                await service._cod_source(
                    db, owner="SYN-OWNER", evidence={"recognized_provider": "cod"},
                    courier_id="smsa", gross=Decimal("1000.00"),
                    accounting_at="2026-09-21T10:00:00+00:00",
                )
            ledger.assert_not_called()
        self.assertEqual(db.accesses, [])


class DecimalWithoutFloat(Decimal):
    def __float__(self):
        raise AssertionError("DECIMAL_TO_FLOAT_IS_FORBIDDEN")


class ShippingContractDecimalLegTests(unittest.TestCase):
    """In-memory Decimal validation only; not downstream ledger acceptance."""

    def leg(self, value):
        return service._leg("expense", "shipping", "debit", value, "shipping_net")

    def test_exact_halala_amounts_are_preserved_as_two_place_strings(self):
        for value in ("0.01", "1.57", "2.25", "10.43", "12.00", "17.25"):
            with self.subTest(value=value):
                row = self.leg(Decimal(value))
                self.assertEqual(row["amount"], value)
                self.assertIsInstance(row["amount"], str)
                self.assertEqual(Decimal(row["amount"]), Decimal(value))

    def test_integer_and_redundant_trailing_zeroes_are_exact_not_subhalalas(self):
        for value, expected in (("15", "15.00"), ("17.2500", "17.25"), ("0.01000", "0.01")):
            with self.subTest(value=value):
                self.assertEqual(self.leg(Decimal(value))["amount"], expected)

    def test_large_exact_values_are_not_reduced_to_binary_precision(self):
        for value in (
            "10000000000000000.01",
            "12345678901234567890123456789012345678901234567890.99",
            "99999999999999999999999999999999999999999999999999.99",
        ):
            with self.subTest(value=value):
                self.assertEqual(self.leg(Decimal(value))["amount"], value)

    def test_large_positive_decimal_exponent_has_exact_fixed_point_output(self):
        self.assertEqual(self.leg(Decimal("1E+60"))["amount"], "1" + "0" * 60 + ".00")

    def test_subhalalas_below_at_and_above_rounding_midpoints_are_rejected(self):
        for value in (
            "0.001", "0.004999", "0.005", "0.005001", "0.009999",
            "0.014999", "0.015", "0.015001",
            "17.249999", "17.250001", "17.254999", "17.255", "17.255001",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                service.ShippingAccountingError, "^shipping_amount_requires_exact_halalas$",
            ):
                self.leg(Decimal(value))

    def test_large_subhalala_is_not_silently_lost(self):
        for value in (
            "10000000000000000.005",
            "12345678901234567890123456789012345678901234567890.995",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                service.ShippingAccountingError, "^shipping_amount_requires_exact_halalas$",
            ):
                self.leg(Decimal(value))

    def test_values_already_rounded_by_decimal_policy_are_accepted_without_rerounding(self):
        for raw, expected in (("17.2449", "17.24"), ("17.245", "17.25"), ("17.255", "17.26")):
            value = Decimal(raw).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            with self.subTest(raw=raw):
                self.assertEqual(self.leg(value)["amount"], expected)

    def test_zero_negative_and_nonfinite_values_are_rejected(self):
        for value in ("0", "-0.00", "-0.01", "NaN", "sNaN", "Infinity", "-Infinity"):
            with self.subTest(value=value), self.assertRaisesRegex(
                service.ShippingAccountingError, "^shipping_leg_amount_invalid$",
            ):
                self.leg(Decimal(value))

    def test_non_decimal_inputs_are_not_coerced(self):
        for value in (17.25, 17, "17.25", None, True):
            with self.subTest(value=value), self.assertRaisesRegex(
                service.ShippingAccountingError, "^shipping_leg_amount_invalid$",
            ):
                self.leg(value)

    def test_binary_conversion_is_never_attempted(self):
        value = DecimalWithoutFloat("10000000000000000.01")
        self.assertEqual(self.leg(value)["amount"], "10000000000000000.01")

    def test_caller_precision_rounding_and_traps_do_not_change_exact_results(self):
        values = [Decimal("17.2500"), Decimal("123456789012345678901234567890.99")]
        with localcontext() as context:
            context.prec = 3
            context.rounding = ROUND_DOWN
            context.traps[Inexact] = True
            context.traps[Rounded] = True
            before_flags, before_traps = dict(context.flags), dict(context.traps)
            self.assertEqual(self.leg(values[0])["amount"], "17.25")
            self.assertEqual(self.leg(values[1])["amount"], "123456789012345678901234567890.99")
            self.assertEqual(context.prec, 3)
            self.assertEqual(context.rounding, ROUND_DOWN)
            self.assertEqual(dict(context.flags), before_flags)
            self.assertEqual(dict(context.traps), before_traps)
        self.assertEqual(values[0].as_tuple().exponent, -4)


if __name__ == "__main__":
    unittest.main()
