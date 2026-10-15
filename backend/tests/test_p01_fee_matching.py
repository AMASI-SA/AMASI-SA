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


def test_saved_draft_projection_removes_only_fee_without_mutating_snapshot():
    node = next(n for n in ast.parse(source.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == "_draft_matching_view")
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), namespace)
    snapshot = {"unmatched": 1, "unmatched_entries": [{"event_type": "sale"}, {"event_type": "settlement_fee"}]}
    draft = {"status": "needs_review", "source_snapshot": snapshot}
    result = namespace["_draft_matching_view"](draft)
    assert result["source_snapshot"]["unmatched_entries"] == [{"event_type": "sale"}]
    assert result["source_snapshot"]["unmatched"] == 1
    assert len(snapshot["unmatched_entries"]) == 2
