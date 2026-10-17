"""Offline arithmetic/scope check for design fixtures, NOT application tests.

No application imports, DB, network, configuration changes, or posting.
Run with Python 3: python -B docs/.../parallel/shipping/verify-prep.py
"""
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import subprocess


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
PREFIX = "docs/operations/MZ2-FIN-CUTOVER-001/parallel/shipping/"
ALLOWED = {"README.md", "INVENTORY.md", "DESIGN.md", "INTEGRATION-CONTRACTS.md",
           "ACCEPTANCE.md", "synthetic-cases.json", "verify-prep.py", "test-prep.py"}
ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def money(value):
    assert isinstance(value, str), "Amounts must be explicit decimal strings"
    result = Decimal(value)
    assert result.is_finite() and result == result.quantize(CENT)
    return result


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def matching_tiers(amount, tiers):
    return [r for r in tiers
            if (amount >= money(r["min"]) if r["min_inclusive"] else amount > money(r["min"]))
            and (amount <= money(r["max"]) if r["max_inclusive"] else amount < money(r["max"]))]


def boundaries_overlap(inputs):
    left, right = Decimal(str(inputs["left_max"])), Decimal(str(inputs["right_min"]))
    assert left.is_finite() and right.is_finite()
    assert type(inputs["left_inclusive"]) is bool and type(inputs["right_inclusive"]) is bool
    return left > right or (left == right and inputs["left_inclusive"] and inputs["right_inclusive"])


def journal_by_key(case, key):
    matches = [j for j in case["expected_journals"] if j["event_key"] == key]
    assert len(matches) == 1, "journal_reference"
    return matches[0]


def legs(journal, side):
    return {k: money(v) for k, v in journal[side].items()}


def validate_settlement(case):
    """Only the explicitly marked net-inflow/offset design contract, not payments."""
    fields = {"bank_received", "gross_cod_cleared", "fee_offset"}
    if not fields.intersection(case):
        return
    assert fields.issubset(case), "settlement_amounts_required"
    kind = case.get("settlement_type")
    assert kind in {"net_inflow", "explicit_offset"}, "settlement_type_required"
    party = case.get("counterparty_id")
    assert party and case.get("statement_ref"), "settlement_identity_required"
    journal = journal_by_key(case, case["settlement_event_key"])
    dr, cr = legs(journal, "debits"), legs(journal, "credits")
    bank, gross, offset = (money(case[k]) for k in ("bank_received", "gross_cod_cleared", "fee_offset"))
    assert bank >= 0 and gross > 0 and offset > 0, "settlement_amount_sign"
    bank_accounts = {k for k in dr.keys() | cr.keys() if k.startswith("bank:")}
    bank_effect = sum((dr.get(k, ZERO) - cr.get(k, ZERO) for k in bank_accounts), ZERO)
    assert bank == bank_effect, "settlement_bank_legs"
    cod, payable = "COD:" + party, "payable:" + party
    assert cr.get(cod, ZERO) - dr.get(cod, ZERO) == gross, "settlement_cod_legs"
    assert dr.get(payable, ZERO) - cr.get(payable, ZERO) == offset, "settlement_payable_legs"
    assert gross == bank + offset, "settlement_equation"
    assert gross <= money(case["initial_balances"].get(cod, "0.00")), "settlement_cod_available"
    assert offset <= -money(case["initial_balances"].get(payable, "0.00")), "settlement_payable_available"
    expected_dr = {payable: offset}
    if kind == "explicit_offset":
        assert bank == ZERO, "zero_offset_amount"
        assert not case.get("bank_receipt_id") and not case.get("bank_reference"), "zero_offset_bank_evidence"
        assert not bank_accounts, "zero_offset_bank_legs"
    else:
        assert bank > 0 and case.get("bank_account_id") and case.get("bank_reference"), "net_inflow_evidence"
        expected_dr["bank:" + case["bank_account_id"]] = bank
    assert dr == expected_dr and cr == {cod: gross}, "settlement_party_or_extra_legs"


def validate_transport(case):
    """Shipping-side cost-origin contract; P03 entries are external expectations."""
    if "cost_origin_key" not in case:
        return
    origin = case["cost_origin_key"]
    assert origin.startswith(case["owner"] + ":"), "transport_origin_owner"
    allocations = case["purpose_allocations"]
    assert set(allocations) == {"inbound_acquisition", "outbound_customer_delivery"}, "transport_purposes"
    inbound, outbound = (money(allocations[k]) for k in ("inbound_acquisition", "outbound_customer_delivery"))
    net, vat = money(case["source_net_cost"]), money(case["source_transport_vat"])
    assert inbound >= 0 and outbound >= 0 and inbound + outbound == net, "transport_allocation_total"
    source = journal_by_key(case, case["source_charge_event_key"])
    assert source.get("cost_origin_key") == origin, "transport_origin_link"
    assert source.get("contract_role") == "shipping_cost_origin", "transport_source_role"
    assert legs(source, "debits") == {"acquisition:clearing": inbound, "expense:delivery": outbound, "VAT:shipping": vat}, "transport_source_allocation_legs"
    assert legs(source, "credits") == {"payable:" + case["counterparty_id"]: net + vat}, "transport_source_payable"
    consumers = [j for j in case["expected_journals"] if j is not source]
    consumed = ZERO
    for journal in consumers:
        assert journal.get("cost_origin_key") == origin, "transport_origin_link"
        assert journal.get("contract_role") == "external_consumer_expectation" and journal.get("consumer") == "P03", "transport_external_consumer_role"
        dr, cr = legs(journal, "debits"), legs(journal, "credits")
        assert set(dr) == {"inventory:value"} and set(cr) == {"acquisition:clearing"}, "transport_consumer_forbidden_legs"
        assert dr["inventory:value"] == cr["acquisition:clearing"], "transport_consumer_balance"
        consumed += dr["inventory:value"]
    assert consumed <= inbound, "transport_consumer_cap"
    # The exact source leg shape and consumer restriction prove VAT/payable once.


