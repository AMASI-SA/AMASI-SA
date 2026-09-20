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
           "ACCEPTANCE.md", "synthetic-cases.json", "verify-prep.py"}
ZERO = Decimal("0.00")
CENT = Decimal("0.01")


def money(value):
    assert isinstance(value, str), "Amounts must be explicit decimal strings"
    result = Decimal(value)
    assert result.is_finite() and result == result.quantize(CENT)
    return result


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main():
    data = json.loads((HERE / "synthetic-cases.json").read_text(encoding="utf-8"))
    assert data["production_configuration"] is False
    assert "NOT_APPLICATION_ACCEPTANCE" in data["classification"]
    cases = data["cases"]
    assert len({c["id"] for c in cases}) == len(cases) == 33
    journals = 0
    for case in cases:
        assert case["application_test_status"] == "PLANNED_NOT_RUN"
        assert case["operation_id"] == "MZ2-FIN-CUTOVER-001"
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
        if "expected_fee" in case:
            amount = money(case["cod_amount"])
            rules = [r for r in data["tiers"]
                     if (amount >= money(r["min"]) if r["min_inclusive"] else amount > money(r["min"]))
                     and (amount <= money(r["max"]) if r["max_inclusive"] else amount < money(r["max"]))]
            assert len(rules) == 1
            rule = rules[0]
            fee = (amount * Decimal(rule["percent"]) + money(rule["fixed"])).quantize(CENT, rounding=ROUND_HALF_UP)
            vat = (fee * Decimal(rule["vat_percent"]) / 100).quantize(CENT, rounding=ROUND_HALF_UP)
            assert fee == money(case["expected_fee"]) and vat == money(case["expected_fee_vat"])
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
    print(f"PASS: {len(cases)} synthetic design cases; {journals} balanced expected journals; decimal outcomes/tier examples; additions-only scope.")
    print("NOT RUN: application/API/Mongo/browser acceptance, permissions, closure, pause/replay, legacy outage integration.")


if __name__ == "__main__":
    main()
