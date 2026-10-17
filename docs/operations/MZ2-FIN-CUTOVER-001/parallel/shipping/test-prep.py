"""Mutation regressions for the offline design validator; no application tests.

Every negative copy is made in memory. The checked-in fixtures are not changed.
Only an AssertionError with the expected contract code counts as rejection.
"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("shipping_prep", HERE / "verify-prep.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


def edit(data, case_id, path, value):
    target = next(c for c in data["cases"] if c["id"] == case_id)
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


# label, exact expected rejection, case, field path, contradictory value
NEGATIVES = [
    ("S14_received_900", "settlement_bank_legs", "S14", ["bank_received"], "900.00"),
    ("S14_gross_999", "settlement_cod_legs", "S14", ["gross_cod_cleared"], "999.00"),
    ("S14_offset_19", "settlement_payable_legs", "S14", ["fee_offset"], "19.00"),
    ("S14_wrong_party", "settlement_cod_legs", "S14", ["counterparty_id"], "D2"),
    ("S14_missing_statement", "settlement_identity_required", "S14", ["statement_ref"], ""),
    ("S14_wrong_journal", "journal_reference", "S14", ["settlement_event_key"], "unrelated"),
    ("S15_fake_receipt", "zero_offset_bank_evidence", "S15", ["bank_receipt_id"], "FAKE-RECEIPT"),
    ("S15_fake_reference", "zero_offset_bank_evidence", "S15", ["bank_reference"], "FAKE-BANK"),
    ("S15_nonzero_received", "settlement_bank_legs", "S15", ["bank_received"], "1.00"),
    ("S15_bank_leg", "settlement_bank_legs", "S15", ["expected_journals", 0, "debits", "bank:B1"], "1.00"),
    ("S30_inbound_200", "transport_allocation_total", "S30", ["purpose_allocations", "inbound_acquisition"], "200.00"),
    ("S30_wrong_origin", "transport_origin_link", "S30", ["expected_journals", 0, "cost_origin_key"], "another-origin"),
    ("S30_net_31", "transport_allocation_total", "S30", ["source_net_cost"], "31.00"),
    ("S30_vat_5", "transport_source_allocation_legs", "S30", ["source_transport_vat"], "5.00"),
    ("S30_repeat_payable", "transport_consumer_forbidden_legs", "S30", ["expected_journals", 1, "credits", "payable:C1"], "20.00"),
    ("S30_repeat_tax", "transport_consumer_forbidden_legs", "S30", ["expected_journals", 1, "debits", "VAT:shipping"], "4.50"),
    ("S30_consumer_bank", "transport_consumer_forbidden_legs", "S30", ["expected_journals", 1, "credits", "bank:B1"], "20.00"),
    ("S30_consumer_owned_by_shipping", "transport_external_consumer_role", "S30", ["expected_journals", 1, "consumer"], "shipping"),
    ("S10_covered_1000", "tier_gap_covered", "S10", ["inputs"], ["1000.00"]),
    ("S10_covered_3000", "tier_gap_covered", "S10", ["inputs"], ["3000.00"]),
    ("S11_nonoverlap_rejected", "tier_overlap_expectation", "S11", ["inputs", "right_inclusive"], False),
    ("S11_separated_rejected", "tier_overlap_expectation", "S11", ["inputs", "right_min"], 1001),
    ("S11_overlap_accepted", "tier_overlap_expectation", "S11", ["expected_result"], "validation_accepted"),
]


def reject(data, label, code):
    try:
        validator.validate_data(data)
    except AssertionError as exc:
        assert str(exc) == code, (label, "wrong failure", str(exc), code)
        print(f"REJECT {label}: {code}")
    else:
        raise AssertionError(f"Accepted contradictory copy: {label}")


def main():
    original = json.loads((HERE / "synthetic-cases.json").read_text(encoding="utf-8"))
    assert validator.validate_data(original) == 30
    print("PASS original: 33 cases, 30 balanced expected journals")
    for label, code, case_id, path, value in NEGATIVES:
        data = deepcopy(original)
        edit(data, case_id, path, value)
        reject(data, label, code)

    # Balanced amount changes: rejects semantic inconsistencies even if generic
    # balance/ending-balance checks could otherwise accept the modified copy.
    data = deepcopy(original)
    edit(data, "S30", ["purpose_allocations"], {"inbound_acquisition": "21.00", "outbound_customer_delivery": "9.00"})
    reject(data, "S30_allocation_sum30_wrong_split", "transport_source_allocation_legs")
    data = deepcopy(original)
    for side, account in (("debits", "inventory:value"), ("credits", "acquisition:clearing")):
        edit(data, "S30", ["expected_journals", 1, side, account], "21.00")
    edit(data, "S30", ["expected_balances", "inventory:value"], "21.00")
    edit(data, "S30", ["expected_balances", "acquisition:clearing"], "-1.00")
    reject(data, "S30_balanced_external_overconsumption", "transport_consumer_cap")
    data = deepcopy(original)
    # Swap one SAR from VAT to fee while preserving debit total and balances.
    for account, value in (("expense:COD_fee", "21.00"), ("VAT:COD", "2.00")):
        edit(data, "S05", ["expected_journals", 0, "debits", account], value)
        edit(data, "S05", ["expected_balances", account], value)
    reject(data, "S05_balanced_fee_tax_swap", "tier_fee_tax_legs")
    data = deepcopy(original)
    edit(data, "S05", ["expected_journals", 0, "credits"], {"payable:C2": "23.00"})
    edit(data, "S05", ["expected_balances"], {"expense:COD_fee": "20.00", "VAT:COD": "3.00", "payable:C2": "-23.00"})
    reject(data, "S05_wrong_fee_counterparty", "tier_payable_legs")

    # Positive controls demonstrate that correct boundary choices are accepted.
    for label, updates in (
        ("right_exclusive", {"right_inclusive": False}),
        ("left_exclusive", {"left_inclusive": False}),
        ("separate_bounds", {"right_min": 1001}),
    ):
        data = deepcopy(original)
        for key, value in updates.items():
            edit(data, "S11", ["inputs", key], value)
        edit(data, "S11", ["expected_result"], "validation_accepted")
        assert validator.validate_data(data) == 30
        print(f"PASS valid boundary: {label}")

    # P03 is an external consumer expectation; partial approved consumption is valid.
    data = deepcopy(original)
    for side, account in (("debits", "inventory:value"), ("credits", "acquisition:clearing")):
        edit(data, "S30", ["expected_journals", 1, side, account], "10.00")
    edit(data, "S30", ["expected_balances", "inventory:value"], "10.00")
    edit(data, "S30", ["expected_balances", "acquisition:clearing"], "10.00")
    assert validator.validate_data(data) == 30
    print("PASS external P03 partial consumption: 10 of 20")

    # A separate bank expense/payment must not enter S14's net-inflow equation.
    data = deepcopy(original)
    case = next(c for c in data["cases"] if c["id"] == "S14")
    case["initial_balances"]["payable:other"] = "-5.00"
    case["expected_journals"].append({"event_key": "separate-fee-payment", "accounting_at": "2030-01-15T12:00:00+03:00",
        "debits": {"payable:other": "5.00"}, "credits": {"bank:B1": "5.00"}})
    case["expected_balances"].update({"payable:other": "0.00", "bank:B1": "975.00"})
    assert validator.validate_data(data) == 31
    print("PASS settlement journal selection: independent payment excluded")
    print(f"PASS mutation suite: {len(NEGATIVES) + 4} negative copies rejected with exact codes; original + 5 valid controls accepted.")
    print("NOT APPLICATION ACCEPTANCE: no HTTP/Mongo/UI, authorization, deduplication, closure or operational replay tests.")


if __name__ == "__main__":
    main()