def validate_data(data):
    """Pure validation of fixture facts; no file, Git, application or DB access."""
    assert data["production_configuration"] is False
    assert "NOT_APPLICATION_ACCEPTANCE" in data["classification"]
    cases = data["cases"]
    assert len({c["id"] for c in cases}) == len(cases) == 33
    journals = 0
    for case in cases:
        assert case["application_test_status"] == "PLANNED_NOT_RUN"
        assert case["operation_id"] == "MZ2-FIN-CUTOVER-001"
        validate_settlement(case)
        validate_transport(case)
        if case["id"] == "S10":
            assert case["inputs"] and all(not matching_tiers(money(v), data["tiers"]) for v in case["inputs"]), "tier_gap_covered"
            assert case["expected_result"] == "needs_review" and case["calculated_fee"] is None, "tier_gap_expectation"
        if case["id"] == "S11":
            result_name = "validation_rejected" if boundaries_overlap(case["inputs"]) else "validation_accepted"
            assert case["expected_result"] == result_name, "tier_overlap_expectation"
        if "expected_fee" in case:
            rules = matching_tiers(money(case["cod_amount"]), data["tiers"])
            assert len(rules) == 1, "tier_unique_match"
            rule = rules[0]
            fee = (money(case["cod_amount"]) * Decimal(rule["percent"]) + money(rule["fixed"])).quantize(CENT, rounding=ROUND_HALF_UP)
            vat = (fee * Decimal(rule["vat_percent"]) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
            assert fee == money(case["expected_fee"]) and vat == money(case["expected_fee_vat"]), "tier_calculated_amounts"
            journal = journal_by_key(case, case["fee_event_key"])
            assert legs(journal, "debits") == {"expense:COD_fee": fee, "VAT:COD": vat}, "tier_fee_tax_legs"
            assert legs(journal, "credits") == {"payable:" + case["counterparty_id"]: fee + vat}, "tier_payable_legs"
        result = {k: money(v) for k, v in case["initial_balances"].items()}
        events = set()
        for journal in case["expected_journals"]:
            assert journal["event_key"] not in events
            events.add(journal["event_key"])
            assert journal["accounting_at"].endswith("+03:00")
            dr = {k: money(v) for k, v in journal["debits"].items()}
            cr = {k: money(v) for k, v in journal["credits"].items()}
            assert dr and cr and all(v > 0 for v in [*dr.values(), *cr.values()])
            assert sum(dr.values(), ZERO) == sum(cr.values(), ZERO), case["id"]
            accounts = set(dr) | set(cr)
            assert not any(k.startswith("revenue:") or k == "VAT:sales" for k in accounts)
            for flag, prefix in (("no_bank_legs", "bank:"),
                                 ("no_expense_legs", "expense:"),
                                 ("no_tax_legs", "VAT:")):
                if case.get(flag):
                    assert not any(k.startswith(prefix) for k in accounts), case["id"]
            for key, value in dr.items():
                result[key] = result.get(key, ZERO) + value
            for key, value in cr.items():
                result[key] = result.get(key, ZERO) - value
            journals += 1
        expected = {k: money(v) for k, v in case["expected_balances"].items()}
        assert result == expected, (case["id"], result, expected)
        for entity, view in case.get("expected_views", {}).items():
            ours = expected.get("COD:" + entity, ZERO)
            theirs = -expected.get("payable:" + entity, ZERO)
            assert ours == money(view["ours"]) and theirs == money(view["theirs"])
            assert ours - theirs == money(view["net"])
    return journals


def main():
    data = json.loads((HERE / "synthetic-cases.json").read_text(encoding="utf-8"))
    journals = validate_data(data)
    # Inspect all tracked deltas against the pinned production base, plus untracked files.
    changes = git("diff", "--name-status", data["base_sha"], "--").splitlines()
    for change in changes:
        status, path = change.split("\t", 1)
        assert status == "A", ("Not an addition", change)
        assert path.startswith(PREFIX) and path[len(PREFIX):] in ALLOWED, change
    for path in git("ls-files", "--others", "--exclude-standard").splitlines():
        assert path.startswith(PREFIX) and path[len(PREFIX):] in ALLOWED, path
    assert git("merge-base", data["base_sha"], "HEAD") == data["base_sha"]
    assert {p.name for p in HERE.iterdir() if p.is_file()} == ALLOWED
    for path in HERE.glob("*.md"):
        assert path.read_text(encoding="utf-8").strip(), path
    print(f"PASS arithmetic: {len(data['cases'])} synthetic design cases; {journals} balanced expected journals.")
    print("PASS contracts: 3 net/offset settlements; 1 transport origin with external P03 expectation; 5 fee/VAT journal bindings; 2 gap amounts; 1 overlapping boundary; additions-only scope.")
    print("NOT RUN: application/API/Mongo/browser acceptance, permissions, closure, pause/replay, legacy outage integration.")


if __name__ == "__main__":
    main()
