"""Fee evidence must not demand a fabricated merchant order."""
import ast
from pathlib import Path
import pytest
source = Path(__file__).parents[1] / "accounting_settlement_routes.py"
node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == "_order_matching_entries")
namespace = {}
exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
filter_entries = namespace["_order_matching_entries"]

@pytest.mark.parametrize("event_type", ["sale", "refund", "partial_refund", "unknown"])
def test_fee_evidence_is_excluded_but_unmatched_financial_events_remain(event_type):
    transaction = {"event_type": event_type, "order_number": "unmatched-order"}
    fee = {"event_type": "settlement_fee", "order_number": "payout-fee:synthetic"}
    entries = [transaction, fee]
    assert filter_entries(entries) == [transaction]
    assert entries == [transaction, fee]
